from datetime import datetime, timedelta
from unittest.mock import patch

with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
    "googleapiclient.discovery.build"
):
    from subscription_alerts import (
        collect_alerts,
        make_alert_payload,
        parse_alert_payload,
        render_digest_parts,
        resolve_alert_subscription,
        subscription_fingerprint,
    )


def subscription(name="Анна", row=3, group="Группа A"):
    return {
        "sheet_name": "Группы 4-5", "row_number": row, "name": name,
        "group": group, "subscription_type": "sub_5",
        "subscription_type_raw": "Абон 5", "start_date_raw": "01.09.2026",
        "end_date_raw": "30.09.2026", "start_date": datetime.now() - timedelta(days=2),
        "unused": 1, "used": 4, "limit": 5, "visit_dates": ["01.09"],
        "days_until_end": "3", "warning_7": "", "deposit": "",
    }


def test_payload_is_compact_and_contains_no_student_data():
    item = subscription("Очень Секретное Имя")
    payload = make_alert_payload(item, "last_lesson")

    assert len(payload.encode()) <= 64
    assert "Очень" not in payload
    assert parse_alert_payload(payload) == (
        "Группы 4-5", 3, "last_lesson", subscription_fingerprint(item)
    )


def test_digest_groups_alerts_links_names_and_keeps_unpaid_independent():
    item = subscription()
    item["deposit"] = "Не оплачено 400"
    grouped = collect_alerts([item], ["Группа A"])
    parts = render_digest_parts(grouped, "acro_bot")

    assert list(grouped) == ["last_lesson", "unpaid"]
    assert len(parts) == 1
    assert "В абонементе осталось 1 занятие" in parts[0]
    assert "Неоплаченные абонементы" in parts[0]
    assert parts[0].count('href="https://t.me/acro_bot?start=') == 2
    assert "Анна</a> · Группа A" in parts[0]
    assert "не оплачено 400" in parts[0]


def test_digest_splits_only_at_limit_and_repeats_category_heading():
    items = [subscription(f"Ученик {number}", number + 3) for number in range(12)]
    grouped = collect_alerts(items, ["Группа A"])

    whole = render_digest_parts(grouped, "acro_bot", limit=4096)
    split = render_digest_parts(grouped, "acro_bot", limit=350)

    assert len(whole) == 1
    assert len(split) > 1
    assert all(len(part) <= 350 for part in split)
    assert all("В абонементе осталось 1 занятие" in part for part in split)


def test_moved_row_resolves_only_by_unique_fingerprint_and_reused_hint_is_safe():
    original = subscription("Анна", 3)
    moved = subscription("Анна", 9)
    reused = subscription("Другой ученик", 3)
    fingerprint = subscription_fingerprint(original)

    assert resolve_alert_subscription([reused, moved], "Группы 4-5", 3, fingerprint) is moved
    assert resolve_alert_subscription([reused], "Группы 4-5", 3, fingerprint) is None
    duplicate = dict(moved, row_number=10)
    assert resolve_alert_subscription(
        [reused, moved, duplicate], "Группы 4-5", 3, fingerprint
    ) is None
