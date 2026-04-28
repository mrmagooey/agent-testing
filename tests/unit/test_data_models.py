"""Tests for FindingIdentity, ExperimentMatrix.expand(), BundleSnapshot.capture(), and RunResult serialization."""

from __future__ import annotations

import json
from datetime import datetime

from sec_review_framework.data.experiment import (
    BundleSnapshot,
    ExperimentMatrix,
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    Finding,
    FindingIdentity,
    Severity,
    VulnClass,
)
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.strategies.strategy_registry import StrategyRegistry

# ---------------------------------------------------------------------------
# FindingIdentity tests
# ---------------------------------------------------------------------------


def _make_finding(line_start: int | None, file_path: str = "app.py") -> Finding:
    return Finding(
        id="f1",
        file_path=file_path,
        line_start=line_start,
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        title="SQL Injection",
        description="desc",
        confidence=0.9,
        raw_llm_output="raw",
        produced_by="test",
        experiment_id="exp-1",
    )


def test_finding_identity_bucket_lines_0_to_9():
    """Lines 0–9 should all map to bucket 0."""
    for line in [0, 1, 5, 9]:
        f = _make_finding(line)
        ident = FindingIdentity.from_finding(f)
        assert ident.line_bucket == 0, f"Expected bucket 0 for line {line}"


def test_finding_identity_bucket_lines_10_to_19():
    """Lines 10–19 should all map to bucket 1."""
    for line in [10, 11, 15, 19]:
        f = _make_finding(line)
        ident = FindingIdentity.from_finding(f)
        assert ident.line_bucket == 1, f"Expected bucket 1 for line {line}"


def test_finding_identity_bucket_line_100():
    """Line 100 → bucket 10."""
    f = _make_finding(100)
    assert FindingIdentity.from_finding(f).line_bucket == 10


def test_finding_identity_none_line_start_maps_to_bucket_0():
    """None line_start should map to bucket 0 (treats as 0)."""
    f = _make_finding(None)
    ident = FindingIdentity.from_finding(f)
    assert ident.line_bucket == 0


def test_finding_identity_str_format():
    """__str__ should produce 'file:<vuln_class>:L<lo>-<hi>' format."""
    f = _make_finding(25)
    ident = FindingIdentity.from_finding(f)
    # bucket = 25 // 10 = 2, lo = 20, hi = 29
    s = str(ident)
    assert s.startswith("app.py:")
    assert "L20-29" in s


def test_finding_identity_str_format_bucket_0():
    """Bucket 0 → L0-9 in the string representation."""
    f = _make_finding(5)
    ident = FindingIdentity.from_finding(f)
    s = str(ident)
    assert s.startswith("app.py:")
    assert "L0-9" in s


def test_finding_identity_contains_file_and_vuln_class():
    f = _make_finding(42, file_path="views.py")
    ident = FindingIdentity.from_finding(f)
    assert ident.file_path == "views.py"
    assert ident.vuln_class == "sqli"


# ---------------------------------------------------------------------------
# Helpers for new-style ExperimentMatrix tests
# ---------------------------------------------------------------------------

_DEFAULT_BUNDLE = StrategyBundleDefault(
    system_prompt="sys",
    user_prompt_template="user",
    model_id="model-a",
    tools=frozenset(["read_file"]),
    verification="none",
    max_turns=10,
    tool_extensions=frozenset(),
)

_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0)


def _make_registry(*strategy_ids: str) -> StrategyRegistry:
    """Build a minimal StrategyRegistry with single_agent strategies for the given IDs."""
    registry = StrategyRegistry()
    for sid in strategy_ids:
        registry.register(
            UserStrategy(
                id=sid,
                name=sid,
                parent_strategy_id=None,
                orchestration_shape=OrchestrationShape.SINGLE_AGENT,
                default=_DEFAULT_BUNDLE,
                overrides=[],
                created_at=_CREATED_AT,
                is_builtin=False,
            )
        )
    return registry


def _minimal_matrix(**overrides) -> tuple[ExperimentMatrix, StrategyRegistry]:
    strategy_ids = overrides.pop("strategy_ids", ["strat-a"])
    registry = _make_registry(*strategy_ids)
    defaults: dict = dict(
        experiment_id="experiment-1",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=strategy_ids,
    )
    defaults.update(overrides)
    return ExperimentMatrix(**defaults), registry


