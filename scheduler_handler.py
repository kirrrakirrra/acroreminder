from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from reminder_handler import poll_to_group
from reminder_handler import schedule_report
from utils import now_local,format_now
from datetime import datetime
from group_config import GROUPS, GROUP_NAME_MAP
from subscription_tools import load_all_subscriptions
import asyncio
import os
import logging

# ------------------------------------------------------------------------------------
groups = GROUPS

ADMIN_ID = int(os.getenv("ADMIN_ID"))

CHECK_HOUR_DAY = int(os.getenv("CHECK_HOUR_DAY", 11))
CHECK_HOUR_EVENING = int(os.getenv("CHECK_HOUR_EVENING", 18))
CHECK_MIN_START = int(os.getenv("CHECK_MIN_1START", 1))
CHECK_MIN_END = int(os.getenv("CHECK_MIN_END", 5))
EXPIRY_HOUR_DAY = int(os.getenv("EXPIRY_HOUR_DAY", 12))
EXPIRY_HOUR_EVENING = int(os.getenv("EXPIRY_HOUR_EVENING", 19))

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
def get_decision_keyboard(group_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data=f"yes|{group_id}")],
        [InlineKeyboardButton("❌ Нет, я сам напишу в группу", callback_data=f"skip|{group_id}")],
    ])


# ------------------------------------------------------------------------------------

async def ask_admin(app, group_id, group):
    msg = await app.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Сегодня занятие для {group['name']} в {group['time']} по расписанию?",
        reply_markup=get_decision_keyboard(group_id)
    )
   # pending[msg.message_id] = group

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    action = data[0]
    group_id = int(data[1])
    group = groups[group_id]

    if action == "yes":
        # Сообщение-объявление
        if group.get("thread_id") is not None:
            await context.bot.send_message(
                chat_id=group["group_id"],
                message_thread_id=group["thread_id"],
                text=f"Доброго дня! Занятие для {group['name']} по расписанию в {group['time']} 🤸🏻🤸🏻‍♀️"
            )
        else:
            await context.bot.send_message(
                chat_id=group["group_id"],
                text=f"Доброго дня! Занятие для {group['name']} по расписанию в {group['time']} 🤸🏻🤸🏻‍♀️"
            )
    
        # Опрос
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
                    question="Кто будет сегодня на занятии?",
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
            
            # 2. Записать запланированный отчёт в таблицу "Репорты"
            try:
                new_row = [[
                    poll_msg.poll.id,
                    group["name"],
                    "",  # report_message_id
                    "",  # ping_message_id
                    str(group["group_id"]),
                    str(group["thread_id"]) if group.get("thread_id") is not None else "",
                    now_local().strftime("%Y-%m-%d")
                ]]
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Репорты!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_row}
                ).execute()
                logging.info("✅ Запланированный отчет записан в таблицу Репорты")
            except Exception as e:
                logging.warning(f"❗ Не удалось записать запланированный отчет: {e}")

            context.application.create_task(schedule_report(context.application, group, poll_msg.poll.id))
        
        except Exception as e:
            logging.warning(f"❗ Не удалось отправить опрос: {e}")
        
        finally:
            # 🔧 Убираем кнопки В ЛЮБОМ СЛУЧАЕ
            try:
                logging.info("🧼 Убираем кнопки после ответа администратора")
                await query.edit_message_text("Напоминание и опрос отправлены ✅")
            except Exception as e:
                logging.warning(f"⚠️ Не удалось изменить сообщение с кнопками: {e}")
        # await query.edit_message_text("Напоминание и опрос отправлены ✅")
    elif action == "skip":
        await query.edit_message_text("❌ Окей, ничего не публикуем.\nНапоминание: не забудьте сами сообщить группе о деталях отмены")
    pass
