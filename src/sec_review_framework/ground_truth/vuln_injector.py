"""Vulnerability injection into clean repos for synthetic ground truth datasets."""

from __future__ import annotations

import difflib
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import BaseModel

from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.findings import Severity, VulnClass

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class InjectionTemplate(BaseModel):
    """A parameterized vulnerability injection template loaded from YAML."""

    id: str
    vuln_class: VulnClass
    cwe_id: str
    language: str
    description: str
    severity: Severity
    patch_template: str  # template string with {{PLACEHOLDER}} tokens
    anchor_pattern: str  # regex that must match a line in the target
    anchor_mode: str  # "after" | "before" | "replace"


class InjectionResult(BaseModel):
    """Result of a successful injection."""

    template_id: str
    target_file: str
    anchor_line: int  # 1-based
    lines_added: int
    label: GroundTruthLabel


class InjectionPreview(BaseModel):
    """Dry-run result — shows what would happen without modifying files."""

    template_id: str
    target_file: str
    anchor_line: int
    unified_diff: str
    before_snippet: str
    after_snippet: str
    label_preview: dict


# ---------------------------------------------------------------------------
# VulnInjector
# ---------------------------------------------------------------------------


class VulnInjector:
    """Loads injection templates and applies them to source files."""

    def __init__(self, templates_root: Path) -> None:
        self.templates_root = Path(templates_root)
        self.templates: dict[str, InjectionTemplate] = self._load_templates()

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _load_templates(self) -> dict[str, InjectionTemplate]:
        """Walk templates_root for *.yaml files and load each as an InjectionTemplate."""
        templates: dict[str, InjectionTemplate] = {}
        if not self.templates_root.exists():
            return templates
        for yaml_path in self.templates_root.rglob("*.yaml"):
            try:
                data = yaml.safe_load(yaml_path.read_text())
                if not isinstance(data, dict):
                    continue
                tmpl = InjectionTemplate(**data)
                templates[tmpl.id] = tmpl
            except Exception:
                # Skip malformed template files
                continue
        return templates

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preview(
        self,
        repo_path: Path,
        template_id: str,
        target_file: str,
        substitutions: dict[str, str] | None = None,
    ) -> InjectionPreview:
        """
        Dry-run: compute the injection without modifying files.
        Returns the unified diff, before/after snippets, and label preview.
        """
        template = self.templates[template_id]
        file_path = Path(repo_path) / target_file
        original_content = file_path.read_text()
        anchor_line = self._find_anchor(original_content, template.anchor_pattern)
        patched_content = self._apply_patch(
            original_content, template, anchor_line, substitutions
        )
        diff = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            patched_content.splitlines(keepends=True),
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
        )
        return InjectionPreview(
            template_id=template_id,
            target_file=target_file,
            anchor_line=anchor_line,
            unified_diff="".join(diff),
            before_snippet=self._extract_snippet(original_content, anchor_line, context=4),
            after_snippet=self._extract_snippet(patched_content, anchor_line, context=6),
            label_preview=self._preview_label(template, target_file, anchor_line, patched_content),
        )

    def inject(
        self,
        repo_path: Path,
        template_id: str,
        target_file: str,
        substitutions: dict[str, str] | None = None,
    ) -> InjectionResult:
        """Apply a template to a specific file in the repo. Modifies the file on disk."""
        template = self.templates[template_id]
        file_path = Path(repo_path) / target_file
        original_content = file_path.read_text()
        anchor_line = self._find_anchor(original_content, template.anchor_pattern)
        patched_content = self._apply_patch(
            original_content, template, anchor_line, substitutions
        )
        file_path.write_text(patched_content)

        lines_added = len(patched_content.splitlines()) - len(original_content.splitlines())

        # Build a preliminary result (label filled in next)
        result = InjectionResult(
            template_id=template_id,
            target_file=target_file,
            anchor_line=anchor_line,
            lines_added=lines_added,
            label=self._make_placeholder_label(template, target_file, anchor_line, lines_added),
        )
        # Replace placeholder label with the real one
        real_label = self.build_label(result, template, "v1")
        return InjectionResult(
            template_id=result.template_id,
            target_file=result.target_file,
            anchor_line=result.anchor_line,
            lines_added=result.lines_added,
            label=real_label,
        )

    def build_label(
        self,
        result: InjectionResult,
        template: InjectionTemplate,
        dataset_version: str,
    ) -> GroundTruthLabel:
        """Build a GroundTruthLabel from an injection result."""
        return GroundTruthLabel(
            id=str(uuid4()),
            dataset_version=dataset_version,
            file_path=result.target_file,
            line_start=result.anchor_line,
            line_end=result.anchor_line + max(0, result.lines_added),
            cwe_id=template.cwe_id,
            vuln_class=template.vuln_class,
            severity=template.severity,
            description=template.description,
            source=GroundTruthSource.INJECTED,
            source_ref=template.id,
            confidence="confirmed",
            created_at=datetime.utcnow(),
            introduced_in_diff=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_anchor(self, content: str, pattern: str) -> int:
        """Return 1-based line number of the first line matching pattern, or 1."""
        for i, line in enumerate(content.splitlines(), start=1):
            if re.search(pattern, line):
                return i
        return 1

    def _apply_patch(
        self,
        content: str,
        template: InjectionTemplate,
        anchor_line: int,
        substitutions: dict[str, str] | None,
    ) -> str:
        """Insert/replace lines in content according to the template."""
        patch = template.patch_template
        if substitutions:
            for key, value in substitutions.items():
                patch = patch.replace(f"{{{{{key}}}}}", value)

        lines = content.splitlines(keepends=True)
        patch_lines = patch.splitlines(keepends=True)

        idx = anchor_line - 1  # convert to 0-based index
        idx = max(0, min(idx, len(lines) - 1))

        if template.anchor_mode == "after":
            result_lines = lines[: idx + 1] + patch_lines + lines[idx + 1 :]
        elif template.anchor_mode == "before":
            result_lines = lines[:idx] + patch_lines + lines[idx:]
        elif template.anchor_mode == "replace":
            result_lines = lines[:idx] + patch_lines + lines[idx + 1 :]
        else:
            # Default: insert after
            result_lines = lines[: idx + 1] + patch_lines + lines[idx + 1 :]

        return "".join(result_lines)

    def _extract_snippet(self, content: str, anchor_line: int, context: int) -> str:
        """Return a snippet of lines around anchor_line (1-based)."""
        lines = content.splitlines(keepends=True)
        start = max(0, anchor_line - 1 - context)
        end = min(len(lines), anchor_line + context)
        return "".join(lines[start:end])

    def _preview_label(
        self,
        template: InjectionTemplate,
        target_file: str,
        anchor_line: int,
        content: str,
    ) -> dict:
        """Return a dict preview of the label that would be created."""
        return {
            "vuln_class": template.vuln_class.value,
            "cwe_id": template.cwe_id,
            "severity": template.severity.value,
            "description": template.description,
            "file_path": target_file,
            "anchor_line": anchor_line,
        }

    def _make_placeholder_label(
        self,
        template: InjectionTemplate,
        target_file: str,
        anchor_line: int,
        lines_added: int,
    ) -> GroundTruthLabel:
        """Temporary label used to satisfy InjectionResult's required field."""
        return GroundTruthLabel(
            id=str(uuid4()),
            dataset_version="v1",
            file_path=target_file,
            line_start=anchor_line,
            line_end=anchor_line + max(0, lines_added),
            cwe_id=template.cwe_id,
            vuln_class=template.vuln_class,
            severity=template.severity,
            description=template.description,
            source=GroundTruthSource.INJECTED,
            source_ref=template.id,
            confidence="confirmed",
            created_at=datetime.utcnow(),
        )
