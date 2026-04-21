"""Security and correctness tests for Coordinator.get_file_content."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from sec_review_framework.coordinator import ExperimentCoordinator as Coordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path) -> tuple[Coordinator, Path]:
    """Return a coordinator whose storage_root is tmp_path, plus the dataset dir."""
    dataset_dir = tmp_path / "datasets" / "myds" / "repo"
    dataset_dir.mkdir(parents=True)
    coord = Coordinator.__new__(Coordinator)
    coord.storage_root = tmp_path
    coord.get_labels = MagicMock(return_value=[])
    return coord, dataset_dir


def _write(dataset_dir: Path, rel: str, content: bytes) -> None:
    target = dataset_dir.parent / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


# ---------------------------------------------------------------------------
# Path-traversal — the primary security concern
# ---------------------------------------------------------------------------


def test_rejects_path_traversal_single_dotdot(tmp_path: Path):
    coord, _ = _make_coordinator(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    with pytest.raises(HTTPException) as exc_info:
        coord.get_file_content("myds", "../secret.txt")
    assert exc_info.value.status_code == 400
    assert "escapes dataset" in exc_info.value.detail


def test_rejects_path_traversal_nested_dotdot(tmp_path: Path):
    coord, _ = _make_coordinator(tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        coord.get_file_content("myds", "../../etc/passwd")
    assert exc_info.value.status_code == 400


def test_rejects_absolute_path(tmp_path: Path):
    coord, _ = _make_coordinator(tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        coord.get_file_content("myds", "/etc/passwd")
    assert exc_info.value.status_code == 400
    assert "escapes dataset" in exc_info.value.detail


def test_rejects_nul_byte(tmp_path: Path):
    coord, _ = _make_coordinator(tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        coord.get_file_content("myds", "safe.py\x00/etc/passwd")
    assert exc_info.value.status_code == 400
    assert "NUL" in exc_info.value.detail


def test_rejects_symlink_escape(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("classified")
    link = dataset_dir.parent / "escape_link"
    link.symlink_to(outside_dir)
    with pytest.raises(HTTPException) as exc_info:
        coord.get_file_content("myds", "escape_link/secret.txt")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Binary detection
# ---------------------------------------------------------------------------


def test_binary_detection(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    bin_file = dataset_dir.parent / "data.bin"
    bin_file.write_bytes(b"ELF\x7f\x00binary\x00data")
    result = coord.get_file_content("myds", "data.bin")
    assert result["binary"] is True
    assert result["content"] == ""
    assert result["size_bytes"] > 0


def test_binary_detection_nul_after_8kb(tmp_path: Path):
    """A NUL byte that appears after the 8 KB probe window is not detected as binary."""
    coord, dataset_dir = _make_coordinator(tmp_path)
    safe_file = dataset_dir.parent / "almost_text.txt"
    safe_file.write_bytes(b"a" * 8200 + b"\x00")
    result = coord.get_file_content("myds", "almost_text.txt")
    # Not flagged as binary — NUL is beyond the probe window.
    assert result.get("binary") is not True


# ---------------------------------------------------------------------------
# Large-file truncation
# ---------------------------------------------------------------------------


def test_large_file_truncation(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    big_file = dataset_dir.parent / "huge.py"
    big_file.write_bytes(b"x" * (3 * 1024 * 1024))  # 3 MiB > 2 MiB limit
    result = coord.get_file_content("myds", "huge.py")
    assert result.get("truncated") is True
    assert "[truncated]" in result["content"]
    assert result["size_bytes"] == 3 * 1024 * 1024


def test_small_file_not_truncated(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    small_file = dataset_dir.parent / "small.py"
    small_file.write_text("print('hello')\n")
    result = coord.get_file_content("myds", "small.py")
    assert result.get("truncated") is None or result.get("truncated") is False


# ---------------------------------------------------------------------------
# Labels attached for matching path
# ---------------------------------------------------------------------------


def test_labels_attached_for_matching_path(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    src_file = dataset_dir.parent / "src" / "auth.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text("pass\n")
    label = {
        "label_id": "lbl-1",
        "file_path": "src/auth.py",
        "line_start": 1,
        "line_end": 1,
        "vuln_class": "sqli",
    }
    coord.get_labels = MagicMock(return_value=[label])
    result = coord.get_file_content("myds", "src/auth.py")
    assert len(result["labels"]) == 1
    assert result["labels"][0]["label_id"] == "lbl-1"


def test_labels_not_attached_for_different_path(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    src_file = dataset_dir.parent / "other.py"
    src_file.write_text("pass\n")
    label = {"label_id": "lbl-2", "file_path": "src/auth.py"}
    coord.get_labels = MagicMock(return_value=[label])
    result = coord.get_file_content("myds", "other.py")
    assert result["labels"] == []


# ---------------------------------------------------------------------------
# Highlight params echoed back
# ---------------------------------------------------------------------------


def test_highlight_params_echoed(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    f = dataset_dir.parent / "a.py"
    f.write_text("line1\nline2\nline3\n")
    result = coord.get_file_content("myds", "a.py", start=2, end=3)
    assert result["highlight_start"] == 2
    assert result["highlight_end"] == 3


def test_highlight_params_absent_when_not_given(tmp_path: Path):
    coord, dataset_dir = _make_coordinator(tmp_path)
    f = dataset_dir.parent / "b.py"
    f.write_text("line1\n")
    result = coord.get_file_content("myds", "b.py")
    assert "highlight_start" not in result
    assert "highlight_end" not in result


# ---------------------------------------------------------------------------
# 404 for missing file
# ---------------------------------------------------------------------------


def test_missing_file_raises_404(tmp_path: Path):
    coord, _ = _make_coordinator(tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        coord.get_file_content("myds", "nonexistent.py")
    assert exc_info.value.status_code == 404
