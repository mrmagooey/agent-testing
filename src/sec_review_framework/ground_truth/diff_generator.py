"""Diff dataset generator: injects vulnerabilities into a clean repo and produces diff_spec.yaml."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from sec_review_framework.data.evaluation import GroundTruthLabel
from sec_review_framework.ground_truth.models import LabelStore
from sec_review_framework.ground_truth.vuln_injector import VulnInjector


class DiffDatasetGenerator:
    """
    Takes a clean repo, applies vuln injection templates to create a
    "vulnerable branch", and generates diff_spec.yaml for DiffReview testing.
    """

    def __init__(self, injector: VulnInjector, datasets_root: Path) -> None:
        self.injector = injector
        self.datasets_root = Path(datasets_root)

    def generate(
        self,
        clean_repo_path: Path,
        dataset_name: str,
        template_ids: list[str],
        target_files: dict[str, str],  # template_id → target file path
        dataset_version: str,
    ) -> list[GroundTruthLabel]:
        """
        1. Copy clean repo to datasets/targets/{dataset_name}/repo/
        2. git init + commit (base_ref)
        3. Apply injection templates
        4. git commit (head_ref)
        5. Write diff_spec.yaml
        6. Write labels via LabelStore
        Returns list of GroundTruthLabel records created.
        """
        repo_dest = self.datasets_root / "targets" / dataset_name / "repo"
        repo_dest.parent.mkdir(parents=True, exist_ok=True)

        # Step 1: Copy clean repo
        shutil.copytree(clean_repo_path, repo_dest, dirs_exist_ok=True)

        # Step 2: git init and commit clean state
        subprocess.run(["git", "init"], cwd=repo_dest, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo_dest, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "clean"],
            cwd=repo_dest,
            check=True,
            capture_output=True,
        )
        base_ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dest,
            text=True,
        ).strip()

        # Step 3: Apply injection templates
        labels: list[GroundTruthLabel] = []
        for tmpl_id in template_ids:
            target_file = target_files[tmpl_id]
            result = self.injector.inject(repo_dest, tmpl_id, target_file)
            template = self.injector.templates[tmpl_id]
            label = self.injector.build_label(result, template, dataset_version)
            label.introduced_in_diff = True
            labels.append(label)

        # Step 4: Commit the vulnerable state
        subprocess.run(["git", "add", "."], cwd=repo_dest, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "inject vulns"],
            cwd=repo_dest,
            check=True,
            capture_output=True,
        )
        head_ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dest,
            text=True,
        ).strip()

        # Step 5: Write diff_spec.yaml
        diff_spec = {"base_ref": base_ref, "head_ref": head_ref}
        spec_path = self.datasets_root / "targets" / dataset_name / "diff_spec.yaml"
        spec_path.write_text(yaml.dump(diff_spec))

        # Step 6: Write labels
        LabelStore(self.datasets_root).append(dataset_name, labels)

        return labels
