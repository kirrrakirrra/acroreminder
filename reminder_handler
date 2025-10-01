import asyncio
import logging
from telegram.constants import ParseMode

# Временное хранилище голосов: {poll_id: set(user_ids)}
poll_votes = {}

# Храним связь между poll_id и группой
poll_to_group = {}

# Добавляется при регистрации handler'а PollAnswer
async def handle_poll_answer(update, context):
    poll_id = update.poll_answer.poll_id
    user_id = update.poll_answer.user.id

    if poll_id not in poll_votes:
        poll_votes[poll_id] = set()
    poll_votes[poll_id].add(user_id)

# Планируем напоминание через 60 минут
async def schedule_reminder(app, group, poll_id):
    poll_to_group[poll_id] = group
    await asyncio.sleep(60 * 60)  # 1 час ожидания
    await send_nonresponders_reminder(app, poll_id)

# Сравниваем абонементов и отметившихся
async def send_nonresponders_reminder(app, poll_id):
    group = poll_to_group.get(poll_id)
    if not group:
        logging.warning(f"❗ Не найдена группа для poll_id {poll_id}")
        return

    try:
        from scheduler_handler import sheets_service, SPREADSHEET_ID, SHEET_RANGE

        resp = sheets_service.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=SHEET_RANGE
        ).execute()
        rows = resp.get("values", [])
        if len(rows) < 2:
            return

        header = rows[0]
        idx_usercol = header.index("username")
        idx_group = header.index("Группа")
        idx_pause = header.index("Пауза") if "Пауза" in header else None

        group_name = group["name"]
        group_names_map = {
            "Старшей начинающей группы": "6-9 лет начинающие",
            "Старшей продолжающей группы": "6-9 лет продолжающие",
            "Младшей группы": "4-5 лет",
        }
        group_value = group_names_map.get(group_name)

        mentions = []
        for row in rows[1:]:
            if len(row) <= max(idx_usercol, idx_group):
                continue
            group_cell = row[idx_group].strip()
            if group_cell != group_value:
                continue

            username = row[idx_usercol].strip().lstrip("@").lower()
            if not username:
                continue

            pause = row[idx_pause].strip().upper() if idx_pause and len(row) > idx_pause else ""
            if pause == "TRUE":
                continue

            # Получаем user_id по username нельзя напрямую — поэтому упрощённо:
            # мы предполагаем, что poll_votes содержит user_id голосовавших
            # и нам нужно найти, чьего username там нет
            # Это ограничение можно решить, если ты хранишь user_id и username заранее
            
            # Пока: просто соберём всех, т.к. нет связи username ↔ user_id
            mentions.append(f"@{username}")

        # Исключаем тех, кто проголосовал
        voted_ids = poll_votes.get(poll_id, set())
        if voted_ids:
            # В будущем можно соотносить user_id ↔ username по стартовой команде /start
            logging.info("🟡 Опрос был, но нет точной связи username ↔ user_id")

        if mentions:
            text = (
                "⏰ *Напоминание!*
Кто-то из вас ещё не отметил участие в сегодняшнем занятии. Пожалуйста, отметьтесь в опросе выше 👆\n\n"
                + " ".join(mentions)
            )
            await app.bot.send_message(
                chat_id=group["thread_id"],
                message_thread_id=group["thread_id"],
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            logging.info(f"✅ Все участники из {group['name']} отметились")

    except Exception as e:
        logging.warning(f"❗ Ошибка в send_nonresponders_reminder: {e}")
