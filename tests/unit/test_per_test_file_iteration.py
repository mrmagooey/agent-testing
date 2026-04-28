"""Unit tests for per-test-file iteration mode (A6).

Covers:
- A dataset with ``iteration: "per-test-file"`` and K matching files produces
  K agent invocations when ``allow_benchmark_iteration=True``.
- The same dataset refuses to run when ``allow_benchmark_iteration=False`` with
  a recognizable error message.
- A dataset without ``iteration`` in metadata uses the existing whole-tree
  dispatch path (exactly 1 invocation per strategy per repetition).
- The _SingleFileTargetCodebase helper restricts list_source_files /
  get_file_tree to the named file.
- ExperimentRun.target_file round-trips through JSON serialization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sec_review_framework.data.experiment import ExperimentMatrix, ExperimentRun
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.strategies.strategy_registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(*strategy_ids: str) -> StrategyRegistry:
    bundle = StrategyBundleDefault(
        system_prompt="sys",
        user_prompt_template="user",
        model_id="claude-opus-4-5",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=10,
        tool_extensions=frozenset(),
    )
    from datetime import datetime

    registry = StrategyRegistry()
    for sid in strategy_ids:
        registry.register(
            UserStrategy(
                id=sid,
                name=sid,
                parent_strategy_id=None,
                orchestration_shape=OrchestrationShape.SINGLE_AGENT,
                default=bundle,
                overrides=[],
                created_at=datetime(2026, 1, 1),
                is_builtin=False,
            )
        )
    return registry


async def _make_coordinator(tmp_path: Path):
    """Build an ExperimentCoordinator with in-memory SQLite and no K8s."""
    from sec_review_framework.coordinator import ExperimentCoordinator
    from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
    from sec_review_framework.db import Database
    from sec_review_framework.reporting.markdown import MarkdownReportGenerator

    db = Database(tmp_path / "test.db")
    await db.init()
    storage = tmp_path / "storage"
    storage.mkdir()
    coordinator = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(
            pricing={"claude-opus-4-5": ModelPricing(input_per_million=15.0, output_per_million=75.0)}
        ),
        default_cap=4,
    )
    return coordinator, db, storage


def _seed_dataset_with_metadata(db_path: Path, storage: Path, dataset_name: str, metadata: dict, num_files: int) -> list[str]:
    """Synchronously seed a dataset row + repo files for testing.

    Returns the list of relative file paths created.
    """
    import sqlite3

    # Create the repo directory and fake test files.
    repo = storage / "datasets" / dataset_name / "repo" / "testcode"
    repo.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for i in range(num_files):
        fname = f"BenchmarkTest{i:04d}.py"
        (repo / fname).write_text(f"# test file {i}\npass\n")
        created.append(f"testcode/{fname}")

    # Insert a dataset row with the given metadata_json.
    # Schema: kind IN ('git','derived'), origin_url+origin_commit required for 'git'.
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            INSERT INTO datasets
                (name, kind, origin_url, origin_commit, cve_id, base_dataset,
                 recipe_json, metadata_json, created_at, materialized_at)
            VALUES
                (?, 'git', 'https://example.com/repo.git', 'abc123',
                 NULL, NULL, '{}', ?, datetime('now'), datetime('now'))
            """,
            (dataset_name, json.dumps(metadata)),
        )
        con.commit()
    return created


# ---------------------------------------------------------------------------
# 1. per-test-file with allow_benchmark_iteration=True → K runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_test_file_produces_k_runs(tmp_path: Path):
    """K matched files with allow_benchmark_iteration=True → K runs per strategy."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    K = 5
    _seed_dataset_with_metadata(
        db_path=tmp_path / "test.db",
        storage=storage,
        dataset_name="bench-ds",
        metadata={"iteration": "per-test-file", "test_glob": "testcode/BenchmarkTest*.py"},
        num_files=K,
    )

    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp-bench",
        dataset_name="bench-ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        num_repetitions=1,
        allow_benchmark_iteration=True,
    )
    base_runs = matrix.expand(registry=registry)
    # base_runs has 1 run; after expansion should have K runs
    expanded = await coordinator._expand_benchmark_iteration(base_runs, matrix)
    assert len(expanded) == K, f"Expected {K} runs, got {len(expanded)}"


@pytest.mark.asyncio
async def test_per_test_file_two_strategies_produces_2k_runs(tmp_path: Path):
    """2 strategies × K files → 2K runs."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    K = 3
    _seed_dataset_with_metadata(
        db_path=tmp_path / "test.db",
        storage=storage,
        dataset_name="bench-ds2",
        metadata={"iteration": "per-test-file", "test_glob": "testcode/BenchmarkTest*.py"},
        num_files=K,
    )

    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp-multi",
        dataset_name="bench-ds2",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
        num_repetitions=1,
        allow_benchmark_iteration=True,
    )
    base_runs = matrix.expand(registry=registry)
    assert len(base_runs) == 2  # sanity: 2 base runs
    expanded = await coordinator._expand_benchmark_iteration(base_runs, matrix)
    assert len(expanded) == 2 * K


