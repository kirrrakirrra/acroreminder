from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from datetime import datetime
import logging
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
USERS_SHEET = 'users'

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()

async def save_user_if_new(user_id: int, username: str, full_name: str):
    try:
        result = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{USERS_SHEET}!A2:A"
        ).execute()
        existing_ids = [row[0] for row in result.get('values', [])]

        if str(user_id) not in existing_ids:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_row = [
                str(user_id),
                f"@{username}" if username else "",
                full_name,
                now
            ]
            sheets_service.values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=USERS_SHEET,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [new_row]}
            ).execute()
    except Exception as e:
        logging.warning(f"Не удалось сохранить юзера в таблицу: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user_if_new(user.id, user.username, user.full_name)
    await update.message.reply_text(
    "Привет! 👋\n\n"
    "Здесь ты можешь:\n"
    "✔️ Проверить свой абонемент с помощью команды /check\n"
    "ℹ️ Получить информацию о ценах, расписании, правилах подготовки, абонементах и индивидуальных занятиях через команду /info\n\n"
    "Если у тебя есть предложения по улучшению или возникли вопросы — напиши тренеру. Мы на связи 😊"
)

def get_start_handler():
    return CommandHandler("start", start_command)
