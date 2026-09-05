"""Structured JSON logging.

Every log line is one JSON object with sorted keys and a UTC timestamp. Local
time never appears: an operator reading logs from two hosts must be able to
interleave them without knowing either host's zone.

``HeliosError`` context is emitted as structured fields rather than being
flattened into prose, so a failure is machine-queryable.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from helios.errors import HeliosError

#: Attributes the stdlib puts on every record; anything else a caller attached
#: via ``extra=`` is treated as a structured field and published.
_STANDARD_RECORD_ATTRIBUTES = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)

_LOGGER_ROOT = "helios"


def _jsonable(value: Any) -> Any:
    """Best-effort reduction to JSON types. Logging never raises."""
    from decimal import Decimal

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


class JsonFormatter(logging.Formatter):
    """Formats a log record as one deterministic JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts_utc": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRIBUTES or key.startswith("_"):
                continue
            payload[key] = _jsonable(value)
        if record.exc_info:
            exception = record.exc_info[1]
            payload["error_type"] = (
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            )
            payload["error_message"] = str(exception) if exception else ""
            if isinstance(exception, HeliosError):
                payload["error_context"] = _jsonable(dict(exception.context))
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str, *, stream: Any = None) -> logging.Logger:
    """Install JSON logging on the ``helios`` logger tree.

    ``level`` comes from validated configuration; this function does not
    invent one.
    """
    resolved = logging.getLevelName(str(level).upper())
    if not isinstance(resolved, int):
        raise HeliosError("unknown log level", level=level)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(_LOGGER_ROOT)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(resolved)
    logger.propagate = False
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """A logger inside the ``helios`` tree."""
    if not name or name == _LOGGER_ROOT:
        return logging.getLogger(_LOGGER_ROOT)
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")
