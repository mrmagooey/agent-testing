"""Regression test for ToolCallRecord JSON serialization bug.

Before the fix, ExperimentWorker._write_jsonl crashed with:
    TypeError: Object of type ToolCallRecord is not JSON serializable
when writing tool_calls.jsonl, because ToolCallRecord was a plain @dataclass
(not a Pydantic BaseModel) and json.dumps() cannot serialize dataclasses or
datetime objects.

This test:
1. Constructs ToolCallRecord instances (mirroring ToolCallAuditLog.record()).
2. Calls _write_jsonl directly with those entries.
3. Asserts the file is written and parses as valid JSONL with expected fields.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sec_review_framework.tools.registry import ToolCallRecord, ToolCallAuditLog
from sec_review_framework.worker import ExperimentWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides) -> ToolCallRecord:
    defaults = dict(
        call_id="call-abc123",
        tool_name="read_file",
        input={"path": "/src/main.py"},
        timestamp=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        duration_ms=42,
        output_truncated=False,
    )
    defaults.update(overrides)
    return ToolCallRecord(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolCallRecordIsSerializable:
    """ToolCallRecord must serialise via model_dump_json() (Pydantic path)."""

    def test_has_model_dump_json(self) -> None:
        record = _make_record()
        assert hasattr(record, "model_dump_json"), (
            "ToolCallRecord must be a Pydantic BaseModel with model_dump_json()"
        )

    def test_model_dump_json_produces_valid_json(self) -> None:
        record = _make_record()
        raw = record.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["call_id"] == "call-abc123"
        assert parsed["tool_name"] == "read_file"
        assert parsed["duration_ms"] == 42
        assert parsed["output_truncated"] is False
        assert parsed["input"] == {"path": "/src/main.py"}


class TestWriteJsonlWithToolCallRecords:
    """_write_jsonl must not raise and must produce parseable JSONL."""

    def test_writes_single_record(self, tmp_path: Path) -> None:
        record = _make_record()
        out = tmp_path / "tool_calls.jsonl"
        worker = ExperimentWorker()
        # Must not raise TypeError
        worker._write_jsonl(out, [record])
        assert out.exists()
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["call_id"] == "call-abc123"
        assert parsed["tool_name"] == "read_file"

    def test_writes_multiple_records(self, tmp_path: Path) -> None:
        records = [
            _make_record(call_id="c1", tool_name="read_file"),
            _make_record(call_id="c2", tool_name="grep", input={"pattern": "TODO"}),
            _make_record(call_id="c3", tool_name="run_semgrep", output_truncated=True),
        ]
        out = tmp_path / "tool_calls.jsonl"
        worker = ExperimentWorker()
        worker._write_jsonl(out, records)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 3
        parsed = [json.loads(ln) for ln in lines]
        assert [p["call_id"] for p in parsed] == ["c1", "c2", "c3"]
        assert parsed[2]["output_truncated"] is True

    def test_all_expected_fields_present(self, tmp_path: Path) -> None:
        record = _make_record()
        out = tmp_path / "tool_calls.jsonl"
        ExperimentWorker()._write_jsonl(out, [record])
        parsed = json.loads(out.read_text().strip())
        expected_fields = {"call_id", "tool_name", "input", "timestamp", "duration_ms", "output_truncated"}
        assert expected_fields.issubset(parsed.keys()), (
            f"Missing fields: {expected_fields - parsed.keys()}"
        )

    def test_timestamp_serialized_as_string(self, tmp_path: Path) -> None:
        """datetime must be serialized to a string, not left as a Python object."""
        record = _make_record()
        out = tmp_path / "tool_calls.jsonl"
        ExperimentWorker()._write_jsonl(out, [record])
        parsed = json.loads(out.read_text().strip())
        assert isinstance(parsed["timestamp"], str), (
            "timestamp must be serialized as a string, not a datetime object"
        )

    def test_empty_entries_writes_empty_file(self, tmp_path: Path) -> None:
        out = tmp_path / "tool_calls.jsonl"
        ExperimentWorker()._write_jsonl(out, [])
        assert out.exists()
        assert out.read_text() == ""


class TestAuditLogRoundTrip:
    """Records produced by ToolCallAuditLog.record() must survive the write path."""

    def test_audit_log_entries_are_writable(self, tmp_path: Path) -> None:
        log = ToolCallAuditLog()
        log.record(name="read_file", input={"path": "foo.py"}, call_id="x1", duration_ms=10)
        log.record(name="grep", input={"pattern": "def "}, call_id="x2", duration_ms=5, output_truncated=True)
        out = tmp_path / "tool_calls.jsonl"
        ExperimentWorker()._write_jsonl(out, log.entries)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["tool_name"] == "read_file"
        assert first["call_id"] == "x1"
        second = json.loads(lines[1])
        assert second["output_truncated"] is True
