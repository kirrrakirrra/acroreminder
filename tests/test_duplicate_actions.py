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


def test_addressed_and_plain_check_share_action_key(monkeypatch):
    main = _load_main(monkeypatch)
    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock())
    processor = main.WebhookUpdateProcessor(app)

    def command(text):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=-100),
            message=SimpleNamespace(text=text),
            callback_query=None,
        )

    assert processor._action_key(command("/check")) == processor._action_key(
        command("/check@Acro_reminder_bot")
    )
    assert processor._action_key(command("/check anything")) != processor._action_key(
        command("/check")
    )


def test_addressed_duplicate_during_slow_check_is_ignored_and_later_retry_allowed(monkeypatch):
    main = _load_main(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def process(update):
        calls.append(update.update_id)
        if update.update_id == 100:
            started.set()
            await finish.wait()

    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock(side_effect=process))
    processor = main.WebhookUpdateProcessor(app)

    def make_update(data, _bot):
        return SimpleNamespace(
            update_id=data["update_id"],
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=-100),
            message=SimpleNamespace(text=data["text"]),
            callback_query=None,
        )

    async def scenario():
        request = SimpleNamespace(json=AsyncMock(return_value={
            "update_id": 100, "text": "/check"
        }))
        with patch.object(main.Update, "de_json", side_effect=make_update):
            await processor.handle(request)
            await started.wait()

            # Simulated 22 seconds after the first request, still active.
            clock[0] = 122.0
            request.json.return_value = {
                "update_id": 101, "text": "/check@Acro_reminder_bot"
            }
            await processor.handle(request)
            assert calls == [100]
            assert not finish.is_set()

            clock[0] = 150.0
            finish.set()
            await asyncio.gather(*processor.active_tasks)
            assert calls == [100]

            # Within the two-second completion cooldown, still suppressed.
            request.json.return_value = {"update_id": 102, "text": "/check"}
            await processor.handle(request)
            assert calls == [100]

            # Later intentional repeat is accepted.
            clock[0] = 153.0
            request.json.return_value = {"update_id": 103, "text": "/check"}
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert calls == [100, 103]

    asyncio.run(scenario())



def test_list_toggles_are_distinct_webhook_actions_even_inside_two_seconds(monkeypatch):
    """The processor must not swallow an intentional immediate uncheck."""
    main = _load_main(monkeypatch)
    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock())
    processor = main.WebhookUpdateProcessor(app)
    clock = [400.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    query = SimpleNamespace(
        data="al:t:abc123:2:0",
        message=SimpleNamespace(message_id=99),
        answer=AsyncMock(),
    )

    def make_update(data, _bot):
        query.data = data["callback"]
        return SimpleNamespace(
            update_id=data["update_id"],
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1),
            message=None,
            callback_query=SimpleNamespace(
                data=query.data,
                message=query.message,
                answer=query.answer,
            ),
        )

    async def scenario():
        request = SimpleNamespace(json=AsyncMock(return_value={
            "update_id": 500, "callback": "al:t:abc123:2:0"
        }))
        with patch.object(main.Update, "de_json", side_effect=make_update):
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert app.process_update.await_count == 1

            # The same callback (replay) is suppressed.
            request.json.return_value = {
                "update_id": 501, "callback": "al:t:abc123:2:0"
            }
            await processor.handle(request)
            assert app.process_update.await_count == 1

            # The updated button has :1 instead of :0, so it is a
            # distinct action accepted immediately, without waiting 2s.
            request.json.return_value = {
                "update_id": 502, "callback": "al:t:abc123:2:1"
            }
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert app.process_update.await_count == 2
            assert query.answer.await_count == 1
    asyncio.run(scenario())



def test_identical_list_checkbox_has_one_second_cooldown_only(monkeypatch):
    main = _load_main(monkeypatch)
    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock())
    processor = main.WebhookUpdateProcessor(app)
    clock = [100.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])

    def make_update(data, _bot):
        callback_data = data.get("callback")
        return SimpleNamespace(
            update_id=data["update_id"],
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1),
            message=None if callback_data is not None else SimpleNamespace(text=data.get("text", "/check")),
            callback_query=(
                SimpleNamespace(
                    data=callback_data,
                    message=SimpleNamespace(message_id=55),
                    answer=AsyncMock(),
                ) if callback_data is not None else None
            ),
        )

    async def scenario():
        request = SimpleNamespace(json=AsyncMock(return_value={
            "update_id": 200, "callback": "al:t:abc123:3:0"
        }))
        with patch.object(main.Update, "de_json", side_effect=make_update):
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert app.process_update.await_count == 1

            clock[0] += 0.99
            request.json.return_value = {"update_id": 201, "callback": "al:t:abc123:3:0"}
            await processor.handle(request)
            assert app.process_update.await_count == 1

            clock[0] += 0.02
            request.json.return_value = {"update_id": 202, "callback": "al:t:abc123:3:0"}
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert app.process_update.await_count == 2

            # Other commands keep their existing two-second protection.
            clock[0] += 1.0
            request.json.return_value = {"update_id": 203, "text": "/check"}
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert app.process_update.await_count == 3
            clock[0] += 1.1
            request.json.return_value = {"update_id": 204, "text": "/check"}
            await processor.handle(request)
            assert app.process_update.await_count == 3
            clock[0] += 1.0
            request.json.return_value = {"update_id": 205, "text": "/check"}
            await processor.handle(request)
            await asyncio.gather(*processor.active_tasks)
            assert app.process_update.await_count == 4
    asyncio.run(scenario())
