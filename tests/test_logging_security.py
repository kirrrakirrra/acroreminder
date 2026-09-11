import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import logging_config


logging_config.configure_logging("unused-test-token")


FAKE_TOKEN = "123456789:FAKE_TEST_SECRET"


def format_log(message, *args, exc_info=None):
    formatter = logging_config.VietnamFormatter(
        "%(asctime)s - %(levelname)s - %(message)s", token=FAKE_TOKEN
    )
    record = logging.LogRecord(
        "acro-test", logging.INFO, __file__, 1, message, args, exc_info
    )
    return formatter.format(record)


def test_exact_bot_token_and_telegram_url_are_redacted():
    output = format_log(
        "token=%s url=https://api.telegram.org/bot%s/sendMessage",
        FAKE_TOKEN,
        FAKE_TOKEN,
    )

    assert FAKE_TOKEN not in output
    assert "token=[REDACTED]" in output
    assert "https://api.telegram.org/bot[REDACTED]/sendMessage" in output


def test_telegram_url_is_redacted_without_configured_token():
    formatter = logging_config.VietnamFormatter("%(message)s")
    record = logging.LogRecord(
        "acro-test",
        logging.INFO,
        __file__,
        1,
        "https://api.telegram.org/bot999999:UNKNOWN_SECRET/getMe",
        (),
        None,
    )

    output = formatter.format(record)

    assert "999999:UNKNOWN_SECRET" not in output
    assert output == "https://api.telegram.org/bot[REDACTED]/getMe"


def test_exception_traceback_is_redacted():
    try:
        raise RuntimeError(f"request failed with {FAKE_TOKEN}")
    except RuntimeError:
        import sys

        output = format_log("application failure", exc_info=sys.exc_info())

    assert FAKE_TOKEN not in output
    assert "RuntimeError: request failed with [REDACTED]" in output


def test_unrelated_application_info_message_is_unchanged():
    message = "[scheduler] Сейчас 10:30 Friday"

    output = format_log(message)

    assert output.endswith(f"INFO - {message}")


def test_routine_http_client_info_logging_is_suppressed():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
