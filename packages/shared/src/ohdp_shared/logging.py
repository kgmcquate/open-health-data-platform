"""Structured logging with secret redaction.

Dagster run logs are world-readable (ARCHITECTURE.md §5). Any logger that can
reach a Dagster run must route through this module so connection strings, tokens,
and keys never render in the public UI.

The redaction here is deliberately conservative: it errs toward over-redacting.
It is a backstop, not a licence to log secrets.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

# Substring match on the *key* of a structured field -> value replaced wholesale.
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "auth",
    "credential",
    "dsn",
    "connection_string",
    "conn_str",
)

# Patterns redacted anywhere they appear in a rendered string value.
_VALUE_PATTERNS = (
    # postgres / generic URI with inline credentials
    re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)[^:/\s]+:[^@/\s]+@", re.IGNORECASE),
    # AWS-style access key ids
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # long opaque bearer-ish blobs
    re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"),
)

_REDACTED = "***redacted***"


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for pattern in _VALUE_PATTERNS:
            if pattern is _VALUE_PATTERNS[0]:
                out = pattern.sub(r"\g<scheme>" + _REDACTED + "@", out)
            else:
                out = pattern.sub(_REDACTED, out)
        return out
    if isinstance(value, dict):
        return {k: _redact_event_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def _redact_event_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    return _redact_value(value)


def redaction_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: redact sensitive keys and value patterns."""
    return {k: _redact_event_value(k, v) for k, v in event_dict.items()}


def configure_logging(*, json: bool = True, level: str = "INFO") -> None:
    """Configure structlog process-wide. Call once at process start."""
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redaction_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_to_int(level)),
        cache_logger_on_first_use=True,
    )


def _level_to_int(level: str) -> int:
    import logging

    return getattr(logging, level.upper(), logging.INFO)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
