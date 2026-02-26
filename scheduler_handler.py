from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from reminder_handler import poll_to_group
from reminder_handler import schedule_report
from utils import now_local,format_now
from datetime import datetime
import asyncio
import os
import logging

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # переменная должна быть в Render Environment
SHEET_RANGE = 'Абонементы!A1:W'  # до колонки W включительно

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build('sheets', 'v4', credentials=creds).spreadsheets()

ADMIN_ID = os.getenv("ADMIN_ID")
GROUP_ID = os.getenv("GROUP_ID")

check_hour = int(os.getenv("CHECK_HOUR", 11))
min_start = int(os.getenv("CHECK_MIN_1START", 1))
min_end = int(os.getenv("CHECK_MIN_END", 3))

# Список групп
groups = [
    {
        "name": "Старшей начинающей группы",
        "days": ["Monday", "Wednesday", "Friday",],
        "time": "17:15",
        "thread_id": 2225,
        # "thread_id": 105,
    },
    {
        "name": "Старшей продолжающей группы",
        "days": ["Monday", "Wednesday", "Friday",],
        "time": "18:30",
        "thread_id": 7,
        # "thread_id": 362,
    },
    {
        "name": "Младшей группы",
        "days": ["Tuesday", "Thursday",],
        "time": "17:15",
        "thread_id": 2226,
        # "thread_id": 362,
    },
     {
        "name": "Младшей группы NEW",
        "days": ["Tuesday", "Thursday",],
        "time": "18:30",
        "thread_id": 4389,
        # "thread_id": 362,
    },
]

pending = {}

def get_decision_keyboard(group_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data=f"yes|{group_id}")],
        [InlineKeyboardButton("❌ Нет, я сам напишу в группу", callback_data=f"skip|{group_id}")],
    ])
