"""Validate Kubernetes manifests in k8s/ without a live cluster."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Resolve k8s/ relative to the repo root (two levels up from this file)
K8S_DIR = Path(__file__).parent.parent.parent / "k8s"


def _load_all_docs(path: Path) -> list[dict]:
    """Parse a YAML file that may contain multiple documents (---separators)."""
    with path.open() as fh:
        docs = list(yaml.safe_load_all(fh))
    return [d for d in docs if d is not None]


def test_all_manifests_valid_yaml() -> None:
    """Every *.yaml file in k8s/ must parse without exceptions."""
    yaml_files = list(K8S_DIR.glob("*.yaml"))
    assert yaml_files, f"No YAML files found in {K8S_DIR}"

    errors: list[str] = []
    for yaml_file in yaml_files:
        try:
            _load_all_docs(yaml_file)
        except yaml.YAMLError as exc:
            errors.append(f"{yaml_file.name}: {exc}")

    assert not errors, "YAML parse errors:\n" + "\n".join(errors)


def test_namespace_defined() -> None:
    """namespace.yaml must declare kind=Namespace with name=sec-review."""
    namespace_file = K8S_DIR / "namespace.yaml"
    assert namespace_file.exists(), f"{namespace_file} not found"

    docs = _load_all_docs(namespace_file)
    assert docs, "namespace.yaml is empty"

    doc = docs[0]
    assert doc.get("kind") == "Namespace", (
        f"Expected kind=Namespace, got {doc.get('kind')!r}"
    )
    assert doc.get("metadata", {}).get("name") == "sec-review", (
        f"Expected metadata.name=sec-review, got {doc.get('metadata', {}).get('name')!r}"
    )


def test_network_policy_egress_only() -> None:
    """network-policy.yaml spec.policyTypes must be exactly ['Egress']."""
    policy_file = K8S_DIR / "network-policy.yaml"
    assert policy_file.exists(), f"{policy_file} not found"

    docs = _load_all_docs(policy_file)
    assert docs, "network-policy.yaml is empty"

    doc = docs[0]
    policy_types = doc.get("spec", {}).get("policyTypes", [])
    assert policy_types == ["Egress"], (
        f"Expected policyTypes=['Egress'], got {policy_types!r}"
    )


def test_rbac_roles_defined() -> None:
    """rbac.yaml must contain a Role with batch/jobs rules."""
    rbac_file = K8S_DIR / "rbac.yaml"
    assert rbac_file.exists(), f"{rbac_file} not found"

    docs = _load_all_docs(rbac_file)
    role_docs = [d for d in docs if d.get("kind") == "Role"]
    assert role_docs, "No Role document found in rbac.yaml"

    role = role_docs[0]
    rules = role.get("rules", [])
    assert rules, "Role has no rules defined"

    # At least one rule must target the batch apiGroup for jobs
    batch_rules = [
        r for r in rules
        if "batch" in r.get("apiGroups", []) and "jobs" in r.get("resources", [])
    ]
    assert batch_rules, (
        "Expected a rule with apiGroups=['batch'] and resources=['jobs'], "
        f"found rules: {rules}"
    )


def test_shared_pvc_access_mode() -> None:
    """shared-pvc.yaml must have ReadWriteMany as an access mode."""
    pvc_file = K8S_DIR / "shared-pvc.yaml"
    assert pvc_file.exists(), f"{pvc_file} not found"

    docs = _load_all_docs(pvc_file)
    assert docs, "shared-pvc.yaml is empty"

    doc = docs[0]
    access_modes = doc.get("spec", {}).get("accessModes", [])
    assert "ReadWriteMany" in access_modes, (
        f"Expected ReadWriteMany in accessModes, got {access_modes!r}"
    )
