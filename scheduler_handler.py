from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from reminder_handler import poll_to_group, send_admin_report
from utils import LOCAL_TZ, now_local,format_now
from datetime import datetime, timedelta
from group_config import GROUPS, GROUP_NAME_MAP
from report_rows import canonical_report_rows
from subscription_tools import (
    load_all_subscriptions,
    get_subscription_alert_status,
    is_started_subscription,
    parse_unpaid_payment,
)
from subscription_alerts import collect_alerts, render_digest_parts
import asyncio
import os
import logging

# ------------------------------------------------------------------------------------
groups = GROUPS
in_process_sent_occurrences = set()

ADMIN_ID = int(os.getenv("ADMIN_ID"))
KARINA_ID = int(os.getenv("KARINA_ID", ADMIN_ID))

CHECK_HOUR_DAY = int(os.getenv("CHECK_HOUR_DAY", 11))
CHECK_HOUR_EVENING = int(os.getenv("CHECK_HOUR_EVENING", 18))
CHECK_MIN_START = int(os.getenv("CHECK_MIN_1START", 1))
CHECK_MIN_END = int(os.getenv("CHECK_MIN_END", 5))
EXPIRY_HOUR_DAY = int(os.getenv("EXPIRY_HOUR_DAY", 12))
EXPIRY_HOUR_EVENING = int(os.getenv("EXPIRY_HOUR_EVENING", 19))
REPORT_HOUR_DAY = int(os.getenv("REPORT_HOUR_DAY", 15))
REPORT_HOUR_MORNING = int(os.getenv("REPORT_HOUR_MORNING", 8))

# ------------------------------------------------------------------------------------
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # переменная должна быть в Render Environment

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()

# pending = {}

# ------------------------------------------------------------------------------------
def get_decision_keyboard(group_id, lesson_date=None):
    suffix = f"|{lesson_date}" if lesson_date else ""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data=f"yes|{group_id}{suffix}")],
        [InlineKeyboardButton("❌ Нет, я сам напишу в группу", callback_data=f"skip|{group_id}{suffix}")],
    ])


# ------------------------------------------------------------------------------------

async def ask_admin(app, group_id, group):
    lesson_date = get_lesson_date(now_local(), group).isoformat()
    if occurrence_was_sent(group, lesson_date):
        logging.info("[scheduler] Напоминание уже отправлено: %s/%s", group["name"], lesson_date)
        return False
    msg = await app.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Занятие для {group['display_name']} {group['lesson_day_text']} в {group['time']} по расписанию?",
        reply_markup=get_decision_keyboard(group_id, lesson_date)
    )
    return True
   # pending[msg.message_id] = group

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in (ADMIN_ID, KARINA_ID):
        await query.edit_message_text("⛔ Эта команда доступна только администратору.")
        return
    data = query.data.split("|")
    action = data[0]
    if action == "cancel_reminder":
        await query.edit_message_text("Отменено.")
        return
    group_id = int(data[1])
    group = groups[group_id]
    if action in ("yes", "select_reminder", "resend_reminder") and len(data) < 3:
        await query.edit_message_text("Эта кнопка устарела. Используйте /send_reminder.")
        return
    lesson_date = data[2] if len(data) > 2 else None

    if action in ("yes", "select_reminder", "resend_reminder"):
        occurrence_status = validate_callback_occurrence(now_local(), group, lesson_date)
        if occurrence_status == "started":
            await query.edit_message_text(
                "Это занятие уже началось или прошло. "
                "Используйте /send_reminder для актуальных занятий."
            )
            return
        if occurrence_status != "valid":
            await query.edit_message_text(
                "Эта кнопка содержит некорректное занятие. Используйте /send_reminder."
            )
            return

    if action in ("yes", "select_reminder") and occurrence_was_sent(group, lesson_date):
        await query.edit_message_text(
            "⚠️ Напоминание и опрос для этого занятия уже отправлены. "
            "Повторная отправка создаст ещё одно сообщение и опрос.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Отправить заново", callback_data=f"resend_reminder|{group_id}|{lesson_date}")],
                [InlineKeyboardButton("Отмена", callback_data="cancel_reminder")],
            ]),
        )
        return

    if action in ("yes", "select_reminder", "resend_reminder"):
        result = await send_reminder_and_poll(
            context, group, lesson_date, replace=action == "resend_reminder"
        )
        await query.edit_message_text(result.admin_message)
    elif action == "skip":
        await query.edit_message_text("❌ Окей, ничего не публикуем.\nНапоминание: не забудьте сами сообщить группе о деталях отмены")


