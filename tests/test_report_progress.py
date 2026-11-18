import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_manual_reminder import load_scheduler


def report_callback_update(data):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(message_thread_id=None),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=100, type="private"),
        effective_user=SimpleNamespace(id=1, username="admin", full_name="Admin"),
    )


def report_callback_context():
    pending = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
    return (
        SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(return_value=pending)),
            application=SimpleNamespace(),
        ),
        pending,
    )


def test_refresh_report_shows_progress_and_cleans_up_on_missing_row(monkeypatch):
    load_scheduler(monkeypatch)
    handler = sys.modules["reminder_handler"]
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())
    monkeypatch.setattr(handler, "_run_sheets", AsyncMock(return_value={"values": []}))
    update = report_callback_update("refresh_report|poll-123")
    context, pending = report_callback_context()

    asyncio.run(handler.refresh_report_callback(update, context))

    update.callback_query.answer.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=100, text="⏳ Собираю информацию…"
    )
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "❌ Не найдена связка в таблице Репорты."
    )
    pending.delete.assert_awaited_once()


def test_refresh_report_error_replaces_progress_with_failure(monkeypatch):
    load_scheduler(monkeypatch)
    handler = sys.modules["reminder_handler"]
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())
    monkeypatch.setattr(handler, "_run_sheets", AsyncMock(side_effect=RuntimeError("Sheets unavailable")))
    update = report_callback_update("refresh_report|poll-123")
    context, pending = report_callback_context()

    asyncio.run(handler.refresh_report_callback(update, context))

    pending.edit_text.assert_awaited_once_with(
        "❌ Не удалось обновить отчёт. Попробуйте ещё раз."
    )
    pending.delete.assert_not_awaited()


def test_notify_parents_shows_progress_and_cleans_up(monkeypatch):
    load_scheduler(monkeypatch)
    handler = sys.modules["reminder_handler"]
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())
    monkeypatch.setattr(handler, "_run_sheets", AsyncMock(return_value={"values": []}))
    update = report_callback_update("notify_parents|poll-123")
    context, pending = report_callback_context()

    asyncio.run(handler.notify_parents_callback(update, context))

    context.bot.send_message.assert_awaited_once_with(
        chat_id=100, text="⏳ Собираю информацию…"
    )
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "❌ Не найдена строка в таблице Репорты."
    )
    pending.delete.assert_awaited_once()
