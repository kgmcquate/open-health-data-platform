from ohdp_shared.logging import redaction_processor


def _run(event: dict) -> dict:
    return redaction_processor(None, "info", event)


def test_sensitive_keys_are_redacted():
    out = _run({"event": "connect", "password": "hunter2", "api_key": "abc123"})
    assert out["password"] == "***redacted***"
    assert out["api_key"] == "***redacted***"
    assert out["event"] == "connect"


def test_inline_uri_credentials_are_stripped():
    out = _run({"event": "postgresql://user:s3cret@db.internal:5432/app"})
    assert "s3cret" not in out["event"]
    assert out["event"].startswith("postgresql://")


def test_long_opaque_blob_is_redacted():
    token = "x" * 48
    out = _run({"note": f"bearer {token}"})
    assert token not in out["note"]


def test_nested_structures_are_walked():
    out = _run({"ctx": {"secret": "v", "safe": "ok"}})
    assert out["ctx"]["secret"] == "***redacted***"
    assert out["ctx"]["safe"] == "ok"
