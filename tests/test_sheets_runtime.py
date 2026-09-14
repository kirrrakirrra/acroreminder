import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sheets_runtime


def isolated_runtime(monkeypatch, workers):
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="test-sheets")
    monkeypatch.setattr(sheets_runtime, "_executor", executor)
    monkeypatch.setattr(sheets_runtime, "_thread_local", threading.local())
    return executor


def test_worker_reuses_client_and_disables_discovery_cache(monkeypatch):
    executor = isolated_runtime(monkeypatch, 1)
    build = Mock()
    first_service = Mock()
    build.return_value.spreadsheets.return_value = first_service
    monkeypatch.setattr(sheets_runtime, "build", build)
    monkeypatch.setattr(
        sheets_runtime.service_account.Credentials,
        "from_service_account_file", Mock(return_value="credentials"),
    )
    try:
        async def scenario():
            first = await sheets_runtime.run_sheets("one", lambda service: service)
            second = await sheets_runtime.run_sheets("two", lambda service: service)
            assert first is second is first_service

        asyncio.run(scenario())
        build.assert_called_once_with(
            "sheets", "v4", credentials="credentials", cache_discovery=False
        )
    finally:
        executor.shutdown(wait=True)


def test_clients_are_thread_local_and_concurrency_is_bounded(monkeypatch):
    executor = isolated_runtime(monkeypatch, 2)
    created = []
    active = 0
    maximum = 0
    lock = threading.Lock()

    def create():
        service = object()
        created.append(service)
        return service

    def operation(service):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return threading.get_ident(), service

    monkeypatch.setattr(sheets_runtime, "_create_service", create)
    try:
        async def scenario():
            results = await asyncio.gather(*(
                sheets_runtime.run_sheets(str(i), operation) for i in range(8)
            ))
            services_by_thread = {}
            for thread_id, service in results:
                previous = services_by_thread.setdefault(thread_id, service)
                assert previous is service
            assert len(services_by_thread) == 2
            assert len({id(service) for service in services_by_thread.values()}) == 2

        asyncio.run(scenario())
        assert maximum == 2
        assert len(created) == 2
    finally:
        executor.shutdown(wait=True)