@pytest.mark.asyncio
async def test_per_test_file_runs_carry_target_file(tmp_path: Path):
    """Each expanded run carries a non-None target_file."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    K = 4
    _seed_dataset_with_metadata(
        db_path=tmp_path / "test.db",
        storage=storage,
        dataset_name="bench-tf",
        metadata={"iteration": "per-test-file", "test_glob": "testcode/BenchmarkTest*.py"},
        num_files=K,
    )

    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp-tf",
        dataset_name="bench-tf",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        allow_benchmark_iteration=True,
    )
    base_runs = matrix.expand(registry=registry)
    expanded = await coordinator._expand_benchmark_iteration(base_runs, matrix)

    assert all(r.target_file is not None for r in expanded)
    # Each target_file should be a relative path within testcode/
    assert all(r.target_file.startswith("testcode/") for r in expanded)
    # All target_files should be distinct
    assert len({r.target_file for r in expanded}) == K


# ---------------------------------------------------------------------------
# 2. per-test-file without allow_benchmark_iteration → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_test_file_blocked_without_flag(tmp_path: Path):
    """Dataset requesting per-test-file iteration without the flag raises ValueError."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    _seed_dataset_with_metadata(
        db_path=tmp_path / "test.db",
        storage=storage,
        dataset_name="bench-blocked",
        metadata={"iteration": "per-test-file", "test_glob": "testcode/BenchmarkTest*.py"},
        num_files=3,
    )

    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp-blocked",
        dataset_name="bench-blocked",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        # allow_benchmark_iteration defaults to False — NOT set here
    )
    base_runs = matrix.expand(registry=registry)

    with pytest.raises(ValueError, match="allow_benchmark_iteration"):
        await coordinator._expand_benchmark_iteration(base_runs, matrix)


# ---------------------------------------------------------------------------
# 3. Dataset without iteration metadata → 1 invocation per strategy (unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_iteration_metadata_uses_whole_tree(tmp_path: Path):
    """A dataset without iteration in metadata produces exactly 1 run per strategy."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    _seed_dataset_with_metadata(
        db_path=tmp_path / "test.db",
        storage=storage,
        dataset_name="normal-ds",
        metadata={},  # no iteration key
        num_files=10,
    )

    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp-normal",
        dataset_name="normal-ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
    )
    base_runs = matrix.expand(registry=registry)
    expanded = await coordinator._expand_benchmark_iteration(base_runs, matrix)

    # Must be byte-identical (same list) — no fan-out
    assert len(expanded) == 1
    assert expanded[0].target_file is None


@pytest.mark.asyncio
async def test_missing_iteration_key_is_unchanged(tmp_path: Path):
    """iteration key missing from metadata_json → no expansion, all target_file=None."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    _seed_dataset_with_metadata(
        db_path=tmp_path / "test.db",
        storage=storage,
        dataset_name="plain-ds",
        metadata={"description": "not a benchmark dataset"},
        num_files=5,
    )

    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp-plain",
        dataset_name="plain-ds",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
    )
    base_runs = matrix.expand(registry=registry)
    assert len(base_runs) == 2
    expanded = await coordinator._expand_benchmark_iteration(base_runs, matrix)
    assert len(expanded) == 2
    assert all(r.target_file is None for r in expanded)


# ---------------------------------------------------------------------------
# 4. _SingleFileTargetCodebase behaviour
# ---------------------------------------------------------------------------


def test_single_file_target_codebase_list_source_files(tmp_path: Path):
    """_SingleFileTargetCodebase.list_source_files returns only the target file."""
    from sec_review_framework.worker import _SingleFileTargetCodebase

    repo = tmp_path / "repo"
    (repo / "testcode").mkdir(parents=True)
    (repo / "testcode" / "FileA.py").write_text("pass")
    (repo / "testcode" / "FileB.py").write_text("pass")

    target = _SingleFileTargetCodebase(repo, "testcode/FileA.py")
    files = target.list_source_files()
    assert files == ["testcode/FileA.py"]


