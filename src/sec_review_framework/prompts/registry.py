"""Prompt snapshot registry — persists PromptSnapshot objects to YAML files."""

from pathlib import Path

import yaml

from sec_review_framework.data.experiment import PromptSnapshot, StrategyName


class PromptRegistry:
    """
    Persists PromptSnapshot objects to config/prompts/{strategy}/{snapshot_id}.yaml
    for version tracking across batches.
    """

    def __init__(self, config_root: Path = Path("config")):
        self.prompts_dir = config_root / "prompts"

    def save(self, strategy_name: StrategyName, snapshot: PromptSnapshot) -> Path:
        """Persist snapshot to YAML. Returns the path written."""
        strategy_dir = self.prompts_dir / strategy_name.value
        strategy_dir.mkdir(parents=True, exist_ok=True)
        path = strategy_dir / f"{snapshot.snapshot_id}.yaml"
        data = snapshot.model_dump()
        data["captured_at"] = snapshot.captured_at.isoformat()
        path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
        return path

    def load(self, strategy_name: StrategyName, snapshot_id: str) -> PromptSnapshot:
        """Load a snapshot by ID. Raises FileNotFoundError if not found."""
        path = self.prompts_dir / strategy_name.value / f"{snapshot_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"Snapshot {snapshot_id} not found for strategy {strategy_name.value}"
            )
        data = yaml.safe_load(path.read_text())
        return PromptSnapshot.model_validate(data)

    def list_snapshots(self, strategy_name: StrategyName) -> list[str]:
        """List all snapshot IDs for a strategy, sorted by filename."""
        strategy_dir = self.prompts_dir / strategy_name.value
        if not strategy_dir.exists():
            return []
        return [f.stem for f in sorted(strategy_dir.glob("*.yaml"))]
