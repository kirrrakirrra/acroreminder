import asyncio
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
    "googleapiclient.discovery.build"
):
    from subscription_alerts import make_alert_payload


def subscription():
    return {
        "sheet_name": "Группы 4-5", "row_number": 3, "name": "Анна",
        "group": "Группа A", "subscription_type": "sub_5",
        "subscription_type_raw": "Абон 5", "start_date_raw": "01.09.2026",
        "end_date_raw": "30.09.2026", "start_date": datetime.now() - timedelta(days=2),
        "unused": 1, "used": 4, "limit": 5, "visit_dates": ["01.09"],
        "days_until_end": "3", "warning_7": "", "deposit": "не оплачено 400",
    }


def import_handler(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("KARINA_ID", "2")
    sys.modules.pop("start_handler", None)
    with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
        "googleapiclient.discovery.build"
    ):
        import start_handler
    return start_handler


def update(user_id):
    return SimpleNamespace(
        effective_user=SimpleNamespace(
            id=user_id, username="trainer", full_name="Trainer"
        ),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


@pytest.mark.parametrize("user_id", [1, 2])
def test_admin_and_karina_can_open_current_alert_card(monkeypatch, user_id):
    handler = import_handler(monkeypatch)
    item = subscription()
    monkeypatch.setattr(handler, "load_all_subscriptions", Mock(return_value=[item]))
    request = update(user_id)

    asyncio.run(handler.start_command(
        request, SimpleNamespace(args=[make_alert_payload(item, "last_lesson")])
    ))

    assert request.message.reply_text.await_args.args[0] == "⏳ Собираю информацию…"
    result = request.message.reply_text.return_value.edit_text
    assert "Анна" in result.await_args.args[0]
    assert "не оплачено 400" in result.await_args.args[0]
    assert result.await_args.kwargs["parse_mode"] == "HTML"


def test_unauthorized_and_stale_links_do_not_expose_card(monkeypatch):
    handler = import_handler(monkeypatch)
    item = subscription()
    payload = make_alert_payload(item, "last_lesson")
    loader = Mock(return_value=[item])
    monkeypatch.setattr(handler, "load_all_subscriptions", loader)
    unauthorized = update(99)
    asyncio.run(handler.start_command(unauthorized, SimpleNamespace(args=[payload])))
    assert unauthorized.message.reply_text.await_args.args[0] == (
        "Карточка недоступна или ситуация уже изменилась."
    )
    loader.assert_not_called()  # authorization happens before Sheets access

    item["unused"] = 2
    stale = update(1)
    asyncio.run(handler.start_command(stale, SimpleNamespace(args=[payload])))
    assert stale.message.reply_text.await_args.args[0] == "⏳ Собираю информацию…"
    assert stale.message.reply_text.return_value.edit_text.await_args.args[0] == (
        "Карточка недоступна или ситуация уже изменилась."
    )


def test_old_or_malformed_alert_prefix_does_not_enter_alert_flow(monkeypatch):
    handler = import_handler(monkeypatch)
    save = AsyncMock()
    monkeypatch.setattr(handler, "save_user_if_new", save)
    request = update(1)

    asyncio.run(handler.start_command(request, SimpleNamespace(args=["sa1.garbage"])))

    save.assert_awaited_once()
    assert request.message.reply_text.await_args.args[0].startswith("Привет!")



def test_trainer_alert_shows_progress_before_sheets_load(monkeypatch):
    handler = import_handler(monkeypatch)
    item = subscription()
    request = update(1)

    async def delayed_load(loader, sheet_names):
        assert request.message.reply_text.await_count == 1
        assert request.message.reply_text.await_args.args[0] == "⏳ Собираю информацию…"
        return [item]

    monkeypatch.setattr(handler.asyncio, "to_thread", delayed_load)
    asyncio.run(handler.start_command(
        request, SimpleNamespace(args=[make_alert_payload(item, "last_lesson")])
    ))
    request.message.reply_text.return_value.edit_text.assert_awaited_once()


def test_failed_trainer_alert_replaces_progress_with_unavailable(monkeypatch):
    handler = import_handler(monkeypatch)
    item = subscription()

    def broken_loader(_sheet_names):
        raise RuntimeError("Sheets unavailable")

    monkeypatch.setattr(handler, "load_all_subscriptions", broken_loader)
    request = update(1)
    asyncio.run(handler.start_command(
        request, SimpleNamespace(args=[make_alert_payload(item, "last_lesson")])
    ))
    assert request.message.reply_text.return_value.edit_text.await_args.args[0] == (
        "Карточка недоступна или ситуация уже изменилась."
    )
