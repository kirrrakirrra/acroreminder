import asyncio
import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# The production module constructs the synchronous Google client at import time.
with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
    "googleapiclient.discovery.build"
) as build:
    build.return_value.spreadsheets.return_value = Mock()
    import subscription_tools


def import_check_handler(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("GROUP_ID_45", "-45")
    monkeypatch.setenv("GROUP_ID_69", "-69")
    monkeypatch.setenv("GROUP_ID_ADULT", "-100")
    import group_config
    importlib.reload(group_config)
    scheduler = SimpleNamespace(check_expired_subscriptions=AsyncMock(), groups=group_config.GROUPS)
    monkeypatch.setitem(sys.modules, "scheduler_handler", scheduler)
    sys.modules.pop("check_handler", None)
    import check_handler
    return check_handler


def make_update(chat_id, chat_type="private", thread_id=None):
    message = SimpleNamespace(message_thread_id=thread_id)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=7, username="parent", full_name="Parent"),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_message=message,
        # Deliberately unusable: /check must never reply through the command.
        message=SimpleNamespace(reply_text=AsyncMock(side_effect=AssertionError("reply used"))),
    )


def real_subscription(sub_type="sub_5"):
    return {
        "name": "Child", "group": "Group", "user_ids": ["7"], "usernames": [],
        "subscription_type": sub_type, "subscription_type_raw": "Абон 5",
        "limit": 5, "used": 2, "unused": 3, "visit_dates": [],
        "start_date_raw": "01.01.2026", "end_date_raw": "01.02.2026",
        "days_until_end": "10", "warning_7": "", "difference": "",
        "deposit": "", "start_date": datetime(2026, 1, 1),
    }


def run_check(handler, update):
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(handler.check_subscriptions(update, context))
    return context


def test_group_check_loads_only_matching_sheet_and_sends_before_admin(monkeypatch):
    handler = import_check_handler(monkeypatch)
    events = []
    loader = Mock(return_value=[real_subscription()])
    monkeypatch.setattr(handler, "load_all_subscriptions", loader)
    notify = AsyncMock(side_effect=lambda *args: events.append("admin"))
    monkeypatch.setattr(handler, "notify_karina_action", notify)
    update = make_update(-45, "supergroup", 123)
    context = SimpleNamespace(bot=SimpleNamespace(
        send_message=AsyncMock(side_effect=lambda **kwargs: events.append("user"))
    ))

    asyncio.run(handler.check_subscriptions(update, context))

    assert loader.call_args.args == (["Группы 4-5"],)
    assert events == ["user", "admin"]
    assert context.bot.send_message.call_args.kwargs["message_thread_id"] == 123
    update.message.reply_text.assert_not_awaited()


def test_private_check_searches_all_sheets(monkeypatch):
    handler = import_check_handler(monkeypatch)
    loader = Mock(return_value=[real_subscription()])
    monkeypatch.setattr(handler, "load_all_subscriptions", loader)
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())

    run_check(handler, make_update(7, "private"))

    assert loader.call_args.args == (handler.SUBSCRIPTION_SHEETS,)


def test_error_uses_explicit_send_in_same_topic(monkeypatch):
    handler = import_check_handler(monkeypatch)
    monkeypatch.setattr(handler, "load_all_subscriptions", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())
    update = make_update(-69, "supergroup", 2225)

    context = run_check(handler, update)

    assert context.bot.send_message.call_args.kwargs["chat_id"] == -69
    assert context.bot.send_message.call_args.kwargs["message_thread_id"] == 2225
    update.message.reply_text.assert_not_awaited()


def test_blank_and_drop_in_are_excluded_from_check(monkeypatch):
    handler = import_check_handler(monkeypatch)
    blank = real_subscription("")
    blank["subscription_type_raw"] = ""
    drop_in = real_subscription("drop_in")
    drop_in["subscription_type_raw"] = "Разово"
    monkeypatch.setattr(handler, "load_all_subscriptions", Mock(return_value=[blank, drop_in]))
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())

    context = run_check(handler, make_update(7))

    assert "нет активных абонементов" in context.bot.send_message.call_args.kwargs["text"]


def test_known_group_with_successful_empty_load_gets_no_subscription_message(monkeypatch):
    handler = import_check_handler(monkeypatch)
    loader = Mock(return_value=[])
    monkeypatch.setattr(handler, "load_all_subscriptions", loader)
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())

    context = run_check(handler, make_update(-100, "supergroup"))

    assert loader.call_args.args == (["Взрослая группа"],)
    assert "нет активных абонементов" in context.bot.send_message.call_args.kwargs["text"]
    assert "Таблица пуста" not in context.bot.send_message.call_args.kwargs["text"]


def test_alert_status_excludes_blank_and_drop_in_and_preserves_real_types():
    status = subscription_tools.get_subscription_alert_status
    assert status({"subscription_type": "", "unused": 0}) == "none"
    assert status({"subscription_type": "drop_in", "unused": 0}) == "none"
    assert status({"subscription_type": "sub_5", "unused": 0}) == "finished"
    assert status({"subscription_type": "sub_5", "unused": 1}) == "last_lesson"
    assert status({"subscription_type": "unlimited", "unused": 0,
                   "days_until_end": "expired"}) == "expired"
    assert status({"subscription_type": "unlimited", "unused": 0,
                   "days_until_end": "5"}) == "none"


def test_unpaid_payment_parser_recognizes_supported_forms_and_preserves_detail():
    parse = subscription_tools.parse_unpaid_payment

    assert parse("не оплачено") == "не оплачено"
    assert parse("Не оплачено") == "не оплачено"
    assert parse("не оплачено 100") == "не оплачено 100"
    assert parse("НЕ ОПЛАЧЕНО долг 100") == "не оплачено долг 100"


