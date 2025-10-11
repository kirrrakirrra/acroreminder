# utils.py
from datetime import datetime
import pytz

# Возвращает текущее время во Вьетнаме (Asia/Ho_Chi_Minh, GMT+7)
def get_now_local():
    return datetime.now(pytz.timezone("Asia/Ho_Chi_Minh"))