def get_lesson_date(now, group):
    return now.date() + timedelta(days=group.get("check_day_offset", 0))


def validate_callback_occurrence(now, group, lesson_date):
    """Validate a callback's explicit scheduled occurrence in Vietnam time."""
    try:
        parsed_date = datetime.strptime(lesson_date, "%Y-%m-%d").date()
        if parsed_date.strftime("%A") not in group["days"]:
            return "invalid"
        hour, minute = map(int, group["time"].split(":"))
        lesson_datetime = LOCAL_TZ.localize(datetime(
            parsed_date.year, parsed_date.month, parsed_date.day, hour, minute
        ))
        if now.tzinfo is None:
            now = LOCAL_TZ.localize(now)
    except (TypeError, ValueError, KeyError):
        return "invalid"
    return "started" if lesson_datetime <= now else "valid"


def _report_rows():
    response = sheets_service.values().get(
        spreadsheetId=SPREADSHEET_ID, range="Репорты!A2:G"
    ).execute()
    return response.get("values", [])


def occurrence_was_sent(group, lesson_date):
    occurrence = (group["name"], str(lesson_date))
    if occurrence in in_process_sent_occurrences:
        return True
    return any(
        len(row) >= 7 and row[1] == group["name"] and str(row[6])[:10] == str(lesson_date)
        for row in _report_rows()
    )


class DeliveryResult:
    def __init__(self, state, admin_message):
        self.state = state
        self.admin_message = admin_message


