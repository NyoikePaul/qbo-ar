import json
import os
import stat
from datetime import datetime, timezone

import pytest

from qbo_ar.audit import AuditLog, redact


def fixed():
    return datetime(2026, 11, 5, 8, 0, 0, tzinfo=timezone.utc)


def test_record_writes_one_json_line(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", run_id="r1", clock=fixed)
    log.record("match_decision", txn_id="UJ1", status="matched")
    entry = json.loads((tmp_path / "a.jsonl").read_text())
    assert entry == {
        "ts": "2026-11-05T08:00:00+00:00", "run_id": "r1", "event": "match_decision",
        "txn_id": "UJ1", "status": "matched",
    }


def test_records_append_under_one_run_id(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", clock=fixed)
    log.record("one")
    log.record("two")
    rows = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows] == ["one", "two"]
    assert rows[0]["run_id"] == rows[1]["run_id"] == log.run_id


def test_secrets_are_redacted_recursively():
    out = redact({"refresh_token": "x", "nested": [{"Client_Secret": "y", "ok": 1}]})
    assert out == {"refresh_token": "***", "nested": [{"Client_Secret": "***", "ok": 1}]}


def test_caller_cannot_override_reserved_fields(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", run_id="r1", clock=fixed)
    entry = log.record("e", ts="forged", run_id="forged")
    assert (entry["ts"], entry["run_id"], entry["event"]) == (
        "2026-11-05T08:00:00+00:00", "r1", "e")
    with pytest.raises(TypeError):  # `event` is positional-only in practice
        log.record("e", event="forged")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_file_is_owner_only(tmp_path):
    AuditLog(tmp_path / "a.jsonl").record("e")
    assert stat.S_IMODE((tmp_path / "a.jsonl").stat().st_mode) == 0o600
