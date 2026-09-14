import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from report_rows import recover_missing_report_occurrences


GROUP = {
    "name": "Детская",
    "group_id": "-10",
    "thread_id": 7,
    "check_day_offset": 0,
}
ADULT = {
    "name": "Взрослая",
    "group_id": "-20",
    "thread_id": None,
    "check_day_offset": 1,
}


class Sheets:
    def __init__(self, reports=None, surveys=None):
        self.data = {"Репорты": list(reports or []), "Опросы": list(surveys or [])}
        self.report_appends = 0

    def values(self):
        return self

    def get(self, range, **_kwargs):
        self.result = {"values": [row[:] for row in self.data[range.split("!")[0]]]}
        return self

    def append(self, range, body, **_kwargs):
        sheet = range.split("!")[0]
        self.data[sheet].extend(row[:] for row in body["values"])
        if sheet == "Репорты":
            self.report_appends += 1
        self.result = {}
        return self

    def execute(self):
        return self.result


async def run(service, _label, operation):
    return operation(service)


def creation(poll_id, group, timestamp):
    return [poll_id, group, "", "", timestamp, "", "yes|no"]


def recover(service):
    return asyncio.run(recover_missing_report_occurrences(
        lambda label, operation: run(service, label, operation),
        "sheet", [GROUP, ADULT],
    ))


def test_recovers_complete_rows_and_is_idempotent_with_latest_poll():
    service = Sheets(surveys=[
        creation("old", "Детская", "2026-09-08 10:00:00"),
        creation("new", "Детская", "2026-09-08 11:00:00"),
        creation("adult", "Взрослая", "2026-09-09 19:00:00"),
    ])

    recover(service)
    recover(service)

    assert service.data["Репорты"] == [
        ["new", "Детская", "", "", "-10", "7", "2026-09-08"],
        ["adult", "Взрослая", "", "", "-20", "", "2026-09-10"],
    ]


def test_existing_occurrence_is_not_duplicated():
    existing = ["kept", "Детская", "12", "13", "-10", "7", "2026-09-08"]
    service = Sheets(
        reports=[existing],
        surveys=[creation("replacement", "Детская", "2026-09-08 11:00:00")],
    )
    recover(service)
    assert service.data["Репорты"] == [existing]


def test_concurrent_recovery_serializes_fresh_read_and_single_append():
    service = Sheets(surveys=[
        creation("poll", "Детская", "2026-09-08 11:00:00"),
    ])

    async def yielding_run(_label, operation):
        # Make an unlocked implementation reliably interleave both initial
        # reads before either caller reaches its append.
        await asyncio.sleep(0)
        return operation(service)

    async def recover_twice():
        return await asyncio.gather(
            recover_missing_report_occurrences(yielding_run, "sheet", [GROUP]),
            recover_missing_report_occurrences(yielding_run, "sheet", [GROUP]),
        )

    results = asyncio.run(recover_twice())

    assert len(results) == 2
    assert service.report_appends == 1
    assert service.data["Репорты"] == [
        ["poll", "Детская", "", "", "-10", "7", "2026-09-08"],
    ]
    recover(service)
    assert service.report_appends == 1


def test_vote_malformed_timestamp_and_unknown_group_are_skipped():
    service = Sheets(surveys=[
        ["vote", "Детская", "123", "@parent", "2026-09-08 12:00:00", "Parent", "yes"],
        creation("bad-date", "Детская", "not a timestamp"),
        creation("unknown", "Неизвестная", "2026-09-08 12:00:00"),
    ])
    recover(service)
    assert service.data["Репорты"] == []


def load_report_handler(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1")
    monkeypatch.setenv("KARINA_ID", "2")
    monkeypatch.setenv("SPREADSHEET_ID", "sheet")
    for name in ("group_config", "reminder_handler", "report_handler"):
        sys.modules.pop(name, None)
    with patch("google.oauth2.service_account.Credentials.from_service_account_file"), patch(
        "googleapiclient.discovery.build"
    ):
        return importlib.import_module("report_handler")


def test_manual_report_recovers_then_sends_new_messages(monkeypatch):
    handler = load_report_handler(monkeypatch)
    row = ["poll", "Детская", "", "", "-10", "7", "2026-09-08"]
    monkeypatch.setattr(handler, "format_now", lambda: "2026-09-08 12:00:00")
    recovery = AsyncMock(return_value=[row])
    send = AsyncMock()
    monkeypatch.setattr(handler, "recover_missing_report_occurrences", recovery)
    monkeypatch.setattr(handler, "send_admin_report", send)
    monkeypatch.setattr(handler, "notify_karina_action", AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, full_name="Admin", username="admin"),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(application=Mock())

    asyncio.run(handler.report_command(update, context))

    recovery.assert_awaited_once()
    send.assert_awaited_once_with(
        app=context.application, poll_id="poll",
        report_message_id=None, ping_message_id=None,
    )


def test_scheduled_report_recovers_before_rendering(monkeypatch):
    handler = load_report_handler(monkeypatch)
    sys.modules.pop("scheduler_handler", None)
    scheduler = importlib.import_module("scheduler_handler")
    row = ["poll", "Детская", "", "", "-10", "7", "2026-09-08"]
    recovery = AsyncMock(return_value=[row])
    send = AsyncMock()
    monkeypatch.setattr(scheduler, "recover_missing_report_occurrences", recovery)
    monkeypatch.setattr(scheduler, "send_admin_report", send)

    asyncio.run(scheduler.generate_scheduled_reports(Mock(), [GROUP], "2026-09-08"))

    recovery.assert_awaited_once()
    send.assert_awaited_once()
    assert send.await_args.kwargs["report_message_id"] is None
    assert send.await_args.kwargs["ping_message_id"] is None
