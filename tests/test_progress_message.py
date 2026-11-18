import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from progress_message import (
    PROGRESS_TEXT,
    start_progress,
    finish_progress,
    start_callback_progress,
    complete_callback_progress,
)


def test_command_placeholder_is_edited_in_place():
    pending = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(reply_text=AsyncMock(return_value=pending))

    async def scenario():
        started = await start_progress(message)
        await finish_progress(message, started, "✅ Результат", parse_mode="HTML")

    asyncio.run(scenario())
    message.reply_text.assert_awaited_once_with(PROGRESS_TEXT)
    pending.edit_text.assert_awaited_once_with("✅ Результат", parse_mode="HTML")
    pending.delete.assert_not_awaited()


def test_failed_edit_falls_back_to_reply_without_stuck_placeholder():
    pending = SimpleNamespace(
        edit_text=AsyncMock(side_effect=RuntimeError("edit failed")),
        delete=AsyncMock(),
    )
    message = SimpleNamespace(reply_text=AsyncMock(return_value=pending))

    asyncio.run(finish_progress(message, pending, "✅ Результат"))
    pending.delete.assert_awaited_once()
    message.reply_text.assert_awaited_once_with("✅ Результат")


def test_failed_initial_progress_still_returns_result():
    message = SimpleNamespace(reply_text=AsyncMock(side_effect=[
        RuntimeError("send failed"), None,
    ]))
    async def scenario():
        pending = await start_progress(message)
        assert pending is None
        await finish_progress(message, pending, "✅ Результат")
    asyncio.run(scenario())
    assert message.reply_text.await_count == 2
    assert message.reply_text.await_args.args == ("✅ Результат",)


def test_callback_placeholder_is_separate_and_preserves_thread():
    pending = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
    query = SimpleNamespace(message=SimpleNamespace(message_thread_id=321))
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1000123), callback_query=query,
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=pending)))

    async def scenario():
        state = await start_callback_progress(update, context)
        assert state is pending
        await complete_callback_progress(state)

    asyncio.run(scenario())
    context.bot.send_message.assert_awaited_once_with(
        chat_id=-1000123, text=PROGRESS_TEXT, message_thread_id=321
    )
    pending.delete.assert_awaited_once()
    pending.edit_text.assert_not_awaited()


def test_callback_error_replaces_progress_and_keeps_visible_error():
    pending = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())

    asyncio.run(complete_callback_progress(pending, "❌ Сбой загрузки"))

    pending.edit_text.assert_awaited_once_with("❌ Сбой загрузки")
    pending.delete.assert_not_awaited()


def test_unknown_callback_chat_does_not_send_new_message():
    update = SimpleNamespace(effective_chat=None, callback_query=SimpleNamespace(message=None))
    bot = SimpleNamespace(send_message=AsyncMock())

    result = asyncio.run(start_callback_progress(
        update, SimpleNamespace(bot=bot)
    ))
    assert result is None
    bot.send_message.assert_not_awaited()
