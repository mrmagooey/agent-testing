"""Validate Dockerfile syntax without performing any Docker builds."""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root — two levels up from this file (tests/infra/)
REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.mark.infra
def test_worker_dockerfile_exists() -> None:
    """Dockerfile.worker must exist and contain content."""
    dockerfile = REPO_ROOT / "Dockerfile.worker"
    assert dockerfile.exists(), f"{dockerfile} does not exist"
    assert dockerfile.stat().st_size > 0, "Dockerfile.worker is empty"


@pytest.mark.infra
def test_coordinator_dockerfile_exists() -> None:
    """Dockerfile.coordinator must exist and contain content."""
    dockerfile = REPO_ROOT / "Dockerfile.coordinator"
    assert dockerfile.exists(), f"{dockerfile} does not exist"
    assert dockerfile.stat().st_size > 0, "Dockerfile.coordinator is empty"


@pytest.mark.infra
def test_coordinator_dockerfile_has_multistage() -> None:
    """Dockerfile.coordinator must use a multi-stage build (at least two FROM lines)."""
    dockerfile = REPO_ROOT / "Dockerfile.coordinator"
    assert dockerfile.exists(), f"{dockerfile} does not exist"

    content = dockerfile.read_text()
    from_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip().upper().startswith("FROM")
    ]
    assert len(from_lines) >= 2, (
        f"Expected at least 2 FROM instructions for a multi-stage build, "
        f"found {len(from_lines)}: {from_lines}"
    )
