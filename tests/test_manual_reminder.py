import asyncio
import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_scheduler(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("KARINA_ID", "2")
    monkeypatch.setenv("SPREADSHEET_ID", "sheet")
    monkeypatch.setenv("GROUP_ID_45", "-45")
    monkeypatch.setenv("GROUP_ID_69", "-69")
    monkeypatch.setenv("GROUP_ID_ADULT", "-100")
    for name in ("group_config", "reminder_handler", "scheduler_handler"):
        sys.modules.pop(name, None)
    with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
        "googleapiclient.discovery.build"
    ) as build:
        build.return_value.spreadsheets.return_value = Mock()
        return importlib.import_module("scheduler_handler")


def update(user_id, data=None):
    query = None
    if data is not None:
        query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
        callback_query=query,
    )


def context(poll_id="new-poll"):
    options = [SimpleNamespace(text=x) for x in ("a", "b", "c")]
    poll = SimpleNamespace(id=poll_id, options=options)
    return SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(), send_poll=AsyncMock(return_value=SimpleNamespace(poll=poll))),
        bot_data={}, application=SimpleNamespace(),
    )


def sheet_rows(handler, rows):
    values = handler.sheets_service.values.return_value
    values.get.return_value.execute.return_value = {"values": rows}
    values.append.return_value.execute.return_value = {}
    values.update.return_value.execute.return_value = {}
    return values


def vn(year, month, day, hour, minute=0):
    return pytz.timezone("Asia/Ho_Chi_Minh").localize(datetime(year, month, day, hour, minute))


def test_command_is_registered():
    source = Path("main.py").read_text()
    assert 'CommandHandler("send_reminder", send_reminder_command)' in source


def test_authorization_and_relevant_occurrences(monkeypatch):
    handler = load_scheduler(monkeypatch)
    unauthorized = update(99)
    asyncio.run(handler.send_reminder_command(unauthorized, context()))
    assert "только администратору" in unauthorized.message.reply_text.await_args.args[0]

    # Tuesday: today's child lessons and tomorrow's Wednesday lessons are relevant;
    # adult Thursday (day-before) is not yet relevant.
    choices = handler.relevant_upcoming_groups(vn(2026, 9, 8, 12))
    keys = {group["key"] for _, group, _ in choices}
    assert {"junior_1715", "junior_1830"} <= keys
    assert "adult" not in keys

    authorized = update(2)
    monkeypatch.setattr(handler, "now_local", lambda: vn(2026, 9, 8, 12))
    asyncio.run(handler.send_reminder_command(authorized, context()))
    markup = authorized.message.reply_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert any("сегодня" in label and "17:15" in label for label in labels)


def test_day_before_adult_and_started_lesson_filter(monkeypatch):
    handler = load_scheduler(monkeypatch)
    wednesday = handler.relevant_upcoming_groups(vn(2026, 9, 9, 19))
    adult = [(g, date) for _, g, date in wednesday if g["key"] == "adult"]
    assert adult and adult[0][1].isoformat() == "2026-09-10"
    # At 18:00 Monday the 17:15 child class has started, while 18:30 remains.
    monday = {g["key"] for _, g, _ in handler.relevant_upcoming_groups(vn(2026, 9, 7, 18))}
    assert "69_beginner" not in monday
    assert "69_pro" in monday


def test_first_send_and_duplicate_warning(monkeypatch):
    handler = load_scheduler(monkeypatch)
    values = sheet_rows(handler, [])
    ctx = context()
    first = update(1, "select_reminder|0|2026-09-08")
    asyncio.run(handler.handle_callback(first, ctx))
    assert ctx.bot.send_message.await_count == 1
    assert ctx.bot.send_poll.await_count == 1
    report_appends = [c for c in values.append.call_args_list if c.kwargs.get("range") == "Репорты!A1"]
    assert len(report_appends) == 1
    assert first.callback_query.edit_message_text.await_args.args[0] == "Напоминание и опрос отправлены ✅"

    sheet_rows(handler, [["old", handler.groups[0]["name"], "", "", "-45", "4", "2026-09-08"]])
    duplicate_ctx = context()
    duplicate = update(1, "select_reminder|0|2026-09-08")
    asyncio.run(handler.handle_callback(duplicate, duplicate_ctx))
    duplicate_ctx.bot.send_message.assert_not_awaited()
    duplicate_ctx.bot.send_poll.assert_not_awaited()
    assert "уже отправлены" in duplicate.callback_query.edit_message_text.await_args.args[0]


