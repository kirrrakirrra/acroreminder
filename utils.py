from datetime import datetime
import pytz

LOCAL_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

def now_local():
    return datetime.now(LOCAL_TZ)

def format_now():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")
