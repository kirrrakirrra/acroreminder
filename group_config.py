import os

GROUPS = [
    {
        "key": "junior_1715",
        "name": "Младшей группы",
        "sheet_group": "4-5 лет (17.15)",
        "display_name": "🐼 Младшей группы",
        "days": ["Tuesday", "Thursday"],
        "time": "17:15",
        "thread_id": 4,
        "group_id": os.getenv("GROUP_ID_45"),
        "check_day_offset": 0,
        "check_window": "day",
        "lesson_day_text": "сегодня",
        
    },
    {
        "key": "junior_1830",
        "name": "Младшей группы NEW",
        "sheet_group": "4-5 лет (18.30)",
        "display_name": "🐰 Младшей группы NEW",
        "days": ["Tuesday", "Thursday"],
        "time": "18:30",
        "thread_id": 5,
        "group_id": os.getenv("GROUP_ID_45"),
        "check_day_offset": 0,
        "check_window": "day",
        "lesson_day_text": "сегодня",
    },
    {
        "key": "69_beginner",
        "name": "Старшей начинающей группы",
        "sheet_group": "6-9 лет начинающие",
        "display_name": "⭐️ Старшей начинающей группы",
        "days": ["Monday", "Wednesday", "Friday"],
        "time": "17:15",
        "thread_id": 2225,
        "group_id": os.getenv("GROUP_ID_69"),
        "check_day_offset": 0,
        "check_window": "day",
        "lesson_day_text": "сегодня",
    },
    {
        "key": "69_pro",
        "name": "Старшей продолжающей группы",
        "sheet_group": "6-9 лет продолжающие",
        "display_name": "⚡️ Старшей продолжающей группы",
        "days": ["Monday", "Wednesday", "Friday"],
        "time": "18:30",
        "thread_id": 7,
        "group_id": os.getenv("GROUP_ID_69"),
        "check_day_offset": 0,
        "check_window": "day",
        "lesson_day_text": "сегодня",
    },
    {
        "key": "adult",
        "name": "Взрослой группы",
        "sheet_group": "Взрослая группа",
        "display_name": "Взрослой группы",
        "days": ["Tuesday", "Thursday"],
        "time": "10:00",
        "thread_id": None,
        #"thread_id": 105,
        "group_id": os.getenv("GROUP_ID_ADULT"),
        "check_day_offset": 1,
        "check_window": "evening",
        "lesson_day_text": "завтра",
    },
]

GROUP_NAME_MAP = {
    group["name"]: group["sheet_group"]
    for group in GROUPS
}

SCHEDULE_GROUPS = GROUPS