async def send_reminder_and_poll(context, group, lesson_date, replace=False):
    """The single delivery path used by scheduled and manual confirmations."""
    occurrence = (group["name"], str(lesson_date))
    try:
        if group.get("thread_id") is not None:
            await context.bot.send_message(
                chat_id=group["group_id"],
                message_thread_id=group["thread_id"],
                text=f"Доброго дня! Занятие для {group['display_name']} по расписанию в {group['time']} 🤸🏻🤸🏻‍♀️"
            )
        else:
            await context.bot.send_message(
                chat_id=group["group_id"],
                text=f"Доброго дня! Тренировка для {group['display_name']} по расписанию {group['lesson_day_text']} в {group['time']} 🤸🏻🤸🏻‍♀️"
            )
        logging.info("✅ Объявление отправлено: %s/%s", *occurrence)
    except Exception as e:
        logging.warning("❗ Не удалось отправить объявление %s/%s: %s", *occurrence, e)
        return DeliveryResult("announcement_failed", "❌ Не удалось отправить напоминание. Опрос не отправлялся.")

    try:
        if group.get("thread_id") is not None:
            poll_msg = await context.bot.send_poll(
                chat_id=group["group_id"],
                question="Кто будет сегодня на занятии?",
                options=["✅ Будем по абонементу", "🤸🏻‍♀️ Будем разово", "❌ Пропускаем"],
                is_anonymous=False,
                allows_multiple_answers=False,
                message_thread_id=group["thread_id"],
            )
        else:
            poll_msg = await context.bot.send_poll(
                chat_id=group["group_id"],
                question="Кто будет завтра на тренировке?",
                options=["✅ Будем по абонементу", "🤸🏻‍♀️ Будем разово", "❌ Пропускаем"],
                is_anonymous=False,
                allows_multiple_answers=False,
            )

    except Exception as e:
        logging.warning("❗ Объявление отправлено, но опрос %s/%s не отправлен: %s", *occurrence, e)
        in_process_sent_occurrences.add(occurrence)
        return DeliveryResult(
            "poll_failed",
            "⚠️ Объявление отправлено, но опрос отправить не удалось. "
            "Не повторяйте отправку вслепую: объявление уже появилось в группе.",
        )

    logging.info("✅ Опрос отправлен: %s/%s poll_id=%s", *occurrence, poll_msg.poll.id)
    options_text = "|".join(opt.text for opt in poll_msg.poll.options)
    survey_row = [[
        poll_msg.poll.id, group["name"], "", "", format_now(), "", options_text,
    ]]
    survey_persisted = False
    for attempt in range(1, 4):
        try:
            sheets_service.values().append(
                spreadsheetId=SPREADSHEET_ID, range="Опросы!A:G",
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                body={"values": survey_row},
            ).execute()
            survey_persisted = True
            logging.info("✅ Опросы сохранены: %s/%s (попытка %d)", *occurrence, attempt)
            break
        except Exception as e:
            logging.warning(
                "❗ Ошибка сохранения Опросы %s/%s (попытка %d/3): %s",
                *occurrence, attempt, e,
            )

    context.bot_data[poll_msg.poll.id] = poll_msg.poll.options
    poll_to_group[poll_msg.poll.id] = group
    report_row = [[
        poll_msg.poll.id, group["name"], "", "", str(group["group_id"]),
        str(group["thread_id"]) if group.get("thread_id") is not None else "",
        str(lesson_date),
    ]]

    persistence_error = None
    for attempt in range(1, 4):
        try:
            rows = _report_rows()
            matches = [
                i for i, row in enumerate(rows, start=2)
                if len(row) >= 7 and row[1] == group["name"]
                and str(row[6])[:10] == str(lesson_date)
            ]
            if any(str(rows[i - 2][0]) == str(poll_msg.poll.id) for i in matches):
                persistence_error = None
                logging.info("✅ Репорты уже сохранены: %s/%s", *occurrence)
                break
            if replace and matches:
                sheets_service.values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Репорты!A{matches[-1]}:G{matches[-1]}",
                    valueInputOption="USER_ENTERED", body={"values": report_row},
                ).execute()
            else:
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID, range="Репорты!A1",
                    valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                    body={"values": report_row},
                ).execute()
            persistence_error = None
            logging.info("✅ Репорты сохранены: %s/%s (попытка %d)", *occurrence, attempt)
            break
        except Exception as e:
            persistence_error = e
            logging.warning("❗ Ошибка сохранения Репорты %s/%s (попытка %d/3): %s", *occurrence, attempt, e)

    in_process_sent_occurrences.add(occurrence)
    if persistence_error is not None:
        logging.error("❌ Частичная доставка: Telegram отправлен, Репорты не сохранены: %s/%s", *occurrence)
        return DeliveryResult(
            "persistence_failed",
            "⚠️ Напоминание и опрос отправлены в Telegram, но сохранить состояние "
            "для отчёта и защиты от дублей не удалось. Не отправляйте их повторно.",
        )

    if not survey_persisted:
        logging.error(
            "❌ Частичная доставка: Telegram и Репорты сохранены, Опросы не сохранены: %s/%s",
            *occurrence,
        )
        return DeliveryResult(
            "survey_persistence_failed",
            "⚠️ Напоминание и опрос отправлены в Telegram, состояние отчёта и защиты "
            "от дублей сохранено, но данные для восстановления ответов опроса сохранить "
            "не удалось. Не отправляйте сообщения повторно.",
        )

    logging.info("✅ Полная доставка завершена: %s/%s", *occurrence)
    return DeliveryResult("success", "Напоминание и опрос отправлены ✅")


def relevant_upcoming_groups(now):
    result = []
    for idx, group in enumerate(groups):
        lesson_date = get_lesson_date(now, group)
        if lesson_date.strftime("%A") not in group["days"]:
            continue
        hour, minute = map(int, group["time"].split(":"))
        lesson_time = now.replace(year=lesson_date.year, month=lesson_date.month, day=lesson_date.day,
                                  hour=hour, minute=minute, second=0, microsecond=0)
        if lesson_time <= now:
            continue
        result.append((idx, group, lesson_date))
    return result


