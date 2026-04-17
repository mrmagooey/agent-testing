"""Loaders for prompt text files shipped with the package.

System prompts are returned verbatim. User prompts are templates with
``{placeholder}`` markers; render them with ``str.format(**kwargs)`` at the
call site (substituted values are treated as literals, so embedded braces in
e.g. file contents or the FINDING_OUTPUT_FORMAT JSON example are safe).
"""

from pathlib import Path

_SYSTEM_DIR = Path(__file__).parent / "system"
_USER_DIR = Path(__file__).parent / "user"


def load_system_prompt(*parts: str) -> str:
    """Read a system prompt text file and return its stripped contents.

    Args:
        *parts: Path components under ``prompts/system/``. For example,
            ``load_system_prompt("single_agent.txt")`` or
            ``load_system_prompt("per_vuln_class", "sqli.txt")``.

    Returns:
        The file contents with surrounding whitespace stripped.
    """
    return _SYSTEM_DIR.joinpath(*parts).read_text(encoding="utf-8").strip()


def load_user_prompt(*parts: str) -> str:
    """Read a user prompt template text file and return its stripped contents.

    The returned string contains ``{name}`` placeholders intended for
    ``str.format(**kwargs)``.

    Args:
        *parts: Path components under ``prompts/user/``.

    Returns:
        The template string with surrounding whitespace stripped.
    """
    return _USER_DIR.joinpath(*parts).read_text(encoding="utf-8").strip()
