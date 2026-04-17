"""Validate Kubernetes manifests rendered from the Helm chart.

These tests render helm/sec-review/ with default values and assert invariants
on the generated resources. They do NOT require a live cluster.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CHART_DIR = REPO_ROOT / "helm" / "sec-review"

requires_helm = pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="helm CLI not installed",
)


def _render_chart(values_file: str | None = None) -> list[dict]:
    """Render the chart and return parsed resources.

    Args:
        values_file: Optional path (relative to CHART_DIR) to override values.
    """
    cmd = [
        "helm", "template", "sec-review", str(CHART_DIR),
        "--namespace", "sec-review",
    ]
    if values_file is not None:
        cmd.extend(["--values", str(CHART_DIR / values_file)])

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


def _by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


@requires_helm
def test_chart_renders_with_default_values() -> None:
    """Default values render valid YAML with a sane set of resources."""
    docs = _render_chart()
    assert docs, "Chart rendered no resources"

    kinds = {d.get("kind") for d in docs}
    expected_core = {"Deployment", "Service", "PersistentVolumeClaim",
                     "Role", "RoleBinding", "ServiceAccount"}
    missing = expected_core - kinds
    assert not missing, f"Missing core resource kinds: {missing}"


@requires_helm
def test_chart_renders_with_minikube_values() -> None:
    """Minikube profile: feature-gated resources are absent."""
    docs = _render_chart(values_file="values-minikube.yaml")
    kinds = {d.get("kind") for d in docs}

    # Must NOT render the cluster-addon-dependent resources
    assert "NetworkPolicy" not in kinds
    assert "ServiceMonitor" not in kinds
    assert "ConstraintTemplate" not in kinds


@requires_helm
def test_chart_renders_with_prod_values() -> None:
    """Prod profile: all opt-in resources render."""
    docs = _render_chart(values_file="values-prod.yaml")
    kinds = {d.get("kind") for d in docs}

    assert "NetworkPolicy" in kinds
    assert "ServiceMonitor" in kinds
    assert "ConstraintTemplate" in kinds


@requires_helm
def test_network_policy_egress_only() -> None:
    """worker-egress NetworkPolicy has policyTypes=['Egress']."""
    docs = _render_chart(values_file="values-prod.yaml")
    worker_policies = [
        d for d in _by_kind(docs, "NetworkPolicy")
        if "worker-egress" in d["metadata"]["name"]
    ]
    assert worker_policies, "worker-egress NetworkPolicy not rendered"
    assert worker_policies[0]["spec"]["policyTypes"] == ["Egress"]


@requires_helm
def test_rbac_role_grants_job_management() -> None:
    """Role grants create/delete on batch/jobs."""
    docs = _render_chart()
    roles = _by_kind(docs, "Role")
    assert roles, "No Role rendered"

    batch_rules = [
        r for role in roles for r in role.get("rules", [])
        if "batch" in r.get("apiGroups", []) and "jobs" in r.get("resources", [])
    ]
    assert batch_rules, "No rule targeting batch/jobs found in any Role"


@requires_helm
def test_shared_pvc_access_mode_defaults_to_rwx() -> None:
    """Default values request ReadWriteMany for the shared PVC."""
    docs = _render_chart()
    pvcs = _by_kind(docs, "PersistentVolumeClaim")
    assert pvcs, "No PVC rendered"
    assert "ReadWriteMany" in pvcs[0]["spec"]["accessModes"]


@requires_helm
def test_minikube_pvc_access_mode_is_rwo() -> None:
    """Minikube overrides collapse accessMode to ReadWriteOnce."""
    docs = _render_chart(values_file="values-minikube.yaml")
    pvcs = _by_kind(docs, "PersistentVolumeClaim")
    assert pvcs, "No PVC rendered"
    assert pvcs[0]["spec"]["accessModes"] == ["ReadWriteOnce"]


@requires_helm
def test_coordinator_worker_image_env_matches_values() -> None:
    """WORKER_IMAGE env var on the coordinator reflects image.worker values."""
    docs = _render_chart(values_file="values-prod.yaml")
    deployments = _by_kind(docs, "Deployment")
    assert deployments, "No Deployment rendered"

    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["WORKER_IMAGE"] == "ghcr.io/myorg/sec-review/worker:v1.0.0", (
        f"Unexpected WORKER_IMAGE: {env.get('WORKER_IMAGE')!r}"
    )


@requires_helm
def test_minikube_image_pull_policy_is_never() -> None:
    """Minikube must not pull images (they're built locally)."""
    docs = _render_chart(values_file="values-minikube.yaml")
    container = _by_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["imagePullPolicy"] == "Never"
