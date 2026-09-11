import logging
import re
from datetime import datetime

import pytz


class VietnamFormatter(logging.Formatter):
    tz = pytz.timezone("Asia/Ho_Chi_Minh")
    telegram_bot_url = re.compile(
        r"(https://api\.telegram\.org/bot)[^/\s]+", re.IGNORECASE
    )

    def __init__(self, fmt=None, datefmt=None, style="%", token=None):
        super().__init__(fmt, datefmt, style)
        self.token = token

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, self.tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

    def redact(self, text):
        if self.token:
            text = text.replace(self.token, "[REDACTED]")
        return self.telegram_bot_url.sub(r"\1[REDACTED]", text)

    def formatException(self, exc_info):
        return self.redact(super().formatException(exc_info))

    def format(self, record):
        # Redact after arguments and exception text have been formatted, so the
        # final text emitted by this formatter cannot contain the credential.
        return self.redact(super().format(record))


def configure_logging(bot_token):
    formatter = VietnamFormatter(
        "%(asctime)s - %(levelname)s - %(message)s", token=bot_token
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