# ------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------
async def check_expired_subscriptions(app, today_group_names):
    print("🔍 check_expired_subscriptions запущена")
    logging.info("🔍 check_expired_subscriptions запущена")

    try:
        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=SHEET_RANGE
        ).execute()
        rows = resp.get('values', [])
        if not rows or len(rows) < 2:
            print("⛔️ Таблица пуста или недоступна.")
            logging.warning("⛔️ Таблица пуста или недоступна.")
            return

        header = rows[0]
        try:
            idx_name = header.index("Имя ребёнка")
            idx_group = header.index("Группа")
            idx_used = header.index("Использованно")
            idx_end = header.index("Срок действия")
            idx_diff = header.index("Разница")
            idx_remaining = header.index("Осталось календарных занятий")
            idx_used_left = header.index("Осталось занятий")
            idx_pause = header.index("Пауза")

        except ValueError as e:
            print(f"⛔️ Колонка не найдена: {e}")
            logging.warning(f"⛔️ Колонка не найдена: {e}")
            return

        logging.info(f"🔎 Группы, которые проверяются сегодня: {today_group_names}")
        from collections import defaultdict
        usage_by_name = defaultdict(list)

        for row in rows[1:]:
            name = row[idx_name] if len(row) > idx_name else ""
            used = row[idx_used] if len(row) > idx_used else ""
            group = row[idx_group] if len(row) > idx_group else ""

            if not name or group not in today_group_names:
                continue

            try:
                used_num = int(used)
            except:
                used_num = 0

            usage_by_name[name].append({
                "used": used_num,
                "group": group
            })

        found = False
        for name, subs in usage_by_name.items():
            finished = [s for s in subs if s["used"] == 8]
            not_finished = [s for s in subs if s["used"] < 8]
        
            # ✅ Абонемент завершён (и нет другого активного)
            if finished and not not_finished:
                for sub in finished:
                     # Ищем первую строку с этим именем и группой
                    for row in rows[1:]:
                        row_name = row[idx_name] if len(row) > idx_name else ""
                        row_group = row[idx_group] if len(row) > idx_group else ""
                        if row_name == name and row_group == sub["group"]:
                            # Даты посещений: колонки F–M → индексы 5–12
                            dates = [row[i] for i in range(5, 13) if i < len(row) and row[i].strip()]
                            dates_text = "\n".join([f"• {d}" for d in dates]) if dates else "—"
        
                            msg = (
                                f"✅ *Абонемент завершён*:\n"
                                f"👤 *Имя*: {name}\n"
                                f"🏷️ *Группа*: {sub['group']}\n"
                                f"☑️ *Использовано*: 8 из 8\n"
                                f"📅 *Даты посещений*:\n{dates_text}"
                            )
        
                            print(f"📤 Отправка сообщения: {msg}")
                            logging.info(f"📤 Отправка сообщения: {msg}")
                            await app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
                            found = True
                            break  # нашли нужную строку
        
            # ⚠️ Абонементы не завершены, но с рисками
            elif not_finished:
                for sub in not_finished:
                    for row in rows[1:]:
                        row_name = row[idx_name] if len(row) > idx_name else ""
                        row_group = row[idx_group] if len(row) > idx_group else ""
                        if row_name == name and row_group == sub["group"]:
                            end = row[idx_end] if len(row) > idx_end else ""
                            used = row[idx_used] if len(row) > idx_used else "0"
        
                            # Проверка срока действия
                            expired_warning = ""
                            for fmt in ["%d.%m.%Y", "%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"]:
                                try:
                                    # end_date = datetime.datetime.strptime(end, fmt)
                                    # if end_date.date() < datetime.datetime.now().date() and int(used) < 8:
                                    end_date = datetime.strptime(end, fmt)
                                    if end_date.date() < now_local().date() and int(used) < 8:

                                        expired_warning = f"‼️ *Срок действия абонемента закончился {end}*"
                                    break
                                except ValueError:
                                    continue
        
                            # Проверка дефицита календарных занятий
                            diff_info = ""
                            if len(row) > idx_diff:
                                try:
                                    diff_value = int(row[idx_diff].strip())
                                    if diff_value in (0, 1):
                                        used_left = row[idx_used_left].strip() if len(row) > idx_used_left else "—"
                                        remaining = row[idx_remaining].strip() if len(row) > idx_remaining else "—"
                                        diff_info = (
                                            f"⚠️ Осталось *{used_left}* неиспользованных занятий, "
                                            f"а до конца срока — *{remaining}* календарных тренировок."
                                        )
                                except ValueError:
                                    pass  # если в diff записано не число — просто игнорируем
                                    
                            # ⏸️ Проверка на паузу
                            on_pause = row[idx_pause].strip().upper() == "TRUE" if len(row) > idx_pause else False
                            pause_text = "\n⏸️ *На паузе*" if on_pause else ""
        
                            # Если есть хоть одна проблема — отправляем
                            if expired_warning or diff_info:
                                msg = (
                                    f"⚠️ *Абонемент требует внимания:*\n"
                                    f"👤 *Имя:* {name}\n"
                                    f"🏷️ *Группа:* {sub['group']}\n"
                                    f"☑️ *Использовано:* {used} из 8\n"
                                    f"{diff_info}\n\n{expired_warning}{pause_text}".strip()
                                )
        
                                print(f"📤 Отправка сообщения: {msg}")
                                logging.info(f"📤 Отправка сообщения: {msg}")
                                await app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
                                found = True
                                break  # прекращаем после первой подходящей строки
        
        if not found:
            logging.info("✅ Нет завершённых или рискованных абонементов для отправки.")
    except Exception as e:
        logging.warning(f"❗️ Ошибка при проверке завершённых абонементов: {e}")

# -----------------------------------------------------------------------------
# ------------------------------------------------------------------------------------

