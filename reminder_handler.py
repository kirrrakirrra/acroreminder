import asyncio
import logging
import os
import re
from utils import now_local, format_now
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from datetime import datetime, timedelta

# delay_minutes = int(os.getenv("REPORT_DELAY_MINUTES", 1))
report_hour = int(os.getenv("REPORT_HOUR", 15))
report_minute = int(os.getenv("REPORT_MINUTE", 10))

# Хранилище голосов в памяти (резервный вариант) и poll_id → group
poll_votes = {}
poll_to_group = {}

# Google Sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SURVEY_SHEET = 'Опросы'
USERNAMES_SHEET = "usernames"

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()

# Обработчик голосов
async def handle_poll_answer(update, context):
    poll_id = update.poll_answer.poll_id
    user = update.poll_answer.user
    user_id = user.id
    username = user.username or "(без username)"
    vote_time = format_now()
    full_name = user.full_name

    selected_options = update.poll_answer.option_ids
    if not selected_options:
        return

    # Получаем текст ответа из poll.message.options (если доступно)
    option_text = ""
    try:
        options = context.bot_data.get(poll_id)
        if options and len(selected_options) > 0:
            option_text = options[selected_options[0]].text
    except Exception as e:
        logging.warning(f"❗ Ошибка при получении текста опции: {e}")
        option_text = "(нет текста)"
       
    # Получаем название группы из poll_to_group
    group_name = poll_to_group.get(poll_id, {}).get("name", "?")
    logging.info(f"📝 Ответ от пользователя для группы: {group_name}")

    # Пишем в память (резервно)
    if poll_id not in poll_votes:
        poll_votes[poll_id] = set()
    poll_votes[poll_id].add(user_id)

    # Запись в Google Sheet
    try:
        new_row = [[
            poll_id,
            group_name,
            str(user_id),
            f"@{username}" if username else "",
            vote_time,
            full_name,
            option_text
        ]]
        sheets_service.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=SURVEY_SHEET,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": new_row}
        ).execute()
        logging.info(f"✅ Ответ опроса записан: {user_id} / @{username} — {option_text}")
    except Exception as e:
        logging.warning(f"❗ Не удалось записать голос в таблицу: {e}")

# 🧠 Восстанавливаем poll_id → group_name при запуске
def restore_poll_to_group():
    """
    При запуске — восстанавливает словарь poll_to_group из таблицы 'Опросы',
    чтобы знать, какой опрос к какой группе относится (в случае перезапуска).
    """
    try:
        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Опросы!A2:G"  # A2 — пропустить заголовок, G — колонка "ответ"
        ).execute()

        rows = resp.get("values", [])
        for row in rows:
            if len(row) < 2:
                continue  # Нужно минимум poll_id + group_name
            poll_id = row[0].strip()
            group_name = row[1].strip()
            if poll_id and group_name:
                poll_to_group[poll_id] = {"name": group_name}
        logging.info(f"♻️ Восстановлено {len(poll_to_group)} записей poll_to_group")
    except Exception as e:
        logging.warning(f"❗ Ошибка при восстановлении poll_to_group: {e}")

# Планируем отправку отчета в заданное время (только сегодня)
async def schedule_report(app, group, poll_id):
    poll_to_group[poll_id] = group
    now = now_local()
    
    TEST_DELAY_MINUTES = 1
    if TEST_DELAY_MINUTES:
        delay_seconds = TEST_DELAY_MINUTES * 60
        logging.info(f"🧪 Тест: ждем {TEST_DELAY_MINUTES} минут до отправки отчета")
        await asyncio.sleep(delay_seconds)
        await send_admin_report(app, poll_id)
        return
        
    report_time = now.replace(hour=report_hour, minute=report_minute, second=0, microsecond=0)

    # ⛔ Если уже позже — НЕ ОТПРАВЛЯЕМ
    if report_time <= now:
        logging.warning(f"⚠️ Время отправки отчета уже прошло ({report_time.strftime('%H:%M')}), отчёт не будет отправлен.")
        return

    delay_seconds = (report_time - now).total_seconds()
    logging.info(f"🕒 Ожидаем {int(delay_seconds)} секунд до отчета в {report_time.strftime('%H:%M')}")
    await asyncio.sleep(delay_seconds)
    await send_admin_report(app, poll_id)


