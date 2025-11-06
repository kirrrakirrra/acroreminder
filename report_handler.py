import os
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Получаем переменные из окружения
ADMIN_ID = int(os.getenv("ADMIN_ID"))
KARINA_ID = int(os.getenv("KARINA_ID"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'service_account.json'

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()

from reminder_handler import send_admin_report, poll_to_group

def is_authorized(user_id: int) -> bool:
    return user_id in (ADMIN_ID, KARINA_ID)

# Команда /report
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return

    try:
        today = datetime.now().strftime("%Y-%m-%d")

        # Получаем строки из таблицы "Репорты"
        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Репорты!A2:G"
        ).execute()
        rows = resp.get("values", [])

        # Фильтруем только сегодняшние
        today_rows = [r for r in rows if len(r) >= 7 and r[6].startswith(today)]

        if not today_rows:
            await update.message.reply_text("ℹ️ Нет репортов на сегодня в таблице Репорты.")
            return

        count = 0
        for row in today_rows:
            poll_id = row[0]
            group_name = row[1]
            report_message_id = int(row[2]) if len(row) > 2 and row[2] else None
            ping_message_id = int(row[3]) if len(row) > 3 and row[3] else None

            poll_to_group[poll_id] = {"name": group_name}
            await send_admin_report(
                app=context.application,
                poll_id=poll_id,
                report_message_id=report_message_id,
                ping_message_id=ping_message_id
            )
            count += 1

        await update.message.reply_text(f"✅ Отправлено {count} отчётов за {today}.")

    except Exception as e:
        logging.warning(f"❗ Ошибка в report_command: {e}")
        await update.message.reply_text("❌ Ошибка при выполнении команды /report")

# Функция для регистрации команды
def register_report_handler(application):
    application.add_handler(CommandHandler("report", report_command))
