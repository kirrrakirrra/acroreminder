import asyncio
import logging
from telegram.constants import ParseMode
from datetime import datetime

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
        if options:
            option_text = options[selected_options[0]].text
    except:
        logging.warning(f"❗ Ошибка при получении текста опции: {e}")
        option_text = "(нет текста)"

    # Загружаем список всех опросов, чтобы найти название группы по poll_id
    group_name = "?"
    try:
        result = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=SURVEY_SHEET + "!A2:B"
        ).execute()
        all_rows = result.get("values", [])
        group_name = next((row[1] for row in all_rows if row[0] == poll_id), "?")
    except Exception as e:
        logging.warning(f"❗ Ошибка при получении group_name по poll_id: {e}")

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
        