# ---------------------------------------------------------------------------
# ExperimentMatrix.expand() tests
# ---------------------------------------------------------------------------


def test_expand_single_strategy_produces_1_run():
    """Single strategy, 1 rep → exactly 1 run."""
    matrix, registry = _minimal_matrix()
    runs = matrix.expand(registry=registry)
    assert len(runs) == 1


def test_expand_two_strategies_produces_2_runs():
    """2 strategies, 1 rep → 2 runs."""
    matrix, registry = _minimal_matrix(strategy_ids=["strat-a", "strat-b"])
    runs = matrix.expand(registry=registry)
    assert len(runs) == 2


def test_expand_num_repetitions_doubles_count():
    """num_repetitions=2 on 2 strategies → 4 total runs."""
    matrix, registry = _minimal_matrix(
        strategy_ids=["strat-a", "strat-b"],
        num_repetitions=2,
    )
    runs = matrix.expand(registry=registry)
    assert len(runs) == 4


def test_expand_run_id_format():
    """Run ID must be {experiment_id}_{strategy_id} (no _ext- suffix)."""
    matrix, registry = _minimal_matrix(strategy_ids=["builtin.single_agent"])
    runs = matrix.expand(registry=registry)
    assert len(runs) == 1
    assert runs[0].id == "experiment-1_builtin.single_agent"


def test_expand_no_rep_suffix_when_repetitions_equals_1():
    """With num_repetitions=1, run IDs must NOT contain '_rep'."""
    matrix, registry = _minimal_matrix(num_repetitions=1)
    run = matrix.expand(registry=registry)[0]
    assert "_rep" not in run.id


def test_expand_rep_suffix_present_when_repetitions_gt_1():
    """With num_repetitions > 1, each run ID must contain '_rep<N>'."""
    matrix, registry = _minimal_matrix(num_repetitions=2)
    runs = matrix.expand(registry=registry)
    assert all("_rep" in r.id for r in runs)
    ids = {r.id for r in runs}
    assert any("_rep0" in rid for rid in ids)
    assert any("_rep1" in rid for rid in ids)


def test_expand_verifier_model_id_propagated():
    """verifier_model_id on the matrix flows through to every run."""
    matrix, registry = _minimal_matrix(verifier_model_id="verifier-xyz")
    run = matrix.expand(registry=registry)[0]
    assert run.verifier_model_id == "verifier-xyz"


def test_expand_no_ext_suffix_ever():
    """The _ext- suffix must never appear in expanded run IDs."""
    matrix, registry = _minimal_matrix(strategy_ids=["strat-a", "strat-b"])
    for run in matrix.expand(registry=registry):
        assert "_ext-" not in run.id


def test_expand_run_carries_strategy_id():
    """Expanded run must carry the strategy_id for worker consumption."""
    matrix, registry = _minimal_matrix(strategy_ids=["strat-a"])
    run = matrix.expand(registry=registry)[0]
    assert run.strategy_id == "strat-a"


def test_expand_run_carries_tool_extensions_from_strategy():
    """tool_extensions on the run are derived from the strategy default bundle."""
    from sec_review_framework.data.strategy_bundle import StrategyBundleDefault

    bundle_with_lsp = StrategyBundleDefault(
        system_prompt="sys",
        user_prompt_template="user",
        model_id="model-a",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=10,
        tool_extensions=frozenset(["lsp"]),
    )
    registry = StrategyRegistry()
    registry.register(
        UserStrategy(
            id="strat-lsp",
            name="strat-lsp",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=bundle_with_lsp,
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=False,
        )
    )
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-lsp"],
    )
    run = matrix.expand(registry=registry)[0]
    assert ToolExtension.LSP in run.tool_extensions


# ---------------------------------------------------------------------------
# ExperimentRun.effective_verifier_model
# ---------------------------------------------------------------------------


def test_effective_verifier_model_uses_verifier_when_set():
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        strategy_id="strat-a",
        model_id="primary-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.WITH_VERIFICATION,
        verifier_model_id="verifier-model",
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.effective_verifier_model == "verifier-model"