def test_unpaid_payment_parser_ignores_paid_and_empty_values():
    parse = subscription_tools.parse_unpaid_payment

    assert parse("оплачено") is None
    assert parse("1000") is None
    assert parse("") is None
    assert parse(None) is None


def test_check_payment_warning_only_for_unpaid_and_keeps_detail(monkeypatch):
    handler = import_check_handler(monkeypatch)

    unpaid = real_subscription()
    unpaid["deposit"] = "Не оплачено 100"
    paid = real_subscription()
    paid["deposit"] = "оплачено"

    assert "💳 *Оплата:* ⚠️ не оплачено 100" in handler.build_subscription_message(unpaid)
    assert "*Оплата:*" not in handler.build_subscription_message(paid)


def import_scheduler_handler(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("GROUP_ID_45", "-45")
    monkeypatch.setenv("GROUP_ID_69", "-69")
    monkeypatch.setenv("GROUP_ID_ADULT", "-100")
    import group_config
    importlib.reload(group_config)
    monkeypatch.setitem(
        sys.modules,
        "reminder_handler",
        SimpleNamespace(poll_to_group={}, send_admin_report=AsyncMock()),
    )
    sys.modules.pop("scheduler_handler", None)
    with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
        "googleapiclient.discovery.build"
    ):
        import scheduler_handler
    return scheduler_handler


def scheduler_subscription(name, group="Group", sub_type="sub_5"):
    subscription = real_subscription(sub_type)
    subscription.update({
        "name": name,
        "group": group,
        "subscription_type_raw": "Разово" if sub_type == "drop_in" else "Абон 5",
        "unused": 2,
        "deposit": "не оплачено",
        "start_date": datetime.now() - timedelta(days=1),
    })
    return subscription


def test_scheduler_excludes_blank_drop_in_and_future_unpaid_subscriptions(monkeypatch):
    handler = import_scheduler_handler(monkeypatch)
    blank = scheduler_subscription("Blank", sub_type="")
    blank["subscription_type_raw"] = ""
    drop_in = scheduler_subscription("Drop-in", sub_type="drop_in")
    future = scheduler_subscription("Future")
    future["start_date"] = datetime.now() + timedelta(days=1)
    monkeypatch.setattr(handler, "load_all_subscriptions", Mock(
        return_value=[blank, drop_in, future]
    ))
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(handler.check_expired_subscriptions(app, ["Group"]))

    app.bot.send_message.assert_not_awaited()


def test_scheduler_aggregates_started_unpaid_subscriptions_independently(monkeypatch):
    handler = import_scheduler_handler(monkeypatch)
    first = scheduler_subscription("Коротченко Дима", "Group A")
    second = scheduler_subscription("Иванова Аня", "Group B")
    second["deposit"] = "не оплачено 100"
    # An expiry alert must not suppress the independent unpaid entry.
    first["days_until_end"] = "expired"
    monkeypatch.setattr(handler, "load_all_subscriptions", Mock(return_value=[first, second]))
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(handler.check_expired_subscriptions(app, ["Group A", "Group B"]))

    assert app.bot.send_message.await_count == 2  # one expiry + one unpaid aggregate
    unpaid_call = app.bot.send_message.await_args_list[-1]
    text = unpaid_call.kwargs["text"]
    assert text.count("*Неоплаченные абонементы*") == 1
    assert "Коротченко Дима (Group A) — не оплачено" in text
    assert "Иванова Аня (Group B) — не оплачено 100" in text
    assert unpaid_call.kwargs["chat_id"] == 1


def test_loader_uses_one_batch_get_and_skips_blank_subscription_rows(monkeypatch):
    header = ["Имя", "Группа", "User ID", "username", "Абонемент", "Лимит",
              "Used", "Дата Начала", "Срок Действия", "Unused"]
    response = {"valueRanges": [
        {"values": [header, ["Blank", "G", "7", "", "", "", "", "", "", ""]]},
        {"values": [header, ["Real", "G", "7", "", "Абон 5", "5", "2", "", "", "3"]]},
    ]}
    execute = Mock(return_value=response)
    batch_get = Mock(return_value=SimpleNamespace(execute=execute))
    service = SimpleNamespace(values=lambda: SimpleNamespace(batchGet=batch_get))
    factory = Mock(return_value=service)
    monkeypatch.setattr(subscription_tools, "create_subscription_sheets_service", factory)

    result = subscription_tools.load_all_subscriptions(["Группы 4-5", "Группы 6-9"])

    assert [item["name"] for item in result] == ["Real"]
    factory.assert_called_once_with()
    batch_get.assert_called_once()
    assert len(batch_get.call_args.kwargs["ranges"]) == 2
    execute.assert_called_once()


def test_each_loader_invocation_creates_its_own_sheets_service(monkeypatch):
    execute_one = Mock(return_value={"valueRanges": []})
    execute_two = Mock(return_value={"valueRanges": []})
    service_one = SimpleNamespace(values=lambda: SimpleNamespace(
        batchGet=Mock(return_value=SimpleNamespace(execute=execute_one))
    ))
    service_two = SimpleNamespace(values=lambda: SimpleNamespace(
        batchGet=Mock(return_value=SimpleNamespace(execute=execute_two))
    ))
    factory = Mock(side_effect=[service_one, service_two])
    monkeypatch.setattr(subscription_tools, "create_subscription_sheets_service", factory)

    subscription_tools.load_all_subscriptions(["Группы 4-5"])
    subscription_tools.load_all_subscriptions(["Группы 4-5"])

    assert factory.call_count == 2
    execute_one.assert_called_once_with()
    execute_two.assert_called_once_with()
