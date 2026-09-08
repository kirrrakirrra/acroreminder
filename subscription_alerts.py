"""Trainer-only subscription alert digests and deep-link cards."""

import base64
import hashlib
import html
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from subscription_tools import (
    SUBSCRIPTION_SHEETS,
    format_usage,
    get_subscription_alert_status,
    is_started_subscription,
    parse_unpaid_payment,
)

ALERT_TITLES = {
    "expired": "📛 Срок действия абонемента истёк",
    "finished": "❌ Абонемент завершён",
    "no_calendar_lessons": "⛔️ Абонемент завершён по расписанию",
    "last_calendar_lesson_today": "🚨 Сегодня финальный день абонемента",
    "last_calendar_lesson": "❕🗓️ Осталось одно занятие в рамках абонемента",
    "last_lesson": "❕ В абонементе осталось 1 занятие",
    "warning_7": "⏳ До конца абонемента осталось менее 7 дней",
    "unpaid": "💳 Неоплаченные абонементы",
}
ALERT_EXPLANATIONS = {
    "expired": "Срок действия уже истёк. Нужен следующий абонемент.",
    "finished": "Лимит занятий использован. Нужен следующий абонемент.",
    "no_calendar_lessons": "По расписанию в срок абонемента больше не входит занятий.",
    "last_calendar_lesson_today": "Сегодня последнее занятие в сроке абонемента.",
    "last_calendar_lesson": "По расписанию в срок абонемента входит ещё одно занятие.",
    "last_lesson": "По лимиту абонемента осталось одно занятие.",
    "warning_7": "До окончания срока осталось менее семи дней.",
    "unpaid": "Оплата текущего абонемента требует внимания.",
}
ALERT_CODES = {
    "expired": "e", "finished": "f", "no_calendar_lessons": "n",
    "last_calendar_lesson_today": "t", "last_calendar_lesson": "c",
    "last_lesson": "l", "warning_7": "w", "unpaid": "u",
}
CODE_ALERTS = {value: key for key, value in ALERT_CODES.items()}
PAYLOAD_PREFIX = "sa1"
TELEGRAM_MESSAGE_LIMIT = 4096


def is_alert_eligible(subscription: Dict) -> bool:
    raw = str(subscription.get("subscription_type_raw", "")).strip()
    kind = subscription.get("subscription_type")
    return bool(raw and kind and kind != "drop_in" and raw.lower() != "разово")


def subscription_fingerprint(subscription: Dict) -> str:
    """Fingerprint stable identity fields; the row number is deliberately excluded."""
    identity = "\x1f".join(str(subscription.get(field, "")).strip().casefold() for field in (
        "sheet_name", "name", "group", "start_date_raw", "subscription_type_raw"
    ))
    digest = hashlib.sha256(identity.encode("utf-8")).digest()[:7]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number < 0:
        raise ValueError("negative value")
    result = "0"
    if number:
        result = ""
        while number:
            number, remainder = divmod(number, 36)
            result = alphabet[remainder] + result
    return result


def make_alert_payload(subscription: Dict, alert_type: str) -> str:
    sheet_code = _base36(SUBSCRIPTION_SHEETS.index(subscription["sheet_name"]))
    row_code = _base36(int(subscription["row_number"]))
    payload = ".".join((PAYLOAD_PREFIX, sheet_code, row_code,
                        ALERT_CODES[alert_type], subscription_fingerprint(subscription)))
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("Telegram start payload is too long")
    return payload


def parse_alert_payload(payload: str) -> Optional[Tuple[str, int, str, str]]:
    if len(payload.encode("utf-8")) > 64:
        return None
    try:
        prefix, sheet_code, row_code, alert_code, fingerprint = payload.split(".")
        sheet_index = int(sheet_code, 36)
        row_number = int(row_code, 36)
        alert_type = CODE_ALERTS[alert_code]
    except (ValueError, KeyError, IndexError):
        return None
    if (prefix != PAYLOAD_PREFIX or row_number < 3 or len(fingerprint) != 10
            or not 0 <= sheet_index < len(SUBSCRIPTION_SHEETS)):
        return None
    sheet_name = SUBSCRIPTION_SHEETS[sheet_index]
    return sheet_name, row_number, alert_type, fingerprint


def collect_alerts(subscriptions: Iterable[Dict], group_names: Iterable[str]) -> Dict[str, List[Dict]]:
    selected = set(group_names)
    grouped = {alert_type: [] for alert_type in ALERT_TITLES}
    for subscription in subscriptions:
        if subscription.get("group") not in selected or not is_alert_eligible(subscription):
            continue
        primary = get_subscription_alert_status(subscription)
        if primary in ALERT_TITLES and primary != "unpaid":
            grouped[primary].append(subscription)
        if (parse_unpaid_payment(subscription.get("deposit"))
                and is_started_subscription(subscription)):
            grouped["unpaid"].append(subscription)
    return {key: value for key, value in grouped.items() if value}


def alert_still_applies(subscription: Dict, alert_type: str) -> bool:
    if not is_alert_eligible(subscription):
        return False
    if alert_type == "unpaid":
        return bool(parse_unpaid_payment(subscription.get("deposit"))
                    and is_started_subscription(subscription))
    return get_subscription_alert_status(subscription) == alert_type


