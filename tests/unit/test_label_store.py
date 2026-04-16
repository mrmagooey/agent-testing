"""Unit tests for LabelStore — load/append with JSONL files."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sec_review_framework.data.evaluation import (
    GroundTruthLabel,
    GroundTruthSource,
)
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.ground_truth.models import LabelStore


def _make_label(
    id: str,
    version: str = "1.0.0",
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
) -> GroundTruthLabel:
    return GroundTruthLabel(
        id=id,
        dataset_version=version,
        file_path=file_path,
        line_start=10,
        line_end=20,
        cwe_id="CWE-89",
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        description="SQL injection",
        source=GroundTruthSource.CVE_PATCH,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def datasets_root(tmp_path: Path) -> Path:
    """Create a temporary datasets root directory."""
    return tmp_path / "datasets"


@pytest.fixture
def label_store(datasets_root: Path) -> LabelStore:
    return LabelStore(datasets_root=datasets_root)


# ---------------------------------------------------------------------------
# Test: append and load round-trip
# ---------------------------------------------------------------------------


def test_append_and_load_round_trip(label_store: LabelStore):
    """Labels appended should be loadable with all fields preserved."""
    labels = [
        _make_label("l1", version="1.0.0"),
        _make_label("l2", version="1.0.0"),
    ]
    label_store.append("my-dataset", labels)

    loaded = label_store.load("my-dataset")

    assert len(loaded) == 2
    ids = {lbl.id for lbl in loaded}
    assert ids == {"l1", "l2"}


def test_append_is_cumulative(label_store: LabelStore):
    """Multiple appends accumulate — the JSONL file grows."""
    label_store.append("my-dataset", [_make_label("l1")])
    label_store.append("my-dataset", [_make_label("l2")])

    loaded = label_store.load("my-dataset")
    assert len(loaded) == 2


# ---------------------------------------------------------------------------
# Test: version filtering
# ---------------------------------------------------------------------------


def test_version_filtering(label_store: LabelStore):
    """load() with a version argument returns only matching labels."""
    label_store.append("my-dataset", [
        _make_label("l1", version="1.0.0"),
        _make_label("l2", version="1.1.0"),
        _make_label("l3", version="1.0.0"),
    ])

    v1_labels = label_store.load("my-dataset", version="1.0.0")
    assert len(v1_labels) == 2
    assert all(lbl.dataset_version == "1.0.0" for lbl in v1_labels)

    v11_labels = label_store.load("my-dataset", version="1.1.0")
    assert len(v11_labels) == 1
    assert v11_labels[0].id == "l2"


def test_version_filter_no_matches(label_store: LabelStore):
    """Filtering for a non-existent version returns an empty list."""
    label_store.append("my-dataset", [_make_label("l1", version="1.0.0")])

    labels = label_store.load("my-dataset", version="99.0.0")
    assert labels == []


def test_load_no_version_returns_all(label_store: LabelStore):
    """load() without version argument returns all labels."""
    label_store.append("my-dataset", [
        _make_label("l1", version="1.0.0"),
        _make_label("l2", version="2.0.0"),
    ])

    all_labels = label_store.load("my-dataset")
    assert len(all_labels) == 2


# ---------------------------------------------------------------------------
# Test: JSONL format on disk
# ---------------------------------------------------------------------------


def test_jsonl_file_format(label_store: LabelStore, datasets_root: Path):
    """Each label should occupy exactly one line in the JSONL file."""
    labels = [_make_label("l1"), _make_label("l2")]
    label_store.append("my-dataset", labels)

    jsonl_path = datasets_root / "targets" / "my-dataset" / "labels.jsonl"
    lines = [line for line in jsonl_path.read_text().splitlines() if line.strip()]

    assert len(lines) == 2
    for line in lines:
        # Each line must be valid JSON
        obj = json.loads(line)
        assert "id" in obj
