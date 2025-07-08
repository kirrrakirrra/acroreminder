from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import ContextTypes

# ——————————————————————————————————————————————
# Настройка доступа к Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SERVICE_ACCOUNT_FILE = 'service_account.json'
import os
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_RANGE = 'Абонементы!A1:M'

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()
# ——————————————————————————————————————————————

async def check_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username
    if not user:
        return await update.message.reply_text(
            "❗ У вас не задан Telegram‐username. Пожалуйста, создайте его в настройках Telegram."
        )

    resp = sheets_service.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_RANGE
    ).execute()
    rows = resp.get('values', [])

    if len(rows) < 2:
        return await update.message.reply_text("Таблица пуста или недоступна.")

    header = rows[0]
    try:
        idx_name = header.index("Имя ребёнка")
        idx_group = header.index("Группа")
        idx_bought = header.index("Дата покупки")
        idx_used = header.index("Использовано")
        idx_usercol = header.index("username")
        visit_cols = [f"{i} посещение" for i in range(1, 9)]
        idx_dates = [header.index(col) for col in visit_cols]
    except ValueError as e:
        return await update.message.reply_text(f"Не найдена колонка в таблице: {e}")

    raw_user = update.effective_user.username
    user = raw_user.lstrip('@').lower()
    user_rows = []
    for row in rows[1:]:
        if len(row) <= idx_usercol:
            continue
        cell = row[idx_usercol]
        allowed = [n.strip().lstrip('@').lower() for n in cell.split(',') if n.strip()]
        if user in allowed:
            user_rows.append(row)

    if not user_rows:
        return await update.message.reply_text("У вас нет активных абонементов.")

    messages = []
    for row in user_rows:
        name = row[idx_name] if len(row) > idx_name else "—"
        group = row[idx_group] if len(row) > idx_group else "—"
        bought = row[idx_bought] if len(row) > idx_bought else "—"
        used = row[idx_used] if len(row) > idx_used else "0"

        dates = []
        first_date = None
        for i, idx in enumerate(idx_dates, start=1):
            if len(row) > idx and row[idx].strip():
                d = row[idx]
                if first_date is None:
                    first_date = d
                dates.append(f"{i}. {d}")
        dates_text = "\n".join(dates) if dates else "—"

        msg = (
            f"👤 *Имя:* `{name}`\n"
            f"🏷️ *Группа:* `{group}`\n"
            f"🛒 *Куплен:* `{bought}`\n"
            f"✅ *Использовано:* `{used}` из `8`\n"
            f"📅 *Даты посещений:*\n{dates_text}"
        )
        if first_date:
            msg += (
                f"\n\nℹ️ Абонемент действует в течение календарного месяца\n"
                f"   с первого посещения: `{first_date}`"
            )
        messages.append(msg)

    await update.message.reply_text("\n\n".join(messages), parse_mode="Markdown")