def resolve_alert_subscription(subscriptions: Iterable[Dict], sheet_name: str,
                               row_number: int, fingerprint: str) -> Optional[Dict]:
    sheet_rows = [sub for sub in subscriptions if sub.get("sheet_name") == sheet_name]
    hinted = next((sub for sub in sheet_rows if sub.get("row_number") == row_number), None)
    if hinted and subscription_fingerprint(hinted) == fingerprint:
        return hinted
    matches = [sub for sub in sheet_rows if subscription_fingerprint(sub) == fingerprint]
    return matches[0] if len(matches) == 1 else None


def _context(subscription: Dict, alert_type: str) -> str:
    if alert_type == "unpaid":
        return parse_unpaid_payment(subscription.get("deposit")) or "не оплачено"
    if alert_type in {"finished", "last_lesson", "no_calendar_lessons",
                      "last_calendar_lesson_today", "last_calendar_lesson"}:
        return format_usage(subscription)
    end = str(subscription.get("end_date_raw", "")).strip()
    return f"до {end}" if end else "срок требует внимания"


def _digest_row(subscription: Dict, alert_type: str, bot_username: str,
                max_length: Optional[int] = None) -> str:
    payload = make_alert_payload(subscription, alert_type)
    url = f"https://t.me/{quote(bot_username.lstrip('@'))}?start={payload}"
    values = [str(subscription.get("name", "—")),
              str(subscription.get("group", "—")), _context(subscription, alert_type)]

    def render(cap: Optional[int] = None) -> str:
        visible = values if cap is None else [
            value if len(value) <= cap else value[:max(0, cap - 1)] + "…"
            for value in values
        ]
        name, group, context = (html.escape(value) for value in visible)
        return f'• <a href="{html.escape(url, quote=True)}">{name}</a> · {group} · {context}'

    row = render()
    if max_length is None or len(row) <= max_length:
        return row
    # Shorten source text before escaping so tags and entities always stay complete.
    low, high = 0, max(map(len, values))
    while low < high:
        middle = (low + high + 1) // 2
        if len(render(middle)) <= max_length:
            low = middle
        else:
            high = middle - 1
    row = render(low)
    if len(row) > max_length:
        raise ValueError("Digest limit is too small for a complete student link")
    return row


def render_digest_parts(grouped: Dict[str, List[Dict]], bot_username: str,
                        limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    """Pack category blocks, repeating headers only when a category must be split."""
    if not grouped:
        return []
    page_header = "🎟 <b>Абонементы</b>"
    pages: List[str] = []
    current = page_header
    for alert_type in ALERT_TITLES:
        subscriptions = grouped.get(alert_type, [])
        if not subscriptions:
            continue
        heading = f"<b>{html.escape(ALERT_TITLES[alert_type])}</b>"
        row_limit = limit - len(page_header) - len(heading) - 4
        rows = [_digest_row(sub, alert_type, bot_username, row_limit)
                for sub in subscriptions]
        block = heading
        for row in rows:
            addition = "\n" + row
            candidate = current + "\n\n" + block + addition
            if len(candidate) <= limit:
                block += addition
                continue
            if block != heading:
                current += "\n\n" + block
                pages.append(current)
                current, block = page_header, heading + addition
            else:
                if current != page_header:
                    pages.append(current)
                    current = page_header
                block = heading + "\n" + row
        if len(current + "\n\n" + block) > limit and current != page_header:
            pages.append(current)
            current = page_header + "\n\n" + block
        else:
            current += "\n\n" + block
    if current != page_header:
        pages.append(current)
    return pages


def build_trainer_alert_card(subscription: Dict, alert_type: str) -> str:
    visits = subscription.get("visit_dates") or []
    dates = "\n".join(f"{i}. {html.escape(str(date))}" for i, date in enumerate(visits, 1)) or "—"
    lines = [
        f"<b>{html.escape(ALERT_TITLES[alert_type])}</b>",
        "",
        f"👤 <b>Имя:</b> {html.escape(str(subscription.get('name', '—')))}",
        f"🏷️ <b>Группа:</b> {html.escape(str(subscription.get('group', '—')))}",
        f"🧾 <b>Абонемент:</b> {html.escape(str(subscription.get('subscription_type_raw', '—')))}",
        f"📆 <b>Срок действия:</b> {html.escape(str(subscription.get('start_date_raw', '—')))} — {html.escape(str(subscription.get('end_date_raw', '—')))}",
        f"☑️ <b>Использовано:</b> {html.escape(format_usage(subscription))}",
        f"📅 <b>Даты посещений:</b>\n{dates}",
    ]
    lines.append(f"\n<b>Ситуация:</b> {html.escape(ALERT_EXPLANATIONS[alert_type])}")
    if alert_type == "warning_7" and subscription.get("days_until_end"):
        lines.append(f"⏳ Осталось дней: {html.escape(str(subscription['days_until_end']))}")
    unpaid_status = parse_unpaid_payment(subscription.get("deposit"))
    if unpaid_status and is_started_subscription(subscription):
        lines.append(f"\n💳 <b>Оплата:</b> ⚠️ {html.escape(unpaid_status)}")
    return "\n".join(lines)