def escape_md(text):
    """
    Экранирует спецсимволы Markdown (v1), чтобы избежать ошибок Telegram.
    """
    return re.sub(r'([_*[\]()])', r'\\\1', text)
    
# Отправка отчёта админу  
async def send_admin_report(app, poll_id, report_message_id=None, ping_message_id=None):
    group = poll_to_group.get(poll_id)
    if not group:
        logging.warning(f"⚠️ Не найдена группа для poll_id={poll_id}")
        return
    logging.info(f"📤 Готовим отчёт по poll_id={poll_id} для группы: {group['name']}")

    try:
        from scheduler_handler import ADMIN_ID
        
        group_name_code = group["name"]

        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=USERNAMES_SHEET + "!A1:N"
        ).execute()
        rows = resp.get("values", [])
        
        header = rows[0]
        try:
            idx_name = header.index("имя")
            idx_username = header.index("username1")
            idx_parent = header.index("Имя Родителя1")
            idx_pause = header.index("Пауза")
            idx_voted = header.index("Проголосовали сегодня")
            idx_group = header.index("тех группа")
        except ValueError as e:
            logging.warning(f"❗ Не найдена колонка: {e}")
            return
        rows = rows[1:]  # Пропускаем заголовок


        def safe_get(row, idx, default=""):
            return row[idx].strip() if len(row) > idx and row[idx] else default
        
        # Лог строк для отладки
        for i, row in enumerate(rows, start=1):
            logging.info(f"[DEBUG] Row {i}: length={len(row)} | values={row}")
        
        # Кто как проголосовал
        voted_by_subscription = []
        voted_by_one_time = []
        voted_absent = []
        not_voted_subscription = []
        not_voted_paused = []
        not_voted_one_time = []
        
        for row in rows:
            if len(row) < idx_group:
                continue
            group_col = safe_get(row, idx_group)
            if group_col != group_name_code:
                continue
        
            name = safe_get(row, idx_name).strip()
            parent_name = safe_get(row, idx_parent).strip()
            username = escape_md(safe_get(row, idx_username).strip())
            pause = safe_get(row, idx_pause).strip().upper()
            voted = safe_get(row, idx_voted).strip().lower()
        
            parent_info = f"👤 {parent_name}"
            if username:
                parent_info += f" (@{username})"
            child_info = f"🧒 {name}\n    {parent_info}"
        
            if "по абонементу" in voted:
                voted_by_subscription.append(child_info)
            elif "разово" in voted:
                voted_by_one_time.append(child_info)
            elif "пропускаем" in voted:
                voted_absent.append(child_info)
            elif not voted:
                if pause == "TRUE":
                    not_voted_paused.append(child_info)
                elif pause == "РАЗОВО":
                    not_voted_one_time.append(child_info)
                else:
                    not_voted_subscription.append(child_info)
        
        parts = [f"📋 *Отчёт {group_name_code}:*"]
        
        if voted_by_subscription:
            parts.append(f"==> ✅ *По абонементу ({len(voted_by_subscription)}):*\n\n" + "\n".join(voted_by_subscription))
        if voted_by_one_time:
            parts.append(f"==> 💵 *Разово ({len(voted_by_one_time)}):*\n\n" + "\n".join(voted_by_one_time))
        if voted_absent:
            parts.append(f"==> ❌ *Пропускают ({len(voted_absent)}):*\n\n" + "\n".join(voted_absent))
        
        parts.append("--------- ⁉️ *Не отметились:* ---------")
        
        if not_voted_subscription:
            parts.append(f"==> 🎟 *Абонементы ({len(not_voted_subscription)}):*\n\n" + "\n".join(not_voted_subscription))
        if not_voted_paused:
            parts.append(f"==> ⏸ *На паузе ({len(not_voted_paused)}):*\n\n" + "\n".join(not_voted_paused))
        if not_voted_one_time:
            parts.append(f"==> 💵 *Ходят разово ({len(not_voted_one_time)}):*\n\n" + "\n".join(not_voted_one_time))
        
        report = "\n\n".join(parts)
        logging.info(f"📤 Отправка отчета админу:\n{report}")
        # await app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode=ParseMode.MARKDOWN)

        report_msg = None
        ping_msg = None
                # 1. Отправляем отчет и сохраняем message_id
        if report_message_id:
            await app.bot.edit_message_text(
                chat_id=ADMIN_ID,
                message_id=report_message_id,
                text=report,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_report|{poll_id}")]
                ])
            )
        else:
            report_msg = await app.bot.send_message(
                chat_id=ADMIN_ID,
                text=report,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_report|{poll_id}")]
                ])
            )
        
        # 2. Формируем упоминания
        mentions = []
        for row in rows:
            if len(row) < idx_group:
                continue
            group_col = safe_get(row, idx_group)
            if group_col != group_name_code:
                continue
        
            pause = safe_get(row, idx_pause).upper()
            voted = safe_get(row, idx_voted)
            username = safe_get(row, idx_username)
        
            if not voted and pause != "TRUE" and pause != "РАЗОВО" and username:
                mentions.append(f"@{username}")
        
        # 3. Отправляем пинг, если есть кого упоминать
        if mentions:
            mention_text = "👋 Родители, пожалуйста, отметьтесь в опросе:\n" + " ".join(mentions)
            if ping_message_id:
                await app.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=ping_message_id,
                    text=mention_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📣 Отправить в группу", callback_data=f"notify_parents|{poll_id}")]
                    ])
                )
            else:
                ping_msg = await app.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=mention_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📣 Отправить в группу", callback_data=f"notify_parents|{poll_id}")]
                    ])
                )
        # Только если сообщение создаётся заново
        report_msg_id = report_msg.message_id if not report_message_id else report_message_id
        ping_msg_id = ping_msg.message_id if not ping_message_id else ping_message_id

        # 4. Записываем связку в таблицу "Репорты"
        if not report_message_id and report_msg:
            try:
                new_row = [[
                    poll_id.strip(),
                    group_name_code,
                    str(report_msg.message_id),
                    str(ping_msg.message_id) if ping_msg else "",
                    "",  # group_chat_id
                    "",  # thread_id
                ]]
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Репорты!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_row}
                ).execute()
                logging.info(f"✅ Связка сообщений записана в Репорты")
            except Exception as e:
                logging.warning(f"❗ Ошибка при записи связки в Репорты: {e}")

    except Exception as e:
        logging.warning(f"❗ Ошибка при отправке отчёта админу: {e}")
        
async def refresh_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, poll_id = query.data.split("|")

    # Получаем связку из таблицы Репорты
    try:
        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Репорты!A2:G"  # заголовки: poll_id, group_name, report_msg_id, ping_msg_id, group_id, thread_id, date
        ).execute()
        rows = resp.get("values", [])

        row = next((r for r in rows if r[0] == poll_id), None)
        if not row:
            await query.edit_message_text("❌ Не найдена связка в таблице Репорты.")
            return

        group_name = row[1]
        report_msg_id = int(row[2]) if row[2] else None
        ping_msg_id = int(row[3]) if row[3] else None

        # Добавляем минимум необходимый в словарь, если нужно
        poll_to_group[poll_id] = {"name": group_name}

        # Обновляем отчёт и пинг
        await send_admin_report(
            app=context.application,
            poll_id=poll_id,
            report_message_id=report_msg_id,
            ping_message_id=ping_msg_id
        )

    except Exception as e:
        logging.warning(f"❗ Ошибка в refresh_report_callback: {e}")
