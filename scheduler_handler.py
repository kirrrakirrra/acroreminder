from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from reminder_handler import poll_to_group, send_admin_report
from utils import now_local,format_now
from datetime import datetime, timedelta
from group_config import GROUPS, GROUP_NAME_MAP
from subscription_tools import (
    load_all_subscriptions,
    get_subscription_alert_status,
    is_started_subscription,
    parse_unpaid_payment,
)
import asyncio
import os
import logging

# ------------------------------------------------------------------------------------
groups = GROUPS


def canonical_report_rows(rows, report_date=None):
    """Select the newest row for each group/date from Sheets row order."""
    canonical = {}
    for row in rows:
        if len(row) < 7:
            continue
        row_date = str(row[6])[:10]
        if report_date is not None and row_date != report_date:
            continue
        canonical[(row[1], row_date)] = row
    return list(canonical.values())

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
    lesson_date = data[2] if len(data) > 2 else get_lesson_date(now_local(), group).isoformat()

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
        await send_reminder_and_poll(context, group, lesson_date, replace=action == "resend_reminder")
        await query.edit_message_text("Напоминание и опрос отправлены ✅")
    elif action == "skip":
        await query.edit_message_text("❌ Окей, ничего не публикуем.\nНапоминание: не забудьте сами сообщить группе о деталях отмены")


def get_lesson_date(now, group):
    return now.date() + timedelta(days=group.get("check_day_offset", 0))


def _report_rows():
    response = sheets_service.values().get(
        spreadsheetId=SPREADSHEET_ID, range="Репорты!A2:G"
    ).execute()
    return response.get("values", [])


def occurrence_was_sent(group, lesson_date):
    return any(
        len(row) >= 7 and row[1] == group["name"] and str(row[6])[:10] == str(lesson_date)
        for row in _report_rows()
    )