def test_explicit_resend_replaces_canonical_poll(monkeypatch):
    handler = load_scheduler(monkeypatch)
    old = ["old", handler.groups[0]["name"], "", "", "-45", "4", "2026-09-08"]
    values = sheet_rows(handler, [old])
    ctx = context("replacement")
    resend = update(2, "resend_reminder|0|2026-09-08")
    asyncio.run(handler.handle_callback(resend, ctx))
    assert ctx.bot.send_message.await_count == 1
    assert ctx.bot.send_poll.await_count == 1
    assert values.update.call_args.kwargs["range"] == "Репорты!A2:G2"
    assert values.update.call_args.kwargs["body"]["values"][0][0] == "replacement"


def test_stale_yes_is_protected_and_scheduler_skips_durable_occurrence(monkeypatch):
    handler = load_scheduler(monkeypatch)
    row = ["manual", handler.groups[0]["name"], "", "", "-45", "4", "2026-09-08"]
    sheet_rows(handler, [row])
    stale_ctx = context()
    stale = update(1, "yes|0|2026-09-08")
    asyncio.run(handler.handle_callback(stale, stale_ctx))
    stale_ctx.bot.send_poll.assert_not_awaited()

    monkeypatch.setattr(handler, "now_local", lambda: vn(2026, 9, 8, 11, 2))
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    assert asyncio.run(handler.ask_admin(app, 0, handler.groups[0])) is False
    app.bot.send_message.assert_not_awaited()


def test_legacy_yes_without_date_is_stale(monkeypatch):
    handler = load_scheduler(monkeypatch)
    ctx = context()
    legacy = update(1, "yes|0")

    asyncio.run(handler.handle_callback(legacy, ctx))

    ctx.bot.send_message.assert_not_awaited()
    ctx.bot.send_poll.assert_not_awaited()
    assert "кнопка устарела" in legacy.callback_query.edit_message_text.await_args.args[0]


def test_announcement_failure_is_reported_without_poll(monkeypatch):
    handler = load_scheduler(monkeypatch)
    sheet_rows(handler, [])
    ctx = context()
    ctx.bot.send_message.side_effect = RuntimeError("telegram announcement failed")
    callback = update(1, "yes|0|2026-09-08")

    asyncio.run(handler.handle_callback(callback, ctx))

    ctx.bot.send_poll.assert_not_awaited()
    message = callback.callback_query.edit_message_text.await_args.args[0]
    assert "Не удалось отправить напоминание" in message
    assert "отправлены ✅" not in message


def test_poll_failure_reports_partial_delivery(monkeypatch):
    handler = load_scheduler(monkeypatch)
    sheet_rows(handler, [])
    ctx = context()
    ctx.bot.send_poll.side_effect = RuntimeError("telegram poll failed")
    callback = update(1, "yes|0|2026-09-08")

    asyncio.run(handler.handle_callback(callback, ctx))

    ctx.bot.send_message.assert_awaited_once()
    message = callback.callback_query.edit_message_text.await_args.args[0]
    assert "Объявление отправлено, но опрос" in message
    assert "Не повторяйте" in message
    assert handler.occurrence_was_sent(handler.groups[0], "2026-09-08")