async def ask_admin(app, group_id, group):
    msg = await app.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Сегодня занятие для {group['name']} в {group['time']} по расписанию?",
        reply_markup=get_decision_keyboard(group_id)
    )
    pending[msg.message_id] = group

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    action = data[0]
    group_id = int(data[1])
    group = groups[group_id]

    if action == "yes":
        # Сообщение-объявление
        await context.bot.send_message(
            chat_id=GROUP_ID,
            message_thread_id=group["thread_id"],
            text=f"Доброго дня! Занятие для {group['name']} по расписанию в {group['time']} 🤸🏻🤸🏻‍♀️"
        )
    
        # Опрос
        try:
            poll_msg = await context.bot.send_poll(
                chat_id=GROUP_ID,
                question="Кто будет сегодня на занятии?",
                options=["✅ Будем по абонементу", "🤸🏻‍♀️ Будем разово", "❌ Пропускаем"],
                is_anonymous=False,
                allows_multiple_answers=False,
                message_thread_id=group["thread_id"],
            )

             # Сохраняем poll_id и название группы в Google Sheets (вкладка "Опросы")
            try:
                options_text = "|".join([opt.text for opt in poll_msg.poll.options])
                new_row = [[
                    poll_msg.poll.id,
                    group["name"],
                    "", "", format_now(), "", options_text  # пустые ячейки под user_id, username, время и ответ
                ]]
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Опросы!A1",  # ⬅️ явное указание вкладки
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_row}
                ).execute()
            except Exception as e:
                logging.warning(f"❗ Не удалось записать poll_id: {e}")

            context.bot_data[poll_msg.poll.id] = poll_msg.poll.options  
            
            # 1. Отправили опрос → запланировать отчет
            poll_to_group[poll_msg.poll.id] = group
            
            # 2. Записать запланированный отчёт в таблицу "Репорты"
            try:
                new_row = [[
                    poll_msg.poll.id,
                    group["name"],
                    "",  # report_message_id — появится после отправки
                    "",  # ping_message_id — появится после отправки
                    "",  #group["chat_id"],     # вставим сразу
                    "",  #group["thread_id"],   # вставим сразу
                    now_local().strftime("%Y-%m-%d")  # дата — удобно для /report
                ]]
                sheets_service.values().append(
                    spreadsheetId=SPREADSHEET_ID,
                    range="Репорты!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_row}
                ).execute()
                logging.info("✅ Запланированный отчет записан в таблицу Репорты")
            except Exception as e:
                logging.warning(f"❗ Не удалось записать запланированный отчет: {e}")

            context.application.create_task(schedule_report(context.application, group, poll_msg.poll.id))
        
        except Exception as e:
            logging.warning(f"❗ Не удалось отправить опрос: {e}")
        
        finally:
            # 🔧 Убираем кнопки В ЛЮБОМ СЛУЧАЕ
            try:
                logging.info("🧼 Убираем кнопки после ответа администратора")
                await query.edit_message_text("Напоминание и опрос отправлены ✅")
            except Exception as e:
                logging.warning(f"⚠️ Не удалось изменить сообщение с кнопками: {e}")
        # await query.edit_message_text("Напоминание и опрос отправлены ✅")
    elif action == "skip":
        await query.edit_message_text("❌ Окей, ничего не публикуем.\nНапоминание: не забудьте сами сообщить группе о деталях отмены")
    pass
# -----------------------------------------------------------------------------
# ------------------------------------------------------------------------------------    
async def scheduler(app):
    await asyncio.sleep(30)  # даём Render время на перезапуск
    last_check = None
    last_expiry_check = None

    while True:
        try:
            now = now_local()
            # now_utc = datetime.datetime.utcnow()------------  проверить потом убрать
            # now = now_utc + datetime.timedelta(hours=7)------------проверить потом убрать
            weekday = now.strftime("%A")
            current_time = now.strftime("%H:%M")

            logging.info(f"[scheduler] Сейчас {current_time} {weekday}")
            logging.info(f"[scheduler] CHECK_HOUR={check_hour}, MIN={min_start}–{min_end}")

            # 🔁 Опрос администратора в 11:00
            # if now.hour == 11 and 1 <= now.minute <= 3:
            if now.hour == check_hour and min_start <= now.minute <= min_end:
                if last_check != now.date():
                    logging.info("[scheduler] Время для опроса — запускаем")
                    for idx, group in enumerate(groups):
                        if weekday in group["days"]:
                            await ask_admin(app, idx, group)
                    last_check = now.date()
                else:
                    logging.info("[scheduler] Уже запускали сегодня")

            # 📋 Проверка завершённых абонементов в 12:15
            if now.hour == 12 and 1 <= now.minute <= 4:
                if last_expiry_check != now.date():
                    logging.info("[scheduler] Проверяем абонементы на завершение...")

                    # Словарь соответствия: название в коде -> название в таблице
                    group_name_map = {
                        "Старшей начинающей группы": "6-9 лет начинающие",
                        "Старшей продолжающей группы": "6-9 лет продолжающие",
                        "Младшей группы": "4-5 лет", 
                        "Младшей группы NEW": "4-5 лет NEW",
                    }
        
                    # Преобразуем названия из кода в названия таблицы
                    today_groups = [
                        group_name_map.get(group["name"])
                        for group in groups
                        if weekday in group["days"]
                    ]
                    await check_expired_subscriptions(app, today_groups)
                    last_expiry_check = now.date()
                else:
                    logging.info("[scheduler] Проверка абонементов уже была сегодня")

            await asyncio.sleep(20)

        except Exception as e:
            logging.error(f"[scheduler] Ошибка: {e}")
            await asyncio.sleep(10)
