"""`hub_api.main`'s `_HealthzAccessFilter` — the uvicorn `access_logger.info(...)`
call it filters is a positional-args API we don't control the shape of
(`uvicorn/protocols/http/h11_impl.py`), so this locks in exactly the tuple
shape it expects rather than trusting a rewrite of uvicorn's call site to
still match our assumptions silently.
"""

from __future__ import annotations

import logging

from hub_api.main import _HealthzAccessFilter


def _record(args: tuple[object, ...]) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=args,
        exc_info=None,
    )


def test_drops_a_successful_healthz_hit() -> None:
    record = _record(("10.0.0.1:1234", "GET", "/healthz", "1.1", 200))

    assert _HealthzAccessFilter().filter(record) is False


def test_keeps_a_failing_healthz_hit() -> None:
    record = _record(("10.0.0.1:1234", "GET", "/healthz", "1.1", 503))

    assert _HealthzAccessFilter().filter(record) is True


def test_keeps_healthz_with_a_query_string() -> None:
    # get_path_with_query_string (uvicorn) appends `?...` to the path — still
    # a healthz hit either way, so this should filter the same as the bare path.
    record = _record(("10.0.0.1:1234", "GET", "/healthz?probe=1", "1.1", 200))

    assert _HealthzAccessFilter().filter(record) is False


def test_keeps_other_routes() -> None:
    record = _record(("10.0.0.1:1234", "GET", "/api/chat", "1.1", 200))

    assert _HealthzAccessFilter().filter(record) is True


def test_keeps_records_with_an_unexpected_args_shape() -> None:
    """Defensive: if uvicorn ever changes its access-log call signature, fail
    open (keep logging) rather than silently swallowing real traffic."""
    record = _record(("just one field",))

    assert _HealthzAccessFilter().filter(record) is True
