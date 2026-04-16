"""Ground truth data models: TargetCodebase, DiffSpec, LabelStore."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel

from sec_review_framework.data.evaluation import GroundTruthLabel

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".md": "markdown",
}

_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".exe", ".bin", ".so",
        ".dylib", ".dll", ".pyc", ".class", ".o", ".a",
    }
)

_SKIP_DIRS: frozenset[str] = frozenset({".git", "node_modules", "__pycache__"})


def _detect_language(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    return _EXT_TO_LANGUAGE.get(ext)


# ---------------------------------------------------------------------------
# DiffSpec
# ---------------------------------------------------------------------------


class DiffSpec(BaseModel):
    base_ref: str
    head_ref: str
    changed_files: list[str] | None = None


# ---------------------------------------------------------------------------
# TargetCodebase
# ---------------------------------------------------------------------------


class TargetCodebase:
    """Wraps a checked-out repository on disk."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = Path(repo_path)

    # ------------------------------------------------------------------
    # Source file listing
    # ------------------------------------------------------------------

    def list_source_files(self) -> list[str]:
        """Walk the repo and return relative paths, skipping non-source items."""
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.repo_path):
            # Prune skip dirs in-place so os.walk doesn't descend into them
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in _BINARY_EXTENSIONS:
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self.repo_path)
                results.append(rel_path)
        return sorted(results)

    # ------------------------------------------------------------------
    # File reading
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        """Read a file relative to repo_path."""
        return (self.repo_path / path).read_text(errors="replace")

    def read_file_excerpt(self, path: str, line_start: int, context_lines: int = 10) -> str:
        """Return lines [line_start, line_start+context_lines) (1-indexed, inclusive start)."""
        lines = (self.repo_path / path).read_text(errors="replace").splitlines(keepends=True)
        start = max(0, line_start - 1)
        end = start + context_lines
        return "".join(lines[start:end])

    # ------------------------------------------------------------------
    # File tree
    # ------------------------------------------------------------------

    def get_file_tree(self) -> dict:
        """Return a nested dict tree: {name, type, children?, size?, language?}."""
        return self._build_tree(self.repo_path)

    def _build_tree(self, path: Path) -> dict:
        name = path.name or str(path)
        if path.is_file():
            return {
                "name": name,
                "type": "file",
                "size": path.stat().st_size,
                "language": _detect_language(path.name),
            }
        children = []
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            entries = []
        for entry in entries:
            if entry.name in _SKIP_DIRS:
                continue
            if entry.is_file():
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in _BINARY_EXTENSIONS:
                    continue
            children.append(self._build_tree(entry))
        return {"name": name, "type": "dir", "children": children}

    # ------------------------------------------------------------------
    # File content
    # ------------------------------------------------------------------

    def get_file_content(self, path: str) -> dict:
        """Return {path, content, language, line_count, size_bytes}."""
        full_path = self.repo_path / path
        content = full_path.read_text(errors="replace")
        return {
            "path": path,
            "content": content,
            "language": _detect_language(path),
            "line_count": len(content.splitlines()),
            "size_bytes": full_path.stat().st_size,
        }

    # ------------------------------------------------------------------
    # Diff spec
    # ------------------------------------------------------------------

    def load_diff_spec(self) -> DiffSpec:
        """Load diff_spec.yaml from the dataset directory (parent of repo_path)."""
        spec_path = self.repo_path.parent / "diff_spec.yaml"
        data = yaml.safe_load(spec_path.read_text())
        return DiffSpec(**data)

    # ------------------------------------------------------------------
    # Git diff
    # ------------------------------------------------------------------

    def get_diff(self, base_ref: str, head_ref: str) -> str:
        """Return the unified diff between two git refs."""
        result = subprocess.run(
            ["git", "diff", base_ref, head_ref],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def get_changed_files(self, base_ref: str, head_ref: str) -> list[str]:
        """Return list of file paths changed between two git refs."""
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref, head_ref],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# LabelStore
# ---------------------------------------------------------------------------


class LabelStore:
    """Reads and writes ground truth labels from JSONL files."""

    def __init__(self, datasets_root: Path) -> None:
        self.datasets_root = Path(datasets_root)

    def load(self, dataset_name: str, version: str | None = None) -> list[GroundTruthLabel]:
        path = self.datasets_root / "targets" / dataset_name / "labels.jsonl"
        labels = [
            GroundTruthLabel.model_validate_json(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        if version:
            labels = [label for label in labels if label.dataset_version == version]
        return labels

    def append(self, dataset_name: str, labels: list[GroundTruthLabel]) -> None:
        path = self.datasets_root / "targets" / dataset_name / "labels.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for label in labels:
                f.write(label.model_dump_json() + "\n")