def test_effective_verifier_model_falls_back_to_model_id():
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        strategy_id="strat-a",
        model_id="primary-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        verifier_model_id=None,
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.effective_verifier_model == "primary-model"


# ---------------------------------------------------------------------------
# RunResult JSON serialization round-trip
# ---------------------------------------------------------------------------


def test_run_result_json_round_trip(sample_run_result: RunResult):
    """model_dump_json → model_validate_json must produce an equal object."""
    json_str = sample_run_result.model_dump_json()
    restored = RunResult.model_validate_json(json_str)

    assert restored.status == sample_run_result.status
    assert restored.tool_call_count == sample_run_result.tool_call_count
    assert restored.estimated_cost_usd == sample_run_result.estimated_cost_usd
    assert len(restored.findings) == len(sample_run_result.findings)
    assert restored.findings[0].id == sample_run_result.findings[0].id


# ---------------------------------------------------------------------------
# BundleSnapshot.capture() — basic sanity checks (full suite in test_bundle_snapshot.py)
# ---------------------------------------------------------------------------


def _make_strategy(sid: str = "strat-a") -> UserStrategy:
    return UserStrategy(
        id=sid,
        name=sid,
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=_DEFAULT_BUNDLE,
        overrides=[],
        created_at=_CREATED_AT,
        is_builtin=False,
    )


def test_bundle_snapshot_capture_deterministic():
    """Same strategy always produces the same snapshot_id."""
    strategy = _make_strategy()
    snap1 = BundleSnapshot.capture(strategy)
    snap2 = BundleSnapshot.capture(strategy)
    assert snap1.snapshot_id == snap2.snapshot_id


def test_bundle_snapshot_id_is_16_chars():
    """snapshot_id is the first 16 hex chars of the SHA-256 digest."""
    snap = BundleSnapshot.capture(_make_strategy())
    assert len(snap.snapshot_id) == 16


def test_bundle_snapshot_carries_strategy_id():
    """snapshot.strategy_id must match the strategy's id."""
    strategy = _make_strategy("my-strategy")
    snap = BundleSnapshot.capture(strategy)
    assert snap.strategy_id == "my-strategy"


# ---------------------------------------------------------------------------
# ToolExtension serialization on ExperimentRun
# ---------------------------------------------------------------------------


def test_experiment_run_tool_extensions_default_is_empty():
    """ExperimentRun.tool_extensions defaults to empty frozenset."""
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        strategy_id="strat-a",
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.tool_extensions == frozenset()


def test_experiment_run_tool_extensions_serializes_sorted():
    """tool_extensions serializes to a sorted list of string values."""
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        strategy_id="strat-a",
        dataset_name="ds",
        dataset_version="1.0",
        tool_extensions=frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP}),
    )
    dumped = json.loads(run.model_dump_json())
    assert dumped["tool_extensions"] == ["lsp", "tree_sitter"]


def test_experiment_run_tool_extensions_round_trips():
    """model_dump_json → model_validate_json preserves tool_extensions."""
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        strategy_id="strat-a",
        dataset_name="ds",
        dataset_version="1.0",
        tool_extensions=frozenset({ToolExtension.LSP, ToolExtension.DEVDOCS}),
    )
    restored = ExperimentRun.model_validate_json(run.model_dump_json())
    assert restored.tool_extensions == frozenset({ToolExtension.LSP, ToolExtension.DEVDOCS})


# ---------------------------------------------------------------------------
# allow_unavailable_models must not appear in serialised output
# ---------------------------------------------------------------------------

def test_allow_unavailable_models_excluded_from_dump():
    """allow_unavailable_models is a submit-time flag and must not be
    persisted.  Verify model_dump_json() does not include the key."""
    matrix, _ = _minimal_matrix(allow_unavailable_models=True)
    assert matrix.allow_unavailable_models is True  # attribute readable
    dumped = json.loads(matrix.model_dump_json())
    assert "allow_unavailable_models" not in dumped


def test_allow_unavailable_models_excluded_from_model_dump():
    """model_dump() (dict form) must also exclude the key."""
    matrix, _ = _minimal_matrix(allow_unavailable_models=True)
    dumped = matrix.model_dump()
    assert "allow_unavailable_models" not in dumped


