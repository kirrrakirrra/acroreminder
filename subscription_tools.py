# subscription_tools.py

import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SERVICE_ACCOUNT_FILE = "service_account.json"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

SUBSCRIPTION_SHEETS = [
    "Группы 4-5",
    "Группы 6-9",
    "Взрослая группа",
]

DATA_RANGE = "A2:AH32"  # 2 строка = заголовки, 3-32 = данные

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
sheets_service = build("sheets", "v4", credentials=creds).spreadsheets()


def safe_get(row: List[str], idx: int, default: str = "") -> str:
    return row[idx].strip() if len(row) > idx and row[idx] else default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None

    formats = [
        "%d.%m.%Y",
        "%d/%m/%y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def normalize_subscription_type(raw_type: str) -> str:
    value = (raw_type or "").strip().lower()

    mapping = {
        "безлимит": "unlimited",
        "абон 8": "sub_8",
        "абон 5": "sub_5",
        "пробный абон 3": "sub_3_trial",
        "разово": "drop_in",
    }
    return mapping.get(value, value)


def get_header_map(header: List[str]) -> Dict[str, int]:
    return {name.strip(): idx for idx, name in enumerate(header) if name and name.strip()}


def get_visit_dates(row: List[str], header_map: Dict[str, int]) -> List[str]:
    visits = []

    for i in range(1, 16):
        col_name = f"Посещение {i}"
        idx = header_map.get(col_name)
        if idx is None:
            continue

        value = safe_get(row, idx)
        if value:
            visits.append(value)

    return visits


def load_all_subscriptions() -> List[Dict[str, Any]]:
    all_subscriptions: List[Dict[str, Any]] = []

    for sheet_name in SUBSCRIPTION_SHEETS:
        try:
            resp = sheets_service.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!{DATA_RANGE}"
            ).execute()
            rows = resp.get("values", [])
        except Exception as e:
            logging.warning(f"Не удалось прочитать вкладку {sheet_name}: {e}")
            continue

        if not rows:
            logging.info(f"Вкладка {sheet_name} пустая или недоступна")
            continue

        header = rows[0]
        header_map = get_header_map(header)
        data_rows = rows[1:]

        required_columns = [
            "Имя",
            "Группа",
            "User ID",
            "username",
            "Абонемент",
            "Лимит",
            "Used",
            "Дата Начала",
            "Срок Действия",
        ]
        missing = [col for col in required_columns if col not in header_map]
        if missing:
            logging.warning(
                f"Во вкладке {sheet_name} отсутствуют колонки: {', '.join(missing)}"
            )
            continue

        for row_offset, row in enumerate(data_rows, start=3):
            name = safe_get(row, header_map["Имя"])
            if not name:
                continue

            raw_user_id = safe_get(row, header_map["User ID"])
            raw_username = safe_get(row, header_map["username"])
            raw_sub_type = safe_get(row, header_map["Абонемент"])

            user_ids = [x.strip() for x in raw_user_id.split(",") if x.strip()]
            usernames = [
                x.strip().lstrip("@").lower()
                for x in raw_username.split(",")
                if x.strip()
            ]

            subscription = {
                "sheet_name": sheet_name,
                "row_number": row_offset,
                "name": name,
                "group": safe_get(row, header_map["Группа"]),
                "user_ids": user_ids,
                "usernames": usernames,
                "parent_name": safe_get(row, header_map.get("Имя родителя", -1)),
                "comment": safe_get(row, header_map.get("Коммент", -1)),
                "deposit": safe_get(row, header_map.get("Депозит", -1)),
                "days_of_week": safe_get(row, header_map.get("Дни недели", -1)),
                "subscription_type_raw": raw_sub_type,
                "subscription_type": normalize_subscription_type(raw_sub_type),
                "limit": to_int(safe_get(row, header_map["Лимит"]), 0),
                "used": to_int(safe_get(row, header_map["Used"]), 0),
                "start_date_raw": safe_get(row, header_map["Дата Начала"]),
                "end_date_raw": safe_get(row, header_map["Срок Действия"]),
                "start_date": parse_date(safe_get(row, header_map["Дата Начала"])),
                "end_date": parse_date(safe_get(row, header_map["Срок Действия"])),
                "unused": to_int(safe_get(row, header_map.get("Unused", -1)), 0),
                "wo_left_until_end": to_int(
                    safe_get(row, header_map.get("WO Left until end", -1)), 0
                ),
                "difference": safe_get(row, header_map.get("Difference", -1)),
                "days_until_end": safe_get(row, header_map.get("Days until end", -1)),
                "visit_dates": get_visit_dates(row, header_map),
                "warning_7": safe_get(row, header_map.get("warning_7", -1)),
            }

            all_subscriptions.append(subscription)

    return all_subscriptions

def has_7_days_warning(subscription):
    value = str(subscription.get("warning_7", "")).strip().lower()
    return value == "warning_7"
  
def find_user_subscriptions(
    all_subscriptions: List[Dict[str, Any]],
    telegram_user_id: int,
    telegram_username: Optional[str],
) -> List[Dict[str, Any]]:
    results = []

    normalized_username = ""
    if telegram_username:
        normalized_username = telegram_username.lstrip("@").lower()

    for sub in all_subscriptions:
        if str(telegram_user_id) in sub["user_ids"]:
            results.append(sub)
            continue

        if normalized_username and normalized_username in sub["usernames"]:
            results.append(sub)

    return results


def is_unlimited(subscription: Dict[str, Any]) -> bool:
    return subscription["subscription_type"] == "unlimited"


def is_drop_in(subscription: Dict[str, Any]) -> bool:
    return subscription["subscription_type"] == "drop_in"


def get_effective_limit(subscription: Dict[str, Any]) -> Optional[int]:
    if is_unlimited(subscription):
        return None

    limit_value = subscription.get("limit", 0)
    if limit_value > 0:
        return limit_value

    # запасной вариант, если в колонке "Лимит" пусто
    sub_type = subscription.get("subscription_type")
    fallback = {
        "sub_8": 8,
        "sub_5": 5,
        "sub_3_trial": 3,
        "drop_in": 1,
    }
    return fallback.get(sub_type)


def is_finished(subscription: Dict[str, Any]) -> bool:
    limit_value = get_effective_limit(subscription)
    if limit_value is None:
        return False
    return subscription.get("used", 0) >= limit_value


def is_expired(subscription: Dict[str, Any], today: Optional[datetime] = None) -> bool:
    if today is None:
        today = datetime.now()

    end_date = subscription.get("end_date")
    if not end_date:
        return False

    if is_unlimited(subscription):
        return end_date.date() < today.date()

    return end_date.date() < today.date() and not is_finished(subscription)


def format_usage(subscription: Dict[str, Any]) -> str:
    used = subscription.get("used", 0)

    if is_unlimited(subscription):
        return f"{used} (безлимит)"

    limit_value = get_effective_limit(subscription)
    if limit_value is None:
        return str(used)

    return f"{used} из {limit_value}"


def needs_attention(subscription: Dict[str, Any]) -> bool:
    """
    Общая проверка: есть ли повод показать предупреждение админу.
    Пока логика простая, потом можно расширить.
    """
    if is_unlimited(subscription):
        return is_expired(subscription)

    if is_expired(subscription):
        return True

    diff_raw = str(subscription.get("difference", "")).strip()
    if diff_raw in {"0", "1", "-1"}:
        return True

    return False
