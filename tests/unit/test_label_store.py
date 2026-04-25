"""Unit tests for LabelStore stub — verifies the deprecated API raises NotImplementedError."""

from pathlib import Path

import pytest

from sec_review_framework.ground_truth.models import LabelStore


@pytest.fixture
def datasets_root(tmp_path: Path) -> Path:
    """Create a temporary datasets root directory."""
    return tmp_path / "datasets"


@pytest.fixture
def label_store(datasets_root: Path) -> LabelStore:
    return LabelStore(datasets_root=datasets_root)


def test_label_store_construction_succeeds(datasets_root: Path):
    """LabelStore can be constructed without error (backward-compat)."""
    store = LabelStore(datasets_root=datasets_root)
    assert store is not None


def test_label_store_load_raises_not_implemented(label_store: LabelStore):
    """LabelStore.load raises NotImplementedError — use Database.list_dataset_labels."""
    with pytest.raises(NotImplementedError, match="list_dataset_labels"):
        label_store.load("any-dataset")


def test_label_store_load_with_version_raises_not_implemented(label_store: LabelStore):
    """LabelStore.load with version raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        label_store.load("any-dataset", version="v1")


def test_label_store_append_raises_not_implemented(label_store: LabelStore):
    """LabelStore.append raises NotImplementedError — use Database.append_dataset_labels."""
    with pytest.raises(NotImplementedError, match="append_dataset_labels"):
        label_store.append("any-dataset", [])
