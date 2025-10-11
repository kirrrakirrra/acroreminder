# utils.py
from datetime import datetime
import pytz

LOCAL_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

def now_local():
    """Возвращает текущее локальное время во Вьетнаме (Asia/Ho_Chi_Minh, GMT+7)"""
    return datetime.now(LOCAL_TZ)

def format_now():
    """Возвращает строку текущего времени в формате '2025-10-11 15:10:00'"""
    return now_local().strftime("%Y-%m-%d %H:%M:%S")