# -----------------------------------------------------------------------------
# ------------------------------------------------------------------------------------
async def check_expired_subscriptions(app, today_group_names):
    logging.info("🔍 check_expired_subscriptions запущена")

    try:
        all_subscriptions = load_all_subscriptions()

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

        for sub in subscriptions_today:
            name = sub.get("name", "—")
            group = sub.get("group", "—")
            sub_type = sub.get("subscription_type")
            sub_type_raw = sub.get("subscription_type_raw", "—")
            start_date = sub.get("start_date_raw", "—")
            end_date = sub.get("end_date_raw", "—")
            used = sub.get("used", 0)
            unused_raw = str(sub.get("unused", "")).strip()
            difference = str(sub.get("difference", "")).strip()
            wo_left = sub.get("wo_left_until_end", 0)
            days_until_end = str(sub.get("days_until_end", "")).strip()
            end_date = sub.get("end_date_raw", "—")
            visit_dates = sub.get("visit_dates", [])
            warning_7 = str(sub.get("warning_7", "")).strip().lower() == "warning_7"

            logging.info(
                f"[expired-debug] name={name}, raw={sub_type_raw}, normalized={sub_type}, unused={unused_raw}, warning_7={warning_7}"
            )

            # ❗ Разовые не включаем в отчёт по абонементам
            if sub_type == "drop_in" or sub_type_raw.lower() == "разово":
                continue

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

            # ----------------------------
            # Безлимит
            # ----------------------------
            if sub_type == "unlimited" or sub_type_raw.strip().lower() == "безлимит":
                if warning_7:
                    parts.insert(0, "⏳ *До конца абонемента осталось менее 7 дней*")
                    parts.append(f"📅 *Даты посещений:*\n{dates_text}")

                    if days_until_end:
                        parts.append(f"⏳ *Осталось дней:* {days_until_end}")

                    parts.append(
                        f"\n💳 *Пожалуйста, внесите оплату за следующий абонемент до {end_date}, чтобы сохранить место в группе.*"
                    )
                    should_send = True

            # ----------------------------
            # Лимитные абонементы
            # ----------------------------
            else:
                # Главный статус по приоритету как в /check
                if unused == 0:
                    parts.insert(0, "🔚 *Абонемент завершён*")
                    parts.append(f"☑️ *Использовано:* {used}")
                    parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                    parts.append(
                        "\n💳 Не забудьте оплатить следующий абонемент, "
                        "чтобы сохранить место в группе."
                    )
                    should_send = True

                elif unused == 1:
                    parts.insert(0, "📌 *В абонементе осталось 1 занятие*")
                    parts.append(f"☑️ *Использовано:* {used}")
                    parts.append(f"📅 *Даты посещений:*\n{dates_text}")
                    parts.append(
                        "\n💳 *Пожалуйста, внесите оплату за следующий абонемент, чтобы сохранить место в группе.*"
                    )
                    should_send = True

                elif warning_7:
                    parts.insert(0, "⏳ *До конца абонемента осталось менее 7 дней*")
                    parts.append(f"📅 *Даты посещений:*\n{dates_text}")

                    if days_until_end:
                        parts.append(f"\n⏳ *Осталось дней:* {days_until_end}")

                    parts.append(
                        f"💳 *\nПожалуйста, внесите оплату за следующий абонемент до {end_date}, "
                        "чтобы сохранить место в группе.*"
                    )
                    should_send = True

                # Difference добавляем как доп. блок в то же сообщение
                if difference:
                    parts.append(
                        f"\n⚠️ *Осталось занятий:* *{sub.get('unused', 0)}*\n"
                        f"*Тренировок до конца абонемента:* *{wo_left}*\n"
                        "_Неиспользованные занятия не переносятся._"
                    )
                    should_send = True

            if should_send:
                msg = "\n".join(parts)

                await app.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=msg,
                    parse_mode="Markdown"
                )
                logging.info(f"📤 Отправлено сообщение по абонементу: {name} / {group}")
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
    
async def scheduler(app):
    
    await asyncio.sleep(30)  # даём Render время на перезапуск
    last_check_dates = {}
    last_expiry_check = {}

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

            # 🔁 Опрос администратора по группам
            for idx, group in enumerate(groups):
                if should_ask_about_group(now, group):
                    last_run_date = last_check_dates.get(group["key"])

                    if last_run_date != now.date():
                        logging.info(f"[scheduler] Время для опроса группы {group['name']} — запускаем")
                        await ask_admin(app, idx, group)
                        last_check_dates[group["key"]] = now.date()
                    else:
                        logging.info(f"[scheduler] Уже спрашивали сегодня по группе {group['name']}")

            # 📋 Проверка абонементов по группам
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

            await asyncio.sleep(20)

        except Exception as e:
            logging.error(f"[scheduler] Ошибка: {e}")
            await asyncio.sleep(10)