def test_report_persistence_failure_retries_without_resending_telegram(monkeypatch):
    handler = load_scheduler(monkeypatch)
    values = sheet_rows(handler, [])
    survey_request = Mock()
    survey_request.execute.return_value = {}
    failed_request = Mock()
    failed_request.execute.side_effect = RuntimeError("sheets unavailable")
    values.append.side_effect = lambda **kwargs: (
        survey_request if kwargs["range"] == "Опросы!A1" else failed_request
    )
    ctx = context()
    callback = update(1, "yes|0|2026-09-08")

    asyncio.run(handler.handle_callback(callback, ctx))

    ctx.bot.send_message.assert_awaited_once()
    ctx.bot.send_poll.assert_awaited_once()
    assert failed_request.execute.call_count == 3
    message = callback.callback_query.edit_message_text.await_args.args[0]
    assert "сохранить состояние" in message
    assert "отправлены ✅" not in message
    assert handler.occurrence_was_sent(handler.groups[0], "2026-09-08")


def test_survey_persistence_retries_then_full_success_without_telegram_resend(monkeypatch):
    handler = load_scheduler(monkeypatch)
    values = sheet_rows(handler, [])
    failed = Mock()
    failed.execute.side_effect = RuntimeError("temporary survey failure")
    succeeded = Mock()
    succeeded.execute.return_value = {}
    report = Mock()
    report.execute.return_value = {}
    survey_attempts = iter([failed, succeeded])
    values.append.side_effect = lambda **kwargs: (
        next(survey_attempts) if kwargs["range"] == "Опросы!A1" else report
    )
    ctx = context()
    callback = update(1, "yes|0|2026-09-08")

    asyncio.run(handler.handle_callback(callback, ctx))

    ctx.bot.send_message.assert_awaited_once()
    ctx.bot.send_poll.assert_awaited_once()
    assert callback.callback_query.edit_message_text.await_args.args[0] == "Напоминание и опрос отправлены ✅"


def test_survey_persistence_exhaustion_still_saves_report_and_is_partial(monkeypatch):
    handler = load_scheduler(monkeypatch)
    values = sheet_rows(handler, [])
    failed = Mock()
    failed.execute.side_effect = RuntimeError("survey unavailable")
    report = Mock()
    report.execute.return_value = {}
    values.append.side_effect = lambda **kwargs: (
        failed if kwargs["range"] == "Опросы!A1" else report
    )
    ctx = context()
    callback = update(1, "yes|0|2026-09-08")

    asyncio.run(handler.handle_callback(callback, ctx))

    ctx.bot.send_message.assert_awaited_once()
    ctx.bot.send_poll.assert_awaited_once()
    assert failed.execute.call_count == 3
    report.execute.assert_called_once()
    message = callback.callback_query.edit_message_text.await_args.args[0]
    assert "защиты от дублей сохранено" in message
    assert "восстановления ответов опроса" in message
    assert "отправлены ✅" not in message


def test_restore_poll_mapping_uses_reports_only_as_fallback(monkeypatch):
    handler = load_scheduler(monkeypatch)
    reminder = sys.modules["reminder_handler"]
    reminder.poll_to_group.clear()
    values = reminder.sheets_service.values.return_value

    def get_request(**kwargs):
        request = Mock()
        if kwargs["range"] == "Опросы!A2:G":
            request.execute.return_value = {
                "values": [["survey-poll", "Survey Group"]]
            }
        else:
            request.execute.return_value = {
                "values": [
                    ["survey-poll", "Wrong Report Group", "", "", "", "", "2026-09-08"],
                    ["old-report-poll", "Report Group", "", "", "", "", "2026-09-08"],
                    ["current-report-poll", "Report Group", "", "", "", "", "2026-09-08"],
                ]
            }
        return request

    values.get.side_effect = get_request

    reminder.restore_poll_to_group()

    assert reminder.poll_to_group["survey-poll"]["name"] == "Survey Group"
    assert "old-report-poll" not in reminder.poll_to_group
    assert reminder.poll_to_group["current-report-poll"]["name"] == "Report Group"


def test_legacy_duplicate_rows_choose_latest_for_reports(monkeypatch):
    handler = load_scheduler(monkeypatch)
    rows = [
        ["old", "Group", "", "", "", "", "2026-09-08"],
        ["other", "Other", "", "", "", "", "2026-09-08"],
        ["new", "Group", "", "", "", "", "2026-09-08"],
    ]
    canonical = handler.canonical_report_rows(rows, "2026-09-08")
    assert [row[0] for row in canonical] == ["new", "other"]
