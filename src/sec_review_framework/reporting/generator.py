"""ReportGenerator ABC — base contract for all report renderers."""

from abc import ABC, abstractmethod
from pathlib import Path

from sec_review_framework.data.experiment import RunResult


class ReportGenerator(ABC):
    """Abstract base class for report generators."""

    @abstractmethod
    def render_run(self, result: RunResult, output_dir: Path) -> None:
        """
        Render a per-run report into *output_dir*.

        Implementations should write one or more files (e.g. report.md,
        report.json) whose names are determined by the concrete subclass.

        Parameters
        ----------
        result:
            The complete result of a single experiment run.
        output_dir:
            Directory where output files should be written.
            Callers guarantee it exists before calling this method.
        """
        ...

    @abstractmethod
    def render_matrix(self, results: list[RunResult], output_dir: Path) -> None:
        """
        Render a cross-run matrix report into *output_dir*.

        Parameters
        ----------
        results:
            All RunResult objects from a batch, typically one per matrix cell.
        output_dir:
            Directory where output files should be written.
            Callers guarantee it exists before calling this method.
        """
        ...
