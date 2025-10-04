import asyncio
import logging
import os
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


    # # Загружаем список всех опросов, чтобы найти название группы по poll_id
    # group_name = "?"
    # try:
    #     result = sheets_service.values().get(
    #         spreadsheetId=SPREADSHEET_ID,
    #         range=SURVEY_SHEET + "!A2:B"
    #     ).execute()
    #     all_rows = result.get("values", [])
    #     group_name = next((row[1] for row in all_rows if row[0] == poll_id), "?")
    # except Exception as e:
    #     logging.warning(f"❗ Ошибка при получении group_name по poll_id: {e}")

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


# Отправка отчёта админу через delay_minutes 
async def send_admin_report(app, poll_id):
    logging.info(f"📤 Готовим отчёт по poll_id={poll_id} для группы: {group['name']}")
    group = poll_to_group.get(poll_id)
    if not group:
        logging.warning(f"⚠️ Не найдена группа для poll_id={poll_id}")
        return

    try:
        from scheduler_handler import ADMIN_ID
        
        group_name_code = group["name"]
        group_name_table = {
            "Старшей начинающей группы": "6-9 лет начинающие",
            "Старшей продолжающей группы": "6-9 лет продолжающие",
            "Младшей группы": "4-5 лет"
        }.get(group_name_code, group_name_code)

        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=USERNAMES_SHEET + "!A2:K"
        ).execute()
        rows = resp.get("values", [])
        
        paused = []
        one_time = []
        missed = []

        idx_group = 0
        idx_name = 1
        idx_username = 2
        idx_parent = 7
        idx_pause = 9
        idx_voted = 10
        
        for row in rows:
            if len(row) < idx_voted:
                continue
            group_col = row[idx_group].strip()
            if group_col != group_name_table:
                continue
            name = row[idx_name].strip()
            parent_name = row[idx_parent].strip() if len(row) > idx_parent else ""
            username = row[idx_username].strip() if len(row) > idx_username else ""
            pause = row[idx_pause].strip().upper() if len(row) > idx_pause else ""
            voted = row[idx_voted].strip()
            
            if pause == "TRUE":
                paused.append(f"{name} — {parent_name}")
            elif pause == "РАЗОВО":
                one_time.append(f"{name} — {parent_name}")
            elif not voted:
                mention = f"{name} — {parent_name}"
                if username:
                    mention += f" (@{username})"
                missed.append(mention)
            
        parts = [f"📋 *Отчёт по группе* {group_name_code}:"]
        if missed:
            parts.append(f"⁉️ Не отметились: {len(missed)}\n" + "\n".join(missed))
        if paused:
            parts.append(f"⏸ На паузе: {', '.join(paused)}")
        if one_time:
            parts.append(f"💵 Разово: {', '.join(one_time)}")
        
        report = "\n\n".join(parts)
        
        logging.info(f"📤 Отправка отчета админу:\n{report}")
        await app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode=ParseMode.MARKDOWN)
    
    except Exception as e:
        logging.warning(f"❗ Ошибка при отправке отчёта админу: {e}")
        
