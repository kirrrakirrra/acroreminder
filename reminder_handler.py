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
    full_name = user.full_name
    vote_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    selected_options = update.poll_answer.option_ids
    if not selected_options:
        return

    # Получаем текст ответа из poll.message.options (если доступно)
    option_text = ""
    try:
        poll = context.bot_data.get(poll_id)
        if poll:
            option_text = poll.options[selected_options[0]].text
    except:
        option_text = "(нет текста)"

    group_name = poll_to_group.get(poll_id, {}).get("name", "?")

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
            full_name,
            option_text,
            vote_time
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

 # Планируем напоминание через 60 минут
async def schedule_reminder(app, group, poll_id):
    poll_to_group[poll_id] = group
#     # await asyncio.sleep(60 * 60)
#     # await send_nonresponders_reminder(app, poll_id)

# # Сравнение участников опроса и абонементов
# async def send_nonresponders_reminder(app, poll_id):
#     group = poll_to_group.get(poll_id)
#     if not group:
#         logging.warning(f"❗ Не найдена группа для poll_id {poll_id}")
#         return

#     try:
#         from scheduler_handler import sheets_service, SPREADSHEET_ID, SHEET_RANGE

#         resp = sheets_service.values().get(
#             spreadsheetId=SPREADSHEET_ID,
#             range=SHEET_RANGE
#         ).execute()
#         rows = resp.get("values", [])
#         if len(rows) < 2:
#             return

#         header = rows[0]
#         idx_usercol = header.index("username")
#         idx_group = header.index("Группа")
#         idx_pause = header.index("Пауза") if "Пауза" in header else None

#         group_name = group["name"]
#         group_names_map = {
#             "Старшей начинающей группы": "6-9 лет начинающие",
#             "Старшей продолжающей группы": "6-9 лет продолжающие",
#             "Младшей группы": "4-5 лет",
#         }
#         group_value = group_names_map.get(group_name)

#         mentions = []
#         for row in rows[1:]:
#             if len(row) <= max(idx_usercol, idx_group):
#                 continue
#             group_cell = row[idx_group].strip()
#             if group_cell != group_value:
#                 continue

#             username = row[idx_usercol].strip().lstrip("@").lower()
#             if not username:
#                 continue

#             pause = row[idx_pause].strip().upper() if idx_pause and len(row) > idx_pause else ""
#             if pause == "TRUE":
#                 continue

#             mentions.append(f"@{username}")

#         # Получаем ответы из Google Sheet
#         result = sheets_service.values().get(
#             spreadsheetId=SPREADSHEET_ID,
#             range=SURVEY_SHEET + "!A2:G"
#         ).execute()
#         voted_rows = result.get("values", [])
#         voted_usernames = set(row[3].lstrip("@").lower() for row in voted_rows if row[0] == poll_id)

#         final_mentions = [m for m in mentions if m.lstrip("@").lower() not in voted_usernames]

#         if final_mentions:
#             text = (
#                 "⏰ *Напоминание!*\n"
#                 "Кто-то из вас ещё не отметил участие в сегодняшнем занятии. "
#                 "Пожалуйста, отметьтесь в опросе выше 👆\n\n"
#                 + " ".join(final_mentions)
#             )

#             await app.bot.send_message(
#                 chat_id=group["thread_id"],
#                 message_thread_id=group["thread_id"],
#                 text=text,
#                 parse_mode=ParseMode.MARKDOWN
#             )
#         else:
#             logging.info(f"✅ Все участники из {group['name']} отметились")

#     except Exception as e:
#         logging.warning(f"❗ Ошибка в send_nonresponders_reminder: {e}")
