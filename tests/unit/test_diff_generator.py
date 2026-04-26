"""Unit tests for DiffDatasetGenerator.generate()."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.ground_truth.diff_generator import DiffDatasetGenerator
from sec_review_framework.ground_truth.vuln_injector import (
    InjectionResult,
    InjectionTemplate,
    VulnInjector,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

BASE_SHA = "aabbccdd" * 5
HEAD_SHA = "11223344" * 5


def _make_template(tmpl_id: str = "sqli_test") -> InjectionTemplate:
    return InjectionTemplate(
        id=tmpl_id,
        vuln_class=VulnClass.SQLI,
        cwe_id="CWE-89",
        language="python",
        description="SQL injection via format string",
        severity=Severity.HIGH,
        patch_template='    query = "SELECT * FROM users WHERE id = %s" % uid\n',
        anchor_pattern=r"def \w+\(",
        anchor_mode="after",
    )


def _make_label(tmpl_id: str = "sqli_test", dataset_version: str = "v1") -> GroundTruthLabel:
    from datetime import datetime, timezone

    return GroundTruthLabel(
        id="label-abc",
        dataset_version=dataset_version,
        file_path="app/views.py",
        line_start=2,
        line_end=3,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection via format string",
        source=GroundTruthSource.INJECTED,
        source_ref=tmpl_id,
        confidence="confirmed",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        introduced_in_diff=None,
    )


def _make_injection_result(tmpl_id: str = "sqli_test") -> InjectionResult:
    return InjectionResult(
        template_id=tmpl_id,
        target_file="app/views.py",
        anchor_line=2,
        lines_added=1,
        label=_make_label(tmpl_id),
    )


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    """Minimal source tree to act as clean_repo_path."""
    repo = tmp_path / "clean_repo"
    repo.mkdir()
    (repo / "app").mkdir()
    (repo / "app" / "views.py").write_text("def index():\n    pass\n")
    return repo


@pytest.fixture
def datasets_root(tmp_path: Path) -> Path:
    root = tmp_path / "datasets"
    root.mkdir()
    return root


def _make_generator(datasets_root: Path, injector: VulnInjector) -> DiffDatasetGenerator:
    return DiffDatasetGenerator(injector=injector, datasets_root=datasets_root)


def _subprocess_side_effect(*args, **kwargs):
    """Return a successful CompletedProcess for any subprocess.run call."""
    return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=b"", stderr=b"")


def _check_output_side_effect(cmd, **kwargs):
    """Return alternating BASE_SHA / HEAD_SHA for rev-parse calls."""
    if "HEAD" in cmd:
        return f"{BASE_SHA}\n"
    return f"{HEAD_SHA}\n"


# We need the two rev-parse calls to return BASE then HEAD in order.
class _CheckOutputSequence:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self, *args, **kwargs):
        return next(self._values)


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------


def test_generate_happy_path(clean_repo, datasets_root):
    """generate() copies repo, runs git, calls injector, writes diff_spec.yaml, returns labels."""
    template = _make_template()
    injection_result = _make_injection_result()

    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {"sqli_test": template}
    mock_injector.inject.return_value = injection_result
    mock_injector.build_label.return_value = _make_label()

    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree") as mock_copy,
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        labels = gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="my-dataset",
            template_ids=["sqli_test"],
            target_files={"sqli_test": "app/views.py"},
            dataset_version="v1",
        )

    assert len(labels) == 1
    assert labels[0].introduced_in_diff is True
    assert labels[0].vuln_class == VulnClass.SQLI

    spec_path = datasets_root / "targets" / "my-dataset" / "diff_spec.yaml"
    assert spec_path.exists()
    spec = yaml.safe_load(spec_path.read_text())
    assert spec["base_ref"] == BASE_SHA
    assert spec["head_ref"] == HEAD_SHA

    mock_copy.assert_called_once()
    mock_injector.inject.assert_called_once_with(
        datasets_root / "targets" / "my-dataset" / "repo",
        "sqli_test",
        "app/views.py",
    )


# ---------------------------------------------------------------------------
# Label contract: introduced_in_diff must be set to True
# ---------------------------------------------------------------------------


def test_generate_sets_introduced_in_diff(clean_repo, datasets_root):
    """Every returned label must have introduced_in_diff == True."""
    templates = {f"tmpl_{i}": _make_template(f"tmpl_{i}") for i in range(3)}
    target_files = {tid: "app/views.py" for tid in templates}

    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = templates
    mock_injector.inject.side_effect = lambda repo, tid, f: _make_injection_result(tid)
    mock_injector.build_label.side_effect = lambda res, tmpl, ver: _make_label(res.template_id)

    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        labels = gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="multi-dataset",
            template_ids=list(templates.keys()),
            target_files=target_files,
            dataset_version="v1",
        )

    assert all(lbl.introduced_in_diff is True for lbl in labels)
    assert len(labels) == 3


# ---------------------------------------------------------------------------
# Multi-file diff (multiple template_ids → multiple injections)
# ---------------------------------------------------------------------------


def test_generate_multi_file_diff(clean_repo, datasets_root):
    """generate() applies each template in order and returns one label per template."""
    sqli_template = _make_template("sqli_tmpl")
    xss_template = InjectionTemplate(
        id="xss_tmpl",
        vuln_class=VulnClass.XSS,
        cwe_id="CWE-79",
        language="python",
        description="Reflected XSS",
        severity=Severity.MEDIUM,
        patch_template='    return f"<h1>{name}</h1>"\n',
        anchor_pattern=r"def render",
        anchor_mode="after",
    )

    def _build_label(result, template, ver):
        from datetime import datetime, timezone

        return GroundTruthLabel(
            id=f"label-{result.template_id}",
            dataset_version=ver,
            file_path=result.target_file,
            line_start=result.anchor_line,
            line_end=result.anchor_line + result.lines_added,
            cwe_id=template.cwe_id,
            vuln_class=template.vuln_class,
            severity=template.severity,
            description=template.description,
            source=GroundTruthSource.INJECTED,
            source_ref=template.id,
            confidence="confirmed",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {"sqli_tmpl": sqli_template, "xss_tmpl": xss_template}
    mock_injector.inject.side_effect = lambda repo, tid, f: InjectionResult(
        template_id=tid,
        target_file=f,
        anchor_line=1,
        lines_added=1,
        label=_make_label(tid),
    )
    mock_injector.build_label.side_effect = _build_label

    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        labels = gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="multi-file",
            template_ids=["sqli_tmpl", "xss_tmpl"],
            target_files={"sqli_tmpl": "app/views.py", "xss_tmpl": "app/templates.py"},
            dataset_version="v2",
        )

    assert len(labels) == 2
    vuln_classes = {lbl.vuln_class for lbl in labels}
    assert VulnClass.SQLI in vuln_classes
    assert VulnClass.XSS in vuln_classes
    mock_injector.inject.assert_any_call(
        datasets_root / "targets" / "multi-file" / "repo", "sqli_tmpl", "app/views.py"
    )
    mock_injector.inject.assert_any_call(
        datasets_root / "targets" / "multi-file" / "repo", "xss_tmpl", "app/templates.py"
    )


# ---------------------------------------------------------------------------
# Empty template_ids (edge case: zero injections)
# ---------------------------------------------------------------------------


def test_generate_empty_template_ids(clean_repo, datasets_root):
    """generate() with no template_ids returns an empty label list and still writes diff_spec."""
    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {}

    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        labels = gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="empty-dataset",
            template_ids=[],
            target_files={},
            dataset_version="v1",
        )

    assert labels == []
    spec_path = datasets_root / "targets" / "empty-dataset" / "diff_spec.yaml"
    assert spec_path.exists()
    mock_injector.inject.assert_not_called()


# ---------------------------------------------------------------------------
# Failure: clone/copytree failure
# ---------------------------------------------------------------------------


def test_generate_copytree_failure(clean_repo, datasets_root):
    """If shutil.copytree raises, generate() propagates the exception."""
    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {}

    with (
        patch(
            "sec_review_framework.ground_truth.diff_generator.shutil.copytree",
            side_effect=OSError("disk full"),
        ),
        patch("sec_review_framework.ground_truth.diff_generator.subprocess.run"),
        patch("sec_review_framework.ground_truth.diff_generator.subprocess.check_output"),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        with pytest.raises(OSError, match="disk full"):
            gen.generate(
                clean_repo_path=clean_repo,
                dataset_name="fail-dataset",
                template_ids=[],
                target_files={},
                dataset_version="v1",
            )


# ---------------------------------------------------------------------------
# Failure: git init / first commit fails (subprocess.run raises)
# ---------------------------------------------------------------------------


def test_generate_git_init_failure(clean_repo, datasets_root):
    """CalledProcessError from git init propagates out of generate()."""
    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {}

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=subprocess.CalledProcessError(128, ["git", "init"]),
        ),
        patch("sec_review_framework.ground_truth.diff_generator.subprocess.check_output"),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        with pytest.raises(subprocess.CalledProcessError):
            gen.generate(
                clean_repo_path=clean_repo,
                dataset_name="fail-init",
                template_ids=[],
                target_files={},
                dataset_version="v1",
            )


# ---------------------------------------------------------------------------
# Failure: second git commit (post-injection) fails
# ---------------------------------------------------------------------------


def test_generate_post_injection_commit_failure(clean_repo, datasets_root):
    """CalledProcessError on the post-injection commit propagates out of generate()."""
    template = _make_template()
    injection_result = _make_injection_result()

    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {"sqli_test": template}
    mock_injector.inject.return_value = injection_result
    mock_injector.build_label.return_value = _make_label()

    call_count = [0]

    def _run_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] >= 4:
            raise subprocess.CalledProcessError(1, args[0])
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=b"", stderr=b"")

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_run_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            return_value=f"{BASE_SHA}\n",
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        with pytest.raises(subprocess.CalledProcessError):
            gen.generate(
                clean_repo_path=clean_repo,
                dataset_name="fail-commit2",
                template_ids=["sqli_test"],
                target_files={"sqli_test": "app/views.py"},
                dataset_version="v1",
            )


# ---------------------------------------------------------------------------
# Failure: injection itself fails
# ---------------------------------------------------------------------------


def test_generate_injection_failure(clean_repo, datasets_root):
    """If injector.inject() raises, generate() propagates the exception."""
    template = _make_template()

    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {"sqli_test": template}
    mock_injector.inject.side_effect = KeyError("anchor not found")

    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        with pytest.raises(KeyError, match="anchor not found"):
            gen.generate(
                clean_repo_path=clean_repo,
                dataset_name="fail-inject",
                template_ids=["sqli_test"],
                target_files={"sqli_test": "app/views.py"},
                dataset_version="v1",
            )


# ---------------------------------------------------------------------------
# Failure: diff_spec.yaml write failure
# ---------------------------------------------------------------------------


def test_generate_spec_write_failure(clean_repo, datasets_root):
    """If writing diff_spec.yaml raises, generate() propagates the exception."""
    template = _make_template()
    injection_result = _make_injection_result()

    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {"sqli_test": template}
    mock_injector.inject.return_value = injection_result
    mock_injector.build_label.return_value = _make_label()

    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
        patch(
            "pathlib.Path.write_text",
            side_effect=PermissionError("read-only filesystem"),
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        with pytest.raises(PermissionError, match="read-only filesystem"):
            gen.generate(
                clean_repo_path=clean_repo,
                dataset_name="fail-write",
                template_ids=["sqli_test"],
                target_files={"sqli_test": "app/views.py"},
                dataset_version="v1",
            )


# ---------------------------------------------------------------------------
# Subprocess call ordering
# ---------------------------------------------------------------------------


def test_generate_subprocess_call_order(clean_repo, datasets_root):
    """generate() issues git commands in the expected order."""
    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {}

    run_calls = []
    check_output_calls = []

    def _track_run(*args, **kwargs):
        run_calls.append(args[0])
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=b"", stderr=b"")

    def _track_check_output(cmd, **kwargs):
        check_output_calls.append(cmd)
        return f"{BASE_SHA}\n" if len(check_output_calls) == 1 else f"{HEAD_SHA}\n"

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_track_run,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=_track_check_output,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="order-check",
            template_ids=[],
            target_files={},
            dataset_version="v1",
        )

    assert run_calls[0] == ["git", "init"]
    assert run_calls[1] == ["git", "add", "."]
    assert run_calls[2][:2] == ["git", "commit"]
    assert check_output_calls[0] == ["git", "rev-parse", "HEAD"]
    assert run_calls[3] == ["git", "add", "."]
    assert run_calls[4][:2] == ["git", "commit"]
    assert check_output_calls[1] == ["git", "rev-parse", "HEAD"]


# ---------------------------------------------------------------------------
# diff_spec.yaml content correctness
# ---------------------------------------------------------------------------


def test_generate_diff_spec_refs(clean_repo, datasets_root):
    """diff_spec.yaml base_ref and head_ref come from the two rev-parse outputs."""
    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {}

    custom_base = "cafe0001" * 5
    custom_head = "beef0002" * 5
    seq = _CheckOutputSequence([f"{custom_base}\n", f"{custom_head}\n"])

    with (
        patch("sec_review_framework.ground_truth.diff_generator.shutil.copytree"),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="ref-check",
            template_ids=[],
            target_files={},
            dataset_version="v1",
        )

    spec = yaml.safe_load(
        (datasets_root / "targets" / "ref-check" / "diff_spec.yaml").read_text()
    )
    assert spec["base_ref"] == custom_base
    assert spec["head_ref"] == custom_head


# ---------------------------------------------------------------------------
# repo destination path
# ---------------------------------------------------------------------------


def test_generate_repo_dest_path(clean_repo, datasets_root):
    """Repo is copied to datasets_root/targets/{dataset_name}/repo/."""
    mock_injector = MagicMock(spec=VulnInjector)
    mock_injector.templates = {}
    seq = _CheckOutputSequence([f"{BASE_SHA}\n", f"{HEAD_SHA}\n"])

    with (
        patch(
            "sec_review_framework.ground_truth.diff_generator.shutil.copytree"
        ) as mock_copy,
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.run",
            side_effect=_subprocess_side_effect,
        ),
        patch(
            "sec_review_framework.ground_truth.diff_generator.subprocess.check_output",
            side_effect=seq,
        ),
    ):
        gen = _make_generator(datasets_root, mock_injector)
        gen.generate(
            clean_repo_path=clean_repo,
            dataset_name="path-check",
            template_ids=[],
            target_files={},
            dataset_version="v1",
        )

    expected_dest = datasets_root / "targets" / "path-check" / "repo"
    mock_copy.assert_called_once_with(clean_repo, expected_dest, dirs_exist_ok=True)