def test_single_file_target_codebase_get_file_tree(tmp_path: Path):
    """_SingleFileTargetCodebase.get_file_tree surfaces only the target file."""
    from sec_review_framework.worker import _SingleFileTargetCodebase

    repo = tmp_path / "repo"
    (repo / "testcode").mkdir(parents=True)
    (repo / "testcode" / "FileA.py").write_text("x = 1\n")
    (repo / "testcode" / "FileB.py").write_text("y = 2\n")

    target = _SingleFileTargetCodebase(repo, "testcode/FileA.py")
    tree = target.get_file_tree()
    # The tree should have exactly one child entry for FileA.py
    children = tree.get("children", [])
    assert len(children) == 1
    assert children[0]["name"] == "testcode/FileA.py"


def test_single_file_target_codebase_read_file_still_works(tmp_path: Path):
    """read_file on _SingleFileTargetCodebase can access any file in the repo."""
    from sec_review_framework.worker import _SingleFileTargetCodebase

    repo = tmp_path / "repo"
    (repo / "testcode").mkdir(parents=True)
    (repo / "testcode" / "FileA.py").write_text("hello = 42\n")
    (repo / "other.py").write_text("world = 1\n")

    target = _SingleFileTargetCodebase(repo, "testcode/FileA.py")
    # Direct read of the target file
    assert "hello" in target.read_file("testcode/FileA.py")
    # Can still read any repo file (full repo access for follow-imports)
    assert "world" in target.read_file("other.py")


def test_single_file_missing_file_returns_empty_list(tmp_path: Path):
    """If the target_file doesn't exist on disk, list_source_files returns []."""
    from sec_review_framework.worker import _SingleFileTargetCodebase

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    target = _SingleFileTargetCodebase(repo, "nonexistent/File.py")
    assert target.list_source_files() == []


# ---------------------------------------------------------------------------
# 5. ExperimentRun.target_file round-trips through JSON
# ---------------------------------------------------------------------------


def test_experiment_run_target_file_round_trips():
    """target_file is preserved through model_dump_json / model_validate_json."""
    run = ExperimentRun(
        id="exp_strat_file-testcode-benchmarktest0001-py",
        experiment_id="exp",
        strategy_id="strat",
        dataset_name="ds",
        dataset_version="1.0",
        target_file="testcode/BenchmarkTest0001.py",
    )
    serialized = run.model_dump_json()
    restored = ExperimentRun.model_validate_json(serialized)
    assert restored.target_file == "testcode/BenchmarkTest0001.py"


def test_experiment_run_target_file_defaults_to_none():
    """target_file defaults to None (existing whole-tree behaviour)."""
    run = ExperimentRun(
        id="run-id",
        experiment_id="exp",
        strategy_id="strat",
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.target_file is None


def test_allow_benchmark_iteration_excluded_from_serialization():
    """allow_benchmark_iteration must not appear in matrix model_dump_json."""
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        allow_benchmark_iteration=True,
    )
    dumped = matrix.model_dump_json()
    data = json.loads(dumped)
    assert "allow_benchmark_iteration" not in data


# ---------------------------------------------------------------------------
# 6. Default glob (absent test_glob) falls back to **/*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_glob_matches_all_files(tmp_path: Path):
    """When test_glob is absent, the default **/* matches all files."""
    coordinator, db, storage = await _make_coordinator(tmp_path)
    # Create 3 files at different locations
    repo = storage / "datasets" / "no-glob-ds" / "repo"
    (repo / "a").mkdir(parents=True)
    (repo / "b").mkdir(parents=True)
    (repo / "a" / "file1.py").write_text("pass")
    (repo / "b" / "file2.py").write_text("pass")
    (repo / "root.py").write_text("pass")

    import sqlite3
    with sqlite3.connect(tmp_path / "test.db") as con:
        con.execute(
            """INSERT INTO datasets
                   (name, kind, origin_url, origin_commit, cve_id, base_dataset,
                    recipe_json, metadata_json, created_at, materialized_at)
               VALUES (?, 'git', 'https://example.com/repo.git', 'abc123',
                       NULL, NULL, '{}', ?, datetime('now'), datetime('now'))""",
            ("no-glob-ds", json.dumps({"iteration": "per-test-file"})),
        )
        con.commit()

    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp-no-glob",
        dataset_name="no-glob-ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        allow_benchmark_iteration=True,
    )
    base_runs = matrix.expand(registry=registry)
    expanded = await coordinator._expand_benchmark_iteration(base_runs, matrix)
    # Should have 3 runs — one per file
    assert len(expanded) == 3
