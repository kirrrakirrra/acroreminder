import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest

from test_manual_reminder import context, freeze_before_lesson, load_scheduler, sheet_rows, update


def test_too_old_callback_still_delivers(monkeypatch):
    handler = load_scheduler(monkeypatch)
    freeze_before_lesson(monkeypatch, handler)
    sheet_rows(handler, [])
    callback = update(1, "yes|0|2026-09-08")
    callback.callback_query.answer.side_effect = BadRequest(
        "Query is too old and response timeout expired or query id is invalid"
    )
    ctx = context()

    asyncio.run(handler.handle_callback(callback, ctx))

    assert ctx.bot.send_poll.await_count == 1


def test_occurrence_lock_blocks_same_group_but_not_another(monkeypatch):
    handler = load_scheduler(monkeypatch)
    freeze_before_lesson(monkeypatch, handler)
    sheet_rows(handler, [])
    entered = asyncio.Event()
    release = asyncio.Event()
    deliveries = []

    async def delivery(context_, group, lesson_date, replace=False):
        deliveries.append(group["name"])
        if group is handler.groups[0]:
            entered.set()
            await release.wait()
        handler.in_process_sent_occurrences.add((group["name"], lesson_date))
        return handler.DeliveryResult("success", "ok")

    monkeypatch.setattr(handler, "send_reminder_and_poll", delivery)

    async def scenario():
        first = asyncio.create_task(handler.handle_callback(update(1, "yes|0|2026-09-08"), context()))
        await entered.wait()
        same = asyncio.create_task(handler.handle_callback(update(1, "yes|0|2026-09-08"), context()))
        other = asyncio.create_task(handler.handle_callback(update(1, "yes|1|2026-09-08"), context()))
        for _ in range(20):
            if handler.groups[1]["name"] in deliveries:
                break
            await asyncio.sleep(0.005)
        assert handler.groups[1]["name"] in deliveries
        release.set()
        await asyncio.gather(first, same, other)

    asyncio.run(scenario())
    assert deliveries.count(handler.groups[0]["name"]) == 1


def _load_main(monkeypatch):
    load_scheduler(monkeypatch)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_webhook_returns_promptly_and_deduplicates(monkeypatch):
    main = _load_main(monkeypatch)
    gate = asyncio.Event()
    async def process_update(_):
        await gate.wait()

    app = SimpleNamespace(bot=Mock(), process_update=AsyncMock(side_effect=process_update))
    processor = main.WebhookUpdateProcessor(app, cache_limit=2)
    request = SimpleNamespace(json=AsyncMock(return_value={"update_id": 10}))

    async def scenario():
        with patch.object(main.Update, "de_json", side_effect=lambda data, bot: SimpleNamespace(update_id=data["update_id"])):
            response = await asyncio.wait_for(processor.handle(request), timeout=0.1)
            assert response.status == 200
            await processor.handle(request)
            request.json.return_value = {"update_id": 11}
            await processor.handle(request)
            await asyncio.sleep(0)
            assert app.process_update.await_count == 2
            gate.set()
            await asyncio.gather(*processor.active_tasks)
            await asyncio.sleep(0)
            assert not processor.active_tasks

    asyncio.run(scenario())


def test_poll_answer_persistence_does_not_block_loop(monkeypatch):
    handler = load_scheduler(monkeypatch)
    reminder = sys.modules["reminder_handler"]
    release = asyncio.Event()

    def blocking(_label, operation):
        import time
        time.sleep(0.05)

    monkeypatch.setattr(reminder, "_timed_sheets", blocking)
    poll_update = SimpleNamespace(poll_answer=SimpleNamespace(
        poll_id="poll", option_ids=[0],
        user=SimpleNamespace(id=7, username="u", full_name="User"),
    ))

    async def scenario():
        task = asyncio.create_task(reminder.handle_poll_answer(poll_update, SimpleNamespace()))
        await asyncio.sleep(0.005)
        assert not task.done()
        # This sleep completes while the synchronous worker remains blocked.
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.01)
        await task

    asyncio.run(scenario())
