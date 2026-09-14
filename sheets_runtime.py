"""Bounded, thread-safe execution for all Google Sheets requests."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import threading
import time

from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
MAX_WORKERS = 2

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="sheets")
_thread_local = threading.local()


def _create_service():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    ).spreadsheets()


def _service():
    """Return the client owned exclusively by the current Sheets worker."""
    service = getattr(_thread_local, "service", None)
    if service is None:
        service = _create_service()
        _thread_local.service = service
        logging.info("Created Sheets client for worker %s", threading.current_thread().name)
    return service


def _execute(label, operation):
    started = time.monotonic()
    try:
        return operation(_service())
    finally:
        logging.info("Sheets %s completed in %.3fs", label, time.monotonic() - started)


async def run_sheets(label, operation):
    """Asynchronously queue an operation on the bounded Sheets executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _execute, label, operation)


def run_sheets_sync(label, operation):
    """Run startup-only synchronous work through the same bounded executor."""
    return _executor.submit(_execute, label, operation).result()