# ---------------------------------------------------------------------------
# HTTP result transport fields on ExperimentRun
# ---------------------------------------------------------------------------


def _make_run(**kwargs) -> ExperimentRun:
    """Build a minimal ExperimentRun with optional overrides."""
    defaults = dict(
        id="run-1",
        experiment_id="exp-1",
        strategy_id="builtin.single_agent",
        dataset_name="ds",
        dataset_version="1.0",
    )
    defaults.update(kwargs)
    return ExperimentRun(**defaults)


def test_experiment_run_default_transport_is_pvc():
    """ExperimentRun defaults to result_transport='pvc' (preserves old behaviour)."""
    run = _make_run()
    assert run.result_transport == "pvc"


def test_experiment_run_http_transport_fields():
    """result_transport, upload_url, upload_token can be set."""
    run = _make_run(
        result_transport="http",
        upload_url="http://coordinator/api/internal/runs/run-1/result",
        upload_token="my-secret-token",
    )
    assert run.result_transport == "http"
    assert run.upload_url == "http://coordinator/api/internal/runs/run-1/result"
    assert run.upload_token == "my-secret-token"


def test_upload_token_excluded_from_model_dump_json():
    """upload_token must not appear in model_dump_json() — never persisted to DB."""
    run = _make_run(
        result_transport="http",
        upload_url="http://coordinator/api/internal/runs/run-1/result",
        upload_token="super-secret",
    )
    dumped = json.loads(run.model_dump_json())
    assert "upload_token" not in dumped
    # upload_url and result_transport ARE included
    assert dumped["upload_url"] == "http://coordinator/api/internal/runs/run-1/result"
    assert dumped["result_transport"] == "http"


def test_upload_token_excluded_from_model_dump_dict():
    """upload_token must not appear in model_dump() (dict form) either."""
    run = _make_run(
        result_transport="http",
        upload_url="http://coordinator/api/internal/runs/run-1/result",
        upload_token="super-secret",
    )
    dumped = run.model_dump()
    assert "upload_token" not in dumped


def test_upload_token_readable_as_attribute():
    """upload_token is excluded from serialisation but readable as an attribute."""
    run = _make_run(upload_token="readable")
    assert run.upload_token == "readable"


def test_ground_truth_source_accepts_all_db_values():
    """Regression: GroundTruthSource must accept every value the DB CHECK allows.

    A worker fetches labels via HTTP and validates each via
    GroundTruthLabel.model_validate(). If the StrEnum drifts behind the DB
    CHECK constraint, every benchmark/cvefixes/crossvul run fails opaquely.
    """
    from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource

    expected = {"cve_patch", "injected", "manual", "benchmark", "cvefixes", "crossvul"}
    assert {s.value for s in GroundTruthSource} == expected

    base_label = {
        "id": "x",
        "dataset_version": "v",
        "file_path": "f.py",
        "line_start": 1,
        "line_end": 2,
        "cwe_id": "CWE-1",
        "vuln_class": VulnClass.SQLI,
        "severity": Severity.MEDIUM,
        "description": "d",
        "confidence": "likely",
        "created_at": datetime.now(),
    }
    for source_value in expected:
        label = GroundTruthLabel.model_validate({**base_label, "source": source_value})
        assert label.source == source_value


def test_experiment_run_roundtrip_without_token():
    """An ExperimentRun with upload_token can be round-tripped via JSON safely.

    The on-disk JSON (from model_dump with manual token injection) should
    deserialise correctly and preserve upload_token when present.
    """
    run = _make_run(
        result_transport="http",
        upload_url="http://coordinator/api/internal/runs/run-1/result",
        upload_token="tok123",
    )
    # Simulate what submit_experiment writes to disk (manually includes token)
    run_dict = run.model_dump(mode="json")
    run_dict["upload_token"] = run.upload_token
    import json as _json
    restored = ExperimentRun.model_validate(_json.loads(_json.dumps(run_dict, default=str)))
    assert restored.result_transport == "http"
    assert restored.upload_url == run.upload_url
    assert restored.upload_token == "tok123"
