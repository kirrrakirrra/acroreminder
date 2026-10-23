import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_webhook_responsiveness import _load_main


def test_rapid_commands_are_suppressed_but_distinct_users_are_not(monkeypatch):
    main = _load_main(monkeypatch)
    gate = asyncio.Event()
    calls = []

    async def process(update):
        calls.append(update)
        if update.update_id == 10:
            await gate.wait()

    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock(side_effect=process))
    processor = main.WebhookUpdateProcessor(app)

    def make_update(data, _bot):
        user = SimpleNamespace(id=data.get("user", 1))
        chat = SimpleNamespace(id=100)
        return SimpleNamespace(
            update_id=data["update_id"], effective_user=user, effective_chat=chat,
            message=SimpleNamespace(text=data.get("text", "/check")),
            callback_query=None,
        )

    async def scenario():
        request = SimpleNamespace(json=AsyncMock(return_value={"update_id": 10}))
        with patch.object(main.Update, "de_json", side_effect=make_update):
            await processor.handle(request)
            await asyncio.sleep(0)
            request.json.return_value = {"update_id": 11}
            await processor.handle(request)  # duplicate command while busy
            request.json.return_value = {"update_id": 12, "user": 2}
            await processor.handle(request)  # another user is independent
            request.json.return_value = {"update_id": 13, "text": "/info"}
            await processor.handle(request)  # different command is independent
            gate.set()
            await asyncio.gather(*processor.active_tasks)
            assert len(calls) == 3
            request.json.return_value = {"update_id": 14}
            await processor.handle(request)  # immediate repeat after completion
            assert len(calls) == 3

    asyncio.run(scenario())


def test_duplicate_callback_is_acknowledged(monkeypatch):
    main = _load_main(monkeypatch)
    gate = asyncio.Event()
    query = SimpleNamespace(data="info|prices", message=SimpleNamespace(message_id=50), answer=AsyncMock())
    async def process(_):
        await gate.wait()
    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock(side_effect=process))
    processor = main.WebhookUpdateProcessor(app)

    def make_update(data, _bot):
        return SimpleNamespace(
            update_id=data["update_id"],
            effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=9),
            message=None, callback_query=query,
        )

    async def scenario():
        request = SimpleNamespace(json=AsyncMock(return_value={"update_id": 1}))
        with patch.object(main.Update, "de_json", side_effect=make_update):
            await processor.handle(request)
            await asyncio.sleep(0)
            request.json.return_value = {"update_id": 2}
            await processor.handle(request)
            query.answer.assert_awaited_once()
            assert app.process_update.await_count == 1
            gate.set()
            await asyncio.gather(*processor.active_tasks)

    asyncio.run(scenario())
