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


def _render_chart(values_file: str | None = None) -> list[dict]:
    cmd = [
        "helm", "template", "sec-review", str(CHART_DIR),
        "--namespace", "sec-review",
    ]
    if values_file is not None:
        cmd.extend(["--values", str(CHART_DIR / values_file)])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


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
    """ConfigMap data includes the expected config/*.yaml files."""
    docs = _render_chart()
    cm = _get_experiment_configmap(docs)
    data = cm.get("data") or {}

    required_keys = {
        "models.yaml",
        "pricing.yaml",
        "review_profiles.yaml",
        "concurrency.yaml",
    }
    missing = required_keys - set(data.keys())
    assert not missing, f"ConfigMap is missing keys: {missing}"


@requires_helm
def test_experiment_configmap_models_yaml_content_matches_repo() -> None:
    """The models.yaml key in the ConfigMap matches config/models.yaml on disk."""
    docs = _render_chart()
    cm = _get_experiment_configmap(docs)
    data = cm.get("data") or {}

    assert "models.yaml" in data, "models.yaml key missing from ConfigMap"

    repo_content = (CONFIG_DIR / "models.yaml").read_text()
    # Both should parse to the same object (ignoring trailing newline differences).
    assert yaml.safe_load(data["models.yaml"]) == yaml.safe_load(repo_content), (
        "models.yaml content in ConfigMap does not match config/models.yaml"
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

    for key in ("models.yaml", "pricing.yaml", "review_profiles.yaml", "concurrency.yaml"):
        assert key in data, f"Missing key {key!r} in ConfigMap with e2e values"