async def send_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in (ADMIN_ID, KARINA_ID):
        await update.message.reply_text("⛔ Эта команда доступна только администратору.")
        return
    choices = relevant_upcoming_groups(now_local())
    if not choices:
        await update.message.reply_text("ℹ️ Сейчас нет доступных предстоящих напоминаний.")
        return
    keyboard = [[InlineKeyboardButton(
        f"{group['display_name']} — {group['lesson_day_text']} в {group['time']}",
        callback_data=f"select_reminder|{idx}|{lesson_date.isoformat()}"
    )] for idx, group, lesson_date in choices]
    await update.message.reply_text("Выберите занятие:", reply_markup=InlineKeyboardMarkup(keyboard))
# -----------------------------------------------------------------------------
# ------------------------------------------------------------------------------------
async def check_expired_subscriptions(app, today_group_names):
    """Send one logical trainer digest for the groups in this scheduler window."""
    logging.info("🔍 check_expired_subscriptions запущена")
    try:
        all_subscriptions = await asyncio.to_thread(load_all_subscriptions)
        if not all_subscriptions:
            logging.warning("⛔️ Не удалось загрузить абонементы или список пуст.")
            return
        logging.info(f"🔎 Группы, которые проверяются сегодня: {today_group_names}")
        grouped = collect_alerts(all_subscriptions, today_group_names)
        if not grouped:
            logging.info("✅ Нет завершённых или проблемных абонементов для отправки.")
            return
        bot_username = getattr(app.bot, "username", None) or os.getenv("BOT_USERNAME")
        if not bot_username:
            bot_user = await app.bot.get_me()
            bot_username = bot_user.username
        for message in render_digest_parts(grouped, bot_username):
            await app.bot.send_message(chat_id=ADMIN_ID, text=message,
                                       parse_mode="HTML", disable_web_page_preview=True)
        logging.info("📤 Отправлен digest абонементов (%d категорий)", len(grouped))
    except Exception as e:
        logging.warning(f"❗️ Ошибка при проверке завершённых абонементов: {e}")

# -----------------------------------------------------------------------------
# ------------------------------------------------------------------------------------    
WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday"
]

def get_check_hour(group: dict) -> int:
    if group.get("check_window") == "evening":
        return CHECK_HOUR_EVENING
    return CHECK_HOUR_DAY

def get_target_lesson_weekday(now, day_offset: int) -> str:
    """
    Возвращает день недели занятия, которое нужно проверить СЕЙЧАС.

    check_day_offset = 0  -> спрашиваем в день занятия
    check_day_offset = 1  -> спрашиваем за день до занятия
    """
    today_idx = WEEKDAYS.index(now.strftime("%A"))
    target_idx = (today_idx + day_offset) % 7
    return WEEKDAYS[target_idx]

def should_ask_about_group(now, group: dict) -> bool:
    """
    Проверяет, пора ли сейчас задавать вопрос по этой группе.
    """
    check_hour = get_check_hour(group)

    if now.hour != check_hour:
        return False

    if not (CHECK_MIN_START <= now.minute <= CHECK_MIN_END):
        return False

    target_weekday = get_target_lesson_weekday(
        now,
        group.get("check_day_offset", 0)
    )
    return target_weekday in group["days"]

def get_expiry_hour(group: dict) -> int:
    if group.get("check_window") == "evening":
        return EXPIRY_HOUR_EVENING
    return EXPIRY_HOUR_DAY


def should_check_expiry_for_group(now, group: dict) -> bool:
    """
    Проверяет, пора ли сейчас запускать проверку абонементов по этой группе.
    """
    expiry_hour = get_expiry_hour(group)

    if now.hour != expiry_hour:
        return False

    if not (CHECK_MIN_START <= now.minute <= CHECK_MIN_END):
        return False

    target_weekday = get_target_lesson_weekday(
        now,
        group.get("check_day_offset", 0)
    )
    return target_weekday in group["days"]

def should_send_report_for_group(now, group: dict) -> bool:
    weekday = now.strftime("%A")

    if weekday not in group["days"]:
        return False

    if group.get("check_window") == "evening":
        if now.hour != REPORT_HOUR_MORNING:
            return False
    else:
        if now.hour != REPORT_HOUR_DAY:
            return False

    if not (CHECK_MIN_START <= now.minute <= CHECK_MIN_END):
        return False

    return True
    
