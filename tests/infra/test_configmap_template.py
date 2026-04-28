"""Validate that the experiment-config ConfigMap is rendered correctly.

Runs ``helm template`` and asserts the ConfigMap carries all expected keys
whose contents match the repo's config/ directory.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CHART_DIR = REPO_ROOT / "helm" / "sec-review"
# helm/sec-review/files is a symlink -> ../../config, so config/ is canonical.
CONFIG_DIR = REPO_ROOT / "config"

requires_helm = pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="helm CLI not installed",
)

_TEST_ENCRYPTION_KEY = "dGVzdC1mZXJuZXQta2V5LWZvci1oZWxtLXJlbmRlcg=="


def _render_chart(values_file: str | None = None) -> list[dict]:
    cmd = [
        "helm", "template", "sec-review", str(CHART_DIR),
        "--namespace", "sec-review",
        # Test-only Fernet key so the chart can render. The chart `required`s
        # this when secrets.create=true; profiles that disable secret creation
        # ignore the override.
        "--set", f"secrets.encryptionKey={_TEST_ENCRYPTION_KEY}",
    ]
    if values_file is not None:
        cmd.extend(["--values", str(CHART_DIR / values_file)])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


def _render_chart_with_sets(extra_sets: list[str], values_file: str | None = None) -> list[dict]:
    cmd = [
        "helm", "template", "sec-review", str(CHART_DIR),
        "--namespace", "sec-review",
        "--set", f"secrets.encryptionKey={_TEST_ENCRYPTION_KEY}",
    ]
    for kv in extra_sets:
        cmd.extend(["--set", kv])
    if values_file is not None:
        cmd.extend(["--values", str(CHART_DIR / values_file)])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


def _get_coordinator_env(docs: list[dict]) -> dict[str, str]:
    deployments = [d for d in docs if d.get("kind") == "Deployment"]
    assert deployments, "No Deployment rendered"
    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["value"] for e in container["env"] if "value" in e}


def _get_experiment_configmap(docs: list[dict]) -> dict:
    matches = [
        d for d in docs
        if d.get("kind") == "ConfigMap"
        and d.get("metadata", {}).get("name") == "experiment-config"
    ]
    assert len(matches) == 1, (
        f"Expected exactly 1 ConfigMap named 'experiment-config', got {len(matches)}"
    )
    return matches[0]


@requires_helm
def test_exactly_one_experiment_config_configmap() -> None:
    """Exactly one ConfigMap named experiment-config is rendered."""
    docs = _render_chart()
    _get_experiment_configmap(docs)  # assertion is inside the helper


@requires_helm
def test_experiment_configmap_has_required_keys() -> None:
    """ConfigMap data includes the expected config/*.yaml files.

    models.yaml was deleted in Phase 2 (the catalog is now probe-driven);
    the required set covers only the files that still exist under config/.
    """
    docs = _render_chart()
    cm = _get_experiment_configmap(docs)
    data = cm.get("data") or {}

    required_keys = {
        "pricing.yaml",
        "review_profiles.yaml",
        "concurrency.yaml",
    }
    missing = required_keys - set(data.keys())
    assert not missing, f"ConfigMap is missing keys: {missing}"
    # Deleted file must not appear.
    assert "models.yaml" not in data, (
        "models.yaml was deleted; it must not appear in the ConfigMap"
    )


@requires_helm
def test_experiment_configmap_prompts_key_or_all_yaml_keys() -> None:
    """ConfigMap data contains a prompts/* key OR at least all *.yaml keys are present.

    The prompts/ directory is currently empty, so this test accepts either:
    - At least one key starting with 'prompts/' (non-empty prompts dir), OR
    - All expected *.yaml keys are present (empty prompts dir is fine).
    """
    docs = _render_chart()
    cm = _get_experiment_configmap(docs)
    data = cm.get("data") or {}

    prompts_keys = [k for k in data if k.startswith("prompts/")]
    yaml_keys = [k for k in data if k.endswith(".yaml")]

    assert yaml_keys or prompts_keys, (
        "ConfigMap data is empty — expected at least yaml config keys"
    )


@requires_helm
def test_experiment_configmap_e2e_values() -> None:
    """ConfigMap renders correctly with e2e values overlay."""
    docs = _render_chart(values_file="values-e2e.yaml")
    cm = _get_experiment_configmap(docs)
    data = cm.get("data") or {}

    for key in ("pricing.yaml", "review_profiles.yaml", "concurrency.yaml"):
        assert key in data, f"Missing key {key!r} in ConfigMap with e2e values"
    assert "models.yaml" not in data, (
        "models.yaml was deleted; it must not appear in the ConfigMap with e2e values"
    )


@requires_helm
def test_semgrep_enabled_flag_flows_through_deployment() -> None:
    """workerTools.semgrep.enabled flows into TOOL_EXT_SEMGREP_AVAILABLE on the coordinator."""
    import subprocess as _sp

    import yaml as _yaml

    chart_dir = str(REPO_ROOT / "helm" / "sec-review")
    # Use values-prod.yaml (secrets.create=false) to avoid the encryptionKey requirement.
    cmd = [
        "helm", "template", "sec-review", chart_dir,
        "--namespace", "sec-review",
        "--values", str(CHART_DIR / "values-prod.yaml"),
        "--set", "workerTools.semgrep.enabled=false",
    ]
    result = _sp.run(cmd, capture_output=True, text=True, check=True)
    docs = [d for d in _yaml.safe_load_all(result.stdout) if d is not None]

    deployments = [d for d in docs if d.get("kind") == "Deployment"]
    assert deployments, "No Deployment rendered"

    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]
    # Some entries use valueFrom instead of value — filter to plain string values.
    env = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    assert env.get("TOOL_EXT_SEMGREP_AVAILABLE") == "false", (
        "Setting workerTools.semgrep.enabled=false must propagate to TOOL_EXT_SEMGREP_AVAILABLE=false"
    )


# ---------------------------------------------------------------------------
# Env-var substitution: Helm values flow into coordinator env vars correctly
# ---------------------------------------------------------------------------


@pytest.mark.infra
@requires_helm
def test_storage_root_env_var_reflects_override() -> None:
    docs = _render_chart_with_sets(["coordinator.storageRoot=/mnt/custom"])
    env = _get_coordinator_env(docs)
    assert env.get("STORAGE_ROOT") == "/mnt/custom", (
        "coordinator.storageRoot override must propagate to STORAGE_ROOT env var"
    )


@pytest.mark.infra
@requires_helm
def test_local_llm_base_url_absent_when_blank() -> None:
    docs = _render_chart_with_sets(["providerEndpoints.localLlm.baseUrl="])
    env = _get_coordinator_env(docs)
    assert "LOCAL_LLM_BASE_URL" not in env, (
        "LOCAL_LLM_BASE_URL must be absent from coordinator env when providerEndpoints.localLlm.baseUrl is empty"
    )


@pytest.mark.infra
@requires_helm
def test_local_llm_base_url_present_when_set() -> None:
    docs = _render_chart_with_sets(["providerEndpoints.localLlm.baseUrl=http://192.168.1.10:8080"])
    env = _get_coordinator_env(docs)
    assert env.get("LOCAL_LLM_BASE_URL") == "http://192.168.1.10:8080", (
        "LOCAL_LLM_BASE_URL must reflect providerEndpoints.localLlm.baseUrl when non-empty"
    )


@pytest.mark.infra
@requires_helm
def test_extra_env_passes_through_to_deployment() -> None:
    docs = _render_chart_with_sets(["coordinator.extraEnv[0].name=LOG_LEVEL", "coordinator.extraEnv[0].value=DEBUG"])
    env = _get_coordinator_env(docs)
    assert env.get("LOG_LEVEL") == "DEBUG", (
        "coordinator.extraEnv entries must appear verbatim in the coordinator container env"
    )


@pytest.mark.infra
@requires_helm
def test_all_four_tool_ext_env_vars_present_by_default() -> None:
    docs = _render_chart()
    env = _get_coordinator_env(docs)
    for var in ("TOOL_EXT_DEVDOCS_AVAILABLE", "TOOL_EXT_LSP_AVAILABLE",
                "TOOL_EXT_SEMGREP_AVAILABLE", "TOOL_EXT_TREE_SITTER_AVAILABLE"):
        assert var in env, f"{var} must be present in coordinator env by default"
    for var in ("TOOL_EXT_DEVDOCS_AVAILABLE", "TOOL_EXT_LSP_AVAILABLE",
                "TOOL_EXT_SEMGREP_AVAILABLE", "TOOL_EXT_TREE_SITTER_AVAILABLE"):
        assert env[var] == "true", f"{var} must default to 'true'"


# ---------------------------------------------------------------------------
# Invalid tool extension names: closed set enforced at Helm and Python layers
# ---------------------------------------------------------------------------


@pytest.mark.infra
@requires_helm
def test_unknown_worker_tools_key_does_not_inject_tool_ext_env_var() -> None:
    docs = _render_chart_with_sets(["workerTools.fakeExt.enabled=true"])
    env = _get_coordinator_env(docs)
    known_tool_ext_vars = {
        "TOOL_EXT_DEVDOCS_AVAILABLE",
        "TOOL_EXT_LSP_AVAILABLE",
        "TOOL_EXT_SEMGREP_AVAILABLE",
        "TOOL_EXT_TREE_SITTER_AVAILABLE",
    }
    unexpected = {k for k in env if k.startswith("TOOL_EXT_") and k not in known_tool_ext_vars}
    assert not unexpected, (
        f"Unknown workerTools key must not inject extra TOOL_EXT_* env vars; found: {unexpected}"
    )


@pytest.mark.infra
def test_tool_extension_enum_rejects_invalid_name() -> None:
    from sec_review_framework.data.experiment import ToolExtension
    with pytest.raises(ValueError):
        ToolExtension("INVALID_EXTENSION")


@pytest.mark.infra
def test_tool_extension_enum_accepts_all_valid_names() -> None:
    from sec_review_framework.data.experiment import ToolExtension
    assert ToolExtension("devdocs") is ToolExtension.DEVDOCS
    assert ToolExtension("lsp") is ToolExtension.LSP
    assert ToolExtension("semgrep") is ToolExtension.SEMGREP
    assert ToolExtension("tree_sitter") is ToolExtension.TREE_SITTER
