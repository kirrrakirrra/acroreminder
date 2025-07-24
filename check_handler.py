from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import ContextTypes
import os
import logging

# ——————————————————————————————————————————————
# Настройка доступа к Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # берём из окружения
SHEET_RANGE = 'Абонементы!B1:P'

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()
# ——————————————————————————————————————————————

async def check_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name

    logging.info(f"/check used by {full_name} (@{user}) [ID: {user_id}]")

    karina_id = os.getenv("KARINA_ID")
    if karina_id:
        try:
            await context.bot.send_message(
                chat_id=karina_id,
                text=f"👀 Команду /check использовал: {full_name} (@{user}) [ID: {user_id}]"
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение админу: {e}")

    # if not user:
    #     return await update.message.reply_text(
    #         "❗ У вас не задан Telegram‐username. Пожалуйста, создайте его в настройках Telegram."
    #     )

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
        idx_start = header.index("Дата начала")
        idx_end = header.index("Срок действия")
        idx_used = header.index("Использованно")
        idx_usercol = header.index("username")
        visit_cols = [f"{i} посещение" for i in range(1, 9)]
        idx_dates = [header.index(col) for col in visit_cols]
    except ValueError as e:
        return await update.message.reply_text(f"Не найдена колонка в таблице: {e}")

    # username-сравнение (без @, с нижним регистром)
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

    # 2. Если по username не найдено — ищем по user_id
    if not user_rows:
        try:
            idx_idcol = header.index("user_id")
        except ValueError:
            idx_idcol = None

        if idx_idcol is not None:
            for row in rows[1:]:
                if len(row) > idx_idcol and row[idx_idcol].strip() == str(user_id):
                    user_rows.append(row)

    # 3. Если всё ещё пусто — сообщение
    if not user_rows:
        return await update.message.reply_text("У вас нет активных абонементов, или ваш username не добавлен, пожалуйста, обратитесь к администратору.")

    # Формируем сообщение
    messages = []
    for row in user_rows:
        name = row[idx_name] if len(row) > idx_name else "—"
        group = row[idx_group] if len(row) > idx_group else "—"
        start = row[idx_start] if len(row) > idx_start else "—"
        end = row[idx_end] if len(row) > idx_end else "—"
        used = row[idx_used] if len(row) > idx_used else "0"

        dates = []
        for i, idx in enumerate(idx_dates, start=1):
            if len(row) > idx and row[idx].strip():
                dates.append(f"{i}. {row[idx]}")
        dates_text = "\n".join(dates) if dates else "—"

        msg = (
            f"👤 *Имя:* `{name}`\n"
            f"🏷️ *Группа:* `{group}`\n"
            f"📆 *Срок действия:* `{start} — {end}`\n"
            f"✅ *Использовано:* `{used}` из `8`\n"
            f"📅 *Даты посещений:*\n{dates_text}"
        )
        messages.append(msg)

    await update.message.reply_text("\n\n".join(messages), parse_mode="Markdown")
