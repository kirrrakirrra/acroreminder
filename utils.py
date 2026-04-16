# utils.py
from datetime import datetime
import pytz
import os
import logging


KARINA_ID = os.getenv("KARINA_ID")

LOCAL_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

def now_local():
    """Возвращает текущее локальное время во Вьетнаме (Asia/Ho_Chi_Minh, GMT+7)"""
    return datetime.now(LOCAL_TZ)

def format_now():
    """Возвращает строку текущего времени в формате '2025-10-11 15:10:00'"""
    return now_local().strftime("%Y-%m-%d %H:%M:%S")

async def notify_karina_action(context, user, action_text: str):
    """
    Отправляет Карине уведомление о действии пользователя.
    Если действие выполняет сама Карина — ничего не отправляет.
    """
    try:
        if not KARINA_ID:
            return

        user_id = str(user.id)
        if user_id == str(KARINA_ID):
            return

        username = f"@{user.username}" if user.username else "(без username)"
        full_name = user.full_name or "Без имени"

        await context.bot.send_message(
            chat_id=KARINA_ID,
            text=f"{action_text}\n от {full_name} ({username}) [ID: {user.id}]"
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление Карине: {e}")