async def scheduler(app):
    
    await asyncio.sleep(30)  # даём Render время на перезапуск
    last_check_dates = {}
    last_expiry_check = {}
    last_report_check = {}

    while True:
        try:
            now = now_local()
            weekday = now.strftime("%A")
            current_time = now.strftime("%H:%M")

            logging.info(f"[scheduler] Сейчас {current_time} {weekday}")
            logging.info(
                f"[scheduler] DAY={CHECK_HOUR_DAY}, EVENING={CHECK_HOUR_EVENING}, "
                f"EXPIRY_DAY={EXPIRY_HOUR_DAY}, EXPIRY_EVENING={EXPIRY_HOUR_EVENING}, "
                f"MIN={CHECK_MIN_START}–{CHECK_MIN_END}"
            )

            # 🔁 Опрос администратора по группам -------------------------------------------------
            for idx, group in enumerate(groups):
                if should_ask_about_group(now, group):
                    last_run_date = last_check_dates.get(group["key"])

                    if last_run_date != now.date():
                        logging.info(f"[scheduler] Время для опроса группы {group['name']} — запускаем")
                        await ask_admin(app, idx, group)
                        last_check_dates[group["key"]] = now.date()
                    else:
                        logging.info(f"[scheduler] Уже спрашивали сегодня по группе {group['name']}")

            # 📋 Проверка абонементов по группам -------------------------------------------------
            expiry_groups_to_check = []

            for group in groups:
                if should_check_expiry_for_group(now, group):
                    expiry_groups_to_check.append(group)

            if expiry_groups_to_check:
                expiry_keys = tuple(sorted(group["key"] for group in expiry_groups_to_check))
                last_run = last_expiry_check.get(expiry_keys)

                if last_run != now.date():
                    logging.info("[scheduler] Проверяем абонементы на завершение...")

                    today_groups = [
                        GROUP_NAME_MAP.get(group["name"])
                        for group in expiry_groups_to_check
                    ]
                    today_groups = [g for g in today_groups if g]

                    await check_expired_subscriptions(app, today_groups)
                    last_expiry_check[expiry_keys] = now.date()
                else:
                    logging.info("[scheduler] Проверка абонементов для этого окна уже была сегодня")
             
            report_groups_to_check = []

            for group in groups:
                if should_send_report_for_group(now, group):
                    report_groups_to_check.append(group)
            
            if report_groups_to_check:
                report_keys = tuple(sorted(group["key"] for group in report_groups_to_check))
                last_run = last_report_check.get(report_keys)
            
                if last_run != now.date():
                    logging.info("[scheduler] Отправляем репорты по группам...")
                    logging.info(f"[scheduler] Группы для репорта: {[g['name'] for g in report_groups_to_check]}")
            
                    resp = sheets_service.values().get(
                        spreadsheetId=SPREADSHEET_ID,
                        range="Репорты!A2:G"
                    ).execute()
                    today_str = now.strftime("%Y-%m-%d")
                    rows = canonical_report_rows(resp.get("values", []), today_str)
            
                    for group in report_groups_to_check:
                        group_name = group["name"]
            
                        row = next(
                            (r for r in rows if len(r) >= 7 and r[1] == group_name and r[6].startswith(today_str)),
                            None
                        )
            
                        if not row:
                            logging.info(f"[scheduler] Нет строки Репорты на сегодня для группы {group_name}")
                            continue
            
                        def safe_int(value):
                            text = str(value).strip() if value is not None else ""
                            if not text or text.lower() == "none":
                                return None
                            try:
                                return int(text)
                            except ValueError:
                                return None
            
                        poll_id = row[0]
                        report_message_id = safe_int(row[2]) if len(row) > 2 else None
                        ping_message_id = safe_int(row[3]) if len(row) > 3 else None
            
                        poll_to_group[poll_id] = {"name": group_name}
            
                        await send_admin_report(
                            app=app,
                            poll_id=poll_id,
                            report_message_id=report_message_id,
                            ping_message_id=ping_message_id
                        )
            
                    last_report_check[report_keys] = now.date()
                else:
                    logging.info("[scheduler] Репорты для этого окна уже отправлялись сегодня")

            await asyncio.sleep(20)

        except Exception as e:
            logging.error(f"[scheduler] Ошибка: {e}")
            await asyncio.sleep(10)
