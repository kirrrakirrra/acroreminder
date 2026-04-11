import os

GROUPS = [
    {
        "key": "junior_1715",
        "label": "Младшей группы",
        "sheet_group": "4-5 лет (17.15)",
        "display_name": "4-5 лет (17.15)",
        "days": ["Tuesday", "Thursday"],
        "time": "17:15",
        "thread_id": 4,
        "group_id": os.getenv("GROUP_ID_45"),
    },
    {
        "key": "junior_1830",
        "label": "Младшей группы NEW",
        "sheet_group": "4-5 лет (18.30)",
        "display_name": "4-5 лет (18.30)",
        "days": ["Tuesday", "Thursday"],
        "time": "18:30",
        "thread_id": 5,
        "group_id": os.getenv("GROUP_ID_45"),
    },
    {
        "key": "69_beginner",
        "label": "Старшей начинающей группы",
        "sheet_group": "6-9 лет начинающие",
        "display_name": "6-9 лет начинающие",
        "days": ["Monday", "Wednesday", "Friday"],
        "time": "17:15",
        "thread_id": 2225,
        "group_id": os.getenv("GROUP_ID_69"),
    },
    {
        "key": "69_pro",
        "label": "Старшей продолжающей группы",
        "sheet_group": "6-9 лет продолжающие",
        "display_name": "6-9 лет продолжающие",
        "days": ["Monday", "Wednesday", "Friday"],
        "time": "18:30",
        "thread_id": 7,
        "group_id": os.getenv("GROUP_ID_69"),
    },
    {
        "key": "adult",
        "label": "Взрослая группа",
        "sheet_group": "Взрослая группа",
        "display_name": "Взрослая группа",
        "days": ["Tuesday", "Thursday"],
        "time": "10:00",
        "thread_id": None,
        "group_id": os.getenv("GROUP_ID_ADULT"),
    },
]

GROUP_NAME_MAP = {
    group["label"]: group["sheet_group"]
    for group in GROUPS
}
