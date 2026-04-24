import asyncio
import logging
import os
import re
from utils import now_local, format_now, notify_karina_action
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from datetime import datetime, timedelta

ADMIN_ID = int(os.getenv("ADMIN_ID"))

# delay_minutes = int(os.getenv("REPORT_DELAY_MINUTES", 1))
REPORT_HOUR_DAY = int(os.getenv("REPORT_HOUR_DAY", 15))
REPORT_HOUR_MORNING = int(os.getenv("REPORT_HOUR_MORNING", 8))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", 10))

# фильтруем по названию группы, позже можно добавить report window в сеттинг групп
def get_report_hour(group: dict) -> int:
    if group["name"] == "Взрослой группы":
        return REPORT_HOUR_MORNING
    return REPORT_HOUR_DAY

# Хранилище голосов в памяти (резервный вариант) и poll_id → group
poll_votes = {}
poll_to_group = {}

DEFAULT_OPTIONS = [
    "✅ Будем по абонементу",
    "💵 Будем разово",
    "❌ Пропускаем"
]

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

        # Получаем текст ответа по индексу из фиксированных опций
    try:
        option_text = DEFAULT_OPTIONS[selected_options[0]]
    except IndexError:
        option_text = "(неизвестный ответ)"
        logging.warning(f"❗ Неизвестный индекс опции: {selected_options[0]}")

    except Exception as e:
        logging.warning(f"❗ Ошибка при получении текста опции: {e}")
        option_text = "(ошибка опции)"
       
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
    
    # TEST_DELAY_MINUTES = 1
    # if TEST_DELAY_MINUTES:
    #     delay_seconds = TEST_DELAY_MINUTES * 60
    #     logging.info(f"🧪 Тест: ждем {TEST_DELAY_MINUTES} минут до отправки отчета")
    #     await asyncio.sleep(delay_seconds)
    #     await send_admin_report(app, poll_id)
    #     return

    report_hour = get_report_hour(group)
    report_time = now.replace(
        hour=report_hour,
        minute=REPORT_MINUTE,
        second=0,
        microsecond=0
    )
    
    if report_time <= now:
        if group["name"] == "Взрослой группы":
            report_time = report_time + timedelta(days=1)
        else:
            logging.warning(
                f"⚠️ Время отправки отчета уже прошло "
                f"({report_time.strftime('%Y-%m-%d %H:%M')}), отчёт не будет отправлен."
            )
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
            idx_deposit = header.index("Депозит")
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
            
            deposit_original = safe_get(row, idx_deposit).strip()
            deposit_raw = deposit_original.lower()
            
            payment_status = ""
            
            if deposit_raw:
                if "не оплач" in deposit_raw:
                    payment_status = "⚠️ не оплачено"
                else:
                    payment_status = f"💰 {deposit_original.strip()}"
            if payment_status:
                child_info = f"🧒 {name} — {payment_status}\n    {parent_info}"
            else:
                child_info = f"🧒 {name}\n    {parent_info}"
        
            if "по абонементу" in voted:
                voted_by_subscription.append(child_info)
            elif "разово" in voted:
                voted_by_one_time.append(child_info)
            elif "пропускаем" in voted:
                voted_absent.append(child_info)
            elif not voted:
                if pause == "ПАУЗА":
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

        report_msg = None
        ping_msg = None

        # Отправляем или обновляем отчёт
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
            logging.info(f"♻️ Обновлено сообщение отчета {report_message_id}")
        else:
            report_msg = await app.bot.send_message(
                chat_id=ADMIN_ID,
                text=report,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_report|{poll_id}")]
                ])
            )
            logging.info(f"📨 Отправлен новый отчет (msg_id={report_msg.message_id})")
        
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
        
            if not voted and pause != "ПАУЗА" and username:
                mentions.append(f"@{username}")
        
        # Отправляем или обновляем пинг
        if mentions:
            mention_text = "👋 Пожалуйста, отметьтесь в опросе:\n" + " ".join(mentions)
            if ping_message_id:
                await app.bot.edit_message_text(
                    chat_id=ADMIN_ID,
                    message_id=ping_message_id,
                    text=mention_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📣 Отправить в группу", callback_data=f"notify_parents|{poll_id}")]
                    ])
                )
                logging.info(f"♻️ Обновлено сообщение пинга {ping_message_id}")
            else:
                ping_msg = await app.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=mention_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📣 Отправить в группу", callback_data=f"notify_parents|{poll_id}")]
                    ])
                )
                logging.info(f"📨 Отправлен новый пинг (msg_id={ping_msg.message_id})")

        # Сохраняем ID сообщений, если они новые
        report_msg_id = report_msg.message_id if report_msg else report_message_id
        ping_msg_id = ping_msg.message_id if ping_msg else ping_message_id

        # 4. Записываем связку в таблицу "Репорты"
        try:
            found = False
            resp = sheets_service.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Репорты!A2:G"
            ).execute()
            rows = resp.get("values", [])
        
            for i, row in enumerate(rows, start=2):  # строки начинаются с A2
                if row[0] == poll_id:
                    found = True
            
                    safe_report_id = str(report_msg_id) if report_msg_id is not None else ""
                    safe_ping_id = str(ping_msg_id) if ping_msg_id is not None else ""
            
                    sheets_service.values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=f"Репорты!C{i}:D{i}",
                        valueInputOption="USER_ENTERED",
                        body={"values": [[safe_report_id, safe_ping_id]]}
                    ).execute()
            
                    logging.info(f"✏️ Обновлены message_id в строке {i}")
                    break
        
            if not found and report_msg:
                safe_report_id = str(report_msg_id) if report_msg_id is not None else ""
                safe_ping_id = str(ping_msg_id) if ping_msg_id is not None else ""
                new_row = [[
                    str(poll_id).strip(),
                    group_name_code,
                    safe_report_id,
                    safe_ping_id,
                    "", "", ""
                ]]
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Репорты!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_row}
                ).execute()
                logging.info(f"✅ Связка сообщений записана в Репорты (новая строка)")
        except Exception as e:
            logging.warning(f"❗ Ошибка при записи связки в Репорты: {e}")
    
        return report_msg_id, ping_msg_id

    except Exception as e:
        logging.warning(f"❗ Ошибка при отправке отчёта админу: {e}")
        
