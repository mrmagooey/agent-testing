"""Tests for MarkdownReportGenerator and JSONReportGenerator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sec_review_framework.data.experiment import RunStatus, ToolExtension
from sec_review_framework.reporting.json_report import JSONReportGenerator
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# MarkdownReportGenerator
# ---------------------------------------------------------------------------


class TestMarkdownReportGenerator:
    def test_render_run_creates_report_md(self, sample_run_result, tmp_path: Path):
        gen = MarkdownReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        assert (tmp_path / "report.md").exists()

    def test_render_run_contains_precision_recall(self, sample_run_result, tmp_path: Path):
        # Attach a minimal evaluation so the Metrics Summary is rendered.
        from sec_review_framework.data.evaluation import EvaluationResult, MatchedFinding

        ev = EvaluationResult(
            experiment_id="test",
            dataset_version="1.0.0",
            total_labels=3,
            total_findings=2,
            true_positives=1,
            false_positives=1,
            false_negatives=2,
            unlabeled_real_count=0,
            precision=0.5,
            recall=0.333,
            f1=0.4,
            false_positive_rate=0.25,
            matched_findings=[],
            unmatched_labels=[],
            evidence_quality_counts={},
        )
        sample_run_result.evaluation = ev

        gen = MarkdownReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        content = (tmp_path / "report.md").read_text()
        assert "Precision" in content
        assert "Recall" in content

    def test_render_run_failed_status_shows_error(self, sample_run_result, tmp_path: Path):
        sample_run_result.status = RunStatus.FAILED
        sample_run_result.error = "Model API timed out."

        gen = MarkdownReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        content = (tmp_path / "report.md").read_text()
        assert "Model API timed out" in content

    def test_render_matrix_creates_matrix_report_md(self, sample_run_result, tmp_path: Path):
        gen = MarkdownReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        assert (tmp_path / "matrix_report.md").exists()

    def test_render_matrix_empty_results_no_crash(self, tmp_path: Path):
        gen = MarkdownReportGenerator()
        gen.render_matrix([], tmp_path)
        content = (tmp_path / "matrix_report.md").read_text()
        assert "No Results" in content


# ---------------------------------------------------------------------------
# JSONReportGenerator
# ---------------------------------------------------------------------------


class TestJSONReportGenerator:
    def test_render_run_creates_report_json(self, sample_run_result, tmp_path: Path):
        gen = JSONReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        assert (tmp_path / "report.json").exists()

    def test_render_run_is_valid_json(self, sample_run_result, tmp_path: Path):
        gen = JSONReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        text = (tmp_path / "report.json").read_text()
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_render_run_has_expected_schema_keys(self, sample_run_result, tmp_path: Path):
        gen = JSONReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        data = json.loads((tmp_path / "report.json").read_text())
        for key in ("run_id", "experiment_id", "model_id", "strategy",
                    "status", "dedup", "token_usage", "findings"):
            assert key in data, f"Missing key: {key}"

    def test_render_matrix_creates_matrix_report_json(self, sample_run_result, tmp_path: Path):
        gen = JSONReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        assert (tmp_path / "matrix_report.json").exists()

    def test_render_matrix_has_expected_schema_keys(self, sample_run_result, tmp_path: Path):
        gen = JSONReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        data = json.loads((tmp_path / "matrix_report.json").read_text())
        for key in ("experiment_id", "dataset", "runs", "dimension_analysis", "cost_analysis"):
            assert key in data, f"Missing key: {key}"

    def test_render_matrix_empty_results_no_crash(self, tmp_path: Path):
        gen = JSONReportGenerator()
        gen.render_matrix([], tmp_path)
        data = json.loads((tmp_path / "matrix_report.json").read_text())
        assert data["runs"] == []

    def test_serialize_run_includes_tool_extensions(self, sample_run_result, tmp_path: Path):
        """tool_extensions must be present in the serialized run as a sorted list."""
        sample_run_result.experiment.tool_extensions = frozenset(
            {ToolExtension.LSP, ToolExtension.DEVDOCS}
        )
        gen = JSONReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        data = json.loads((tmp_path / "report.json").read_text())
        assert "tool_extensions" in data
        assert data["tool_extensions"] == ["devdocs", "lsp"]

    def test_serialize_run_empty_extensions_is_empty_list(self, sample_run_result, tmp_path: Path):
        """A run with no extensions should serialize tool_extensions as []."""
        sample_run_result.experiment.tool_extensions = frozenset()
        gen = JSONReportGenerator()
        gen.render_run(sample_run_result, tmp_path)
        data = json.loads((tmp_path / "report.json").read_text())
        assert data["tool_extensions"] == []

    def test_matrix_dimension_analysis_has_tool_extension_impact(self, sample_run_result, tmp_path: Path):
        """dimension_analysis must include tool_extension_impact key."""
        gen = JSONReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        data = json.loads((tmp_path / "matrix_report.json").read_text())
        assert "tool_extension_impact" in data["dimension_analysis"]


# ---------------------------------------------------------------------------
# MarkdownReportGenerator — extension columns
# ---------------------------------------------------------------------------


class TestMarkdownExtensionColumns:
    def test_no_extension_columns_when_no_runs_use_extensions(self, sample_run_result, tmp_path):
        """If no run has any extension, the matrix header must not contain ext- columns."""
        sample_run_result.experiment.tool_extensions = frozenset()
        gen = MarkdownReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        content = (tmp_path / "matrix_report.md").read_text()
        assert "ext-lsp" not in content
        assert "ext-tree_sitter" not in content
        assert "ext-devdocs" not in content

    def test_extension_column_appears_when_run_uses_it(self, sample_run_result, tmp_path):
        """If a run has LSP extension, ext-lsp column must appear in matrix header."""
        sample_run_result.experiment.tool_extensions = frozenset({ToolExtension.LSP})
        gen = MarkdownReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        content = (tmp_path / "matrix_report.md").read_text()
        assert "ext-lsp" in content
        assert "ext-tree_sitter" not in content

    def test_tool_extension_impact_section_present(self, sample_run_result, tmp_path):
        """Matrix report must include the Tool Extension Impact section."""
        gen = MarkdownReportGenerator()
        gen.render_matrix([sample_run_result], tmp_path)
        content = (tmp_path / "matrix_report.md").read_text()
        assert "Tool Extension Impact" in content