async def send_reminder_and_poll(context, group, lesson_date, replace=False):
    """The single delivery path used by scheduled and manual confirmations."""
    # Announcement
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
    
        # Poll
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

             # Сохраняем poll_id и название группы в Google Sheets (вкладка "Опросы")
            try:
                options_text = "|".join([opt.text for opt in poll_msg.poll.options])
                new_row = [[
                    poll_msg.poll.id,
                    group["name"],
                    "", "", format_now(), "", options_text  # пустые ячейки под user_id, username, время и ответ
                ]]
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Опросы!A1",  # ⬅️ явное указание вкладки
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_row}
                ).execute()
            except Exception as e:
                logging.warning(f"❗ Не удалось записать poll_id: {e}")

            context.bot_data[poll_msg.poll.id] = poll_msg.poll.options  
            
            # 1. Отправили опрос → запланировать отчет
            poll_to_group[poll_msg.poll.id] = group
            
            # Persist the occurrence. A resend replaces the latest canonical row
            # so there remains exactly one current poll for normal data.
            try:
                new_row = [[
                    poll_msg.poll.id,
                    group["name"],
                    "",  # report_message_id
                    "",  # ping_message_id
                    str(group["group_id"]),
                    str(group["thread_id"]) if group.get("thread_id") is not None else "",
                    str(lesson_date)
                ]]
                rows = _report_rows()
                matches = [i for i, row in enumerate(rows, start=2) if len(row) >= 7 and row[1] == group["name"] and str(row[6])[:10] == str(lesson_date)]
                if replace and matches:
                    sheets_service.values().update(
                        spreadsheetId=SPREADSHEET_ID, range=f"Репорты!A{matches[-1]}:G{matches[-1]}",
                        valueInputOption="USER_ENTERED", body={"values": new_row}
                    ).execute()
                else:
                    sheets_service.values().append(
                        spreadsheetId=SPREADSHEET_ID, range="Репорты!A1",
                        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                        body={"values": new_row}
                    ).execute()
                logging.info("✅ Запланированный отчет записан в таблицу Репорты")
            except Exception as e:
                logging.warning(f"❗ Не удалось записать запланированный отчет: {e}")
        
        except Exception as e:
            logging.warning(f"❗ Не удалось отправить опрос: {e}")
        
    except Exception as e:
        logging.warning(f"❗ Не удалось отправить напоминание: {e}")
        raise


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
    logging.info("🔍 check_expired_subscriptions запущена")

    try:
        all_subscriptions = await asyncio.to_thread(load_all_subscriptions)

        if not all_subscriptions:
            logging.warning("⛔️ Не удалось загрузить абонементы или список пуст.")
            return

        logging.info(f"🔎 Группы, которые проверяются сегодня: {today_group_names}")

        subscriptions_today = [
            sub for sub in all_subscriptions
            if sub.get("group") in today_group_names
        ]

        if not subscriptions_today:
            logging.info("ℹ️ Нет абонементов для групп на сегодня.")
            return

        found = False
        unpaid_subscriptions = []
        checked_groups = {sub.get("group") for sub in subscriptions_today}

        for sub in subscriptions_today:
            name = sub.get("name", "—")
            group = sub.get("group", "—")
            sub_type = sub.get("subscription_type")
            sub_type_raw = sub.get("subscription_type_raw", "—")
            start_date = sub.get("start_date_raw", "—")
            end_date = sub.get("end_date_raw", "—")
            used = sub.get("used", 0)
            unused_raw = str(sub.get("unused", "")).strip()
            # difference = str(sub.get("difference", "")).strip()
            wo_left = sub.get("wo_left_until_end", 0)
            days_until_end = str(sub.get("days_until_end", "")).strip()
            visit_dates = sub.get("visit_dates", [])
            warning_7 = str(sub.get("warning_7", "")).strip().lower() == "warning_7"

            logging.info(
                f"[expired-debug] name={name}, raw={sub_type_raw}, normalized={sub_type}, unused={unused_raw}, warning_7={warning_7}"
            )

            # Blank rows and drop-ins are not subscription notifications.
            if (
                not str(sub_type_raw).strip()
                or not sub_type
                or sub_type == "drop_in"
                or sub_type_raw.lower() == "разово"
            ):
                continue

            unpaid_status = parse_unpaid_payment(sub.get("deposit"))
            if unpaid_status and is_started_subscription(sub):
                unpaid_subscriptions.append((name, group, unpaid_status))

            try:
                unused = int(unused_raw) if unused_raw != "" else None
            except ValueError:
                unused = None

            dates_text = "\n".join(
                [f"{i}. {d}" for i, d in enumerate(visit_dates, start=1)]
            ) if visit_dates else "—"

            parts = [
                f"👤 *Имя:* {name}",
                f"🏷️ *Группа:* {group}",
                f"🧾 *Абонемент:* {sub_type_raw}",
                f"📆 *Срок действия:* {start_date} — {end_date}",
            ]

            should_send = False
            status = get_subscription_alert_status(sub)

            if status == "expired":
                parts.insert(0, "📛 *Срок действия абонемента истёк*")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                parts.append(
                    "\n💳 *Не забудьте оплатить следующий абонемент, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True

            elif status == "finished":
                parts.insert(0, "❌ *Абонемент завершён*")
                parts.append(f"☑️ *Использовано:* {used}")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                parts.append(
                    "\n💳 *Не забудьте оплатить следующий абонемент, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True

            elif status == "no_calendar_lessons":
                parts.insert(0, "⛔️ *Абонемент завершён по расписанию*")
                parts.append(f"☑️ *Использовано:* {used}")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                parts.append(
                    "\nПо расписанию больше нет занятий, которые входят в срок этого абонемента."
                )
                parts.append(
                    "\n💳 *Пожалуйста, внесите оплату за следующий абонемент, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True
            
            elif status == "last_calendar_lesson_today":
                parts.insert(0, "🚨 *Сегодня финальный день абонемента*")
                parts.append(f"☑️ *Использовано:* {used}")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                parts.append(
                    "\nСегодня последнее занятие, которое попадает в срок действия абонемента."
                )
                parts.append(
                    "\n💳 *Пожалуйста, внесите оплату за следующий абонемент, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True
            
            elif status == "last_calendar_lesson":
                parts.insert(0, "❕🗓️ *Осталось одно занятие в рамках абонемента*")
                parts.append(f"☑️ *Использовано:* {used}")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                parts.append(
                    "\nВ срок действия абонемента попадает ещё только одно занятие."
                )
                parts.append(
                    "\n💳 *Пожалуйста, внесите оплату за следующий абонемент, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True

            elif status == "last_lesson":
                parts.insert(0, "❕ *В абонементе осталось 1 занятие*")
                parts.append(f"☑️ *Использовано:* {used}")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                parts.append(
                    "\n💳 *Пожалуйста, внесите оплату за следующий абонемент, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True

            elif status == "warning_7":
                parts.insert(0, "⏳ *До конца абонемента осталось менее 7 дней*")
                parts.append(f"📅 *Даты посещений:*\n{dates_text}")

                if days_until_end:
                    parts.append(f"\n⏳ *Осталось дней до конца абонемента:* {days_until_end}")

                parts.append(
                    f"\n💳 *Пожалуйста, внесите оплату за следующий абонемент до {end_date}, "
                    "чтобы сохранить место в группе.*"
                )
                should_send = True

                # # Difference добавляем как доп. блок в то же сообщение
                # if difference:
                #     parts.append(
                #         f"\n⚠️ *Осталось занятий:* *{sub.get('unused', 0)}*\n"
                #         f"*Тренировок до конца абонемента:* *{wo_left}*\n"
                #         "_Неиспользованные занятия не переносятся._"
                #     )
                #     should_send = True

            if should_send:
                msg = "\n".join(parts)

                await app.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=msg,
                    parse_mode="Markdown"
                )
                logging.info(f"📤 Отправлено сообщение по абонементу: {name} / {group}")
                found = True

        if unpaid_subscriptions:
            show_group = len(checked_groups) > 1
            unpaid_lines = []
            for name, group, unpaid_status in unpaid_subscriptions:
                group_context = f" ({group})" if show_group else ""
                unpaid_lines.append(
                    f"⚠️ {name}{group_context} — {unpaid_status}"
                )

            await app.bot.send_message(
                chat_id=ADMIN_ID,
                text="💳 *Неоплаченные абонементы*\n\n" + "\n".join(unpaid_lines),
                parse_mode="Markdown",
            )
            logging.info(
                "📤 Отправлен сводный список неоплаченных абонементов: %d",
                len(unpaid_subscriptions),
            )
            found = True

        if not found:
            logging.info("✅ Нет завершённых или проблемных абонементов для отправки.")

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