async def refresh_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(cache_time=1)
    _, poll_id = query.data.split("|")
    user = update.effective_user
    await notify_karina_action(context, user, f"🔄 Обновление отчёта\npoll_id={poll_id}")
    logging.info(f"🔄 Нажата кнопка обновления для poll_id={poll_id}")

    try:
        # 1️⃣ Берём строку с нужным poll_id из таблицы "Репорты"
        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Репорты!A2:G"
        ).execute()
        rows = resp.get("values", [])
        row = next((r for r in rows if r[0] == poll_id), None)

        if not row:
            await query.edit_message_text("❌ Не найдена связка в таблице Репорты.")
            return

        def safe_int(value):
            text = str(value).strip() if value is not None else ""
            if not text or text.lower() == "none":
                return None
            try:
                return int(text)
            except ValueError:
                return None

        group_name = row[1]
        report_message_id = safe_int(row[2]) if len(row) > 2 else None
        ping_message_id = safe_int(row[3]) if len(row) > 3 else None

        poll_to_group[poll_id] = {"name": group_name}

        # 2️⃣ Обновляем отчёт и получаем актуальные ID
        new_report_id, new_ping_id = await send_admin_report(
            app=context.application,
            poll_id=poll_id,
            report_message_id=report_message_id,
            ping_message_id=ping_message_id
        )


        # 3️⃣ Если message_id изменились — обновляем строку в таблице
        try:
            for i, r in enumerate(rows, start=2):  # начинаем с A2
                if r[0] == poll_id:
                    update_range = f"Репорты!C{i}:D{i}"

                    safe_report_id = str(new_report_id) if new_report_id is not None else ""
                    safe_ping_id = str(new_ping_id) if new_ping_id is not None else ""
                    
                    sheets_service.values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=update_range,
                        valueInputOption="RAW",
                        body={"values": [[safe_report_id, safe_ping_id]]}
                    ).execute()
                    logging.info(f"✏️ Обновлены message_id в строке {i} для poll_id={poll_id}")
                    break
        except Exception as e:
            logging.warning(f"❗ Ошибка при обновлении связки в Репорты: {e}")

        logging.info(f"✅ Обновление отчёта завершено: new_report_id={new_report_id}, new_ping_id={new_ping_id}")

    except Exception as e:
        logging.warning(f"❗ Ошибка в refresh_report_callback: {e}")

# Отправляем пинг сообщение в группу
async def notify_parents_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(cache_time=1)

    def safe_int(value):
        text = str(value).strip() if value is not None else ""
        if not text or text.lower() == "none":
            return None
        try:
            return int(text)
        except ValueError:
            return None

    try:
        _, poll_id = query.data.split("|")
        logging.info(f"📣 Нажата кнопка notify_parents для poll_id={poll_id}")
        user = update.effective_user
        await notify_karina_action(context, user, f"📣 Отправка родителям\npoll_id={poll_id}")

        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Репорты!A2:G"
        ).execute()
        rows = resp.get("values", [])

        row = next((r for r in rows if len(r) > 0 and r[0] == poll_id), None)

        if not row:
            await query.edit_message_text("❌ Не найдена строка в таблице Репорты.")
            return

        ping_message_id = safe_int(row[3]) if len(row) > 3 else None
        group_chat_id = safe_int(row[4]) if len(row) > 4 else None
        thread_id = safe_int(row[5]) if len(row) > 5 else None

        if not ping_message_id:
            await query.edit_message_text("❌ Не найден ping_message_id в таблице Репорты.")
            return

        if not group_chat_id:
            await query.edit_message_text("❌ Не найден group_chat_id в таблице Репорты.")
            return

        copy_kwargs = {
            "chat_id": group_chat_id,
            "from_chat_id": ADMIN_ID,
            "message_id": ping_message_id,
        }

        if row[5] if len(row) > 5 else "":
            copy_kwargs["message_thread_id"] = thread_id

        await context.bot.copy_message(**copy_kwargs)

        await query.edit_message_text("✅ Сообщение с тегами отправлено в группу.")
        logging.info(f"📨 Пинг скопирован в группу для poll_id={poll_id}")

    except Exception as e:
        logging.warning(f"❗ Ошибка в notify_parents_callback: {e}")
        try:
            await query.edit_message_text("❌ Ошибка при отправке сообщения в группу.")
        except Exception:
            pass
