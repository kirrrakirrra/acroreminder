import asyncio
import logging
import os
import re
from telegram.constants import ParseMode
from datetime import datetime

delay_minutes = int(os.getenv("REPORT_DELAY_MINUTES", 5))

# Хранилище голосов в памяти (резервный вариант)
poll_votes = {}

# Храним связь poll_id → group
poll_to_group = {}

# Google Sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

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
    vote_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

# Планируем отправку отчета
async def schedule_report(app, group, poll_id):
    poll_to_group[poll_id] = group
    await asyncio.sleep(60 * delay_minutes)
    await send_admin_report(app, poll_id)

def escape_md(text):
    """
    Экранирует спецсимволы Markdown (v1), чтобы избежать ошибок Telegram.
    """
    return re.sub(r'([_*[\]()])', r'\\\1', text)
    
# Отправка отчёта админу через delay_minutes 
async def send_admin_report(app, poll_id):
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
            range=USERNAMES_SHEET + "!A2:M"
        ).execute()
        rows = resp.get("values", [])

        idx_name = 1
        idx_username = 2
        idx_parent = 7
        idx_pause = 9
        idx_voted = 10
        idx_group = 11

        # Категории: кто как проголосовал
        voted_by_subscription = []
        voted_by_one_time = []
        voted_absent = []

        # Кто не проголосовал — делим на 3 категории
        not_voted_subscription = []
        not_voted_paused = []
        not_voted_one_time = []
        
        for row in rows:
            if len(row) < idx_group:
                continue
            group_col = row[idx_group].strip()
            if group_col != group_name_code:
                continue
            name = row[idx_name].strip()
            parent_name = row[idx_parent].strip() if len(row) > idx_parent else ""
            username = escape_md(row[idx_username].strip()) if len(row) > idx_username else ""
            pause = row[idx_pause].strip().upper() if len(row) > idx_pause else ""
            voted = row[idx_voted].strip().lower()

            parent_info = f"👤 {parent_name}"
            if username:
                parent_info += f" (@{username})"
            child_info = f"🧒 {name}\n  {parent_info}"

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
            
        parts = [f"📋 __Отчёт {group_name_code}:__"]

        # === Те, кто проголосовал ===
        if voted_by_subscription:
            parts.append(f"✅ __По абонементу ({len(voted_by_subscription)}):__\n\n" + "\n".join(voted_by_subscription))
        if voted_by_one_time:
            parts.append(f"💵 __Разово ({len(voted_by_one_time)}):__\n\n" + "\n".join(voted_by_one_time))
        if voted_absent:
            parts.append(f"❌ __Пропускают ({len(voted_absent)}):__\n" + "\n".join(voted_absent))
        
        # === Разделитель ===
        parts.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        
        # === Не отметились ===
        parts.append("⁉️ __Не отметились:__")
        
        if not_voted_subscription:
            parts.append(f"🎟 __Абонементы ({len(not_voted_subscription)}):__\n\n" + "\n".join(not_voted_subscription))
        
        if not_voted_paused:
            parts.append(f"⏸ __На паузе ({len(not_voted_paused)}):__\n\n" + "\n".join(not_voted_paused))
        
        if not_voted_one_time:
            parts.append(f"💵 __Ходят разово ({len(not_voted_one_time)}):__\n\n" + "\n".join(not_voted_one_time))

        
        report = "\n\n".join(parts)
        
        logging.info(f"📤 Отправка отчета админу:\n{report}")
        await app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode=ParseMode.MARKDOWN)
    
    except Exception as e:
        logging.warning(f"❗ Ошибка при отправке отчёта админу: {e}")
        
