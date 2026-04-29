"""Importer for MITRE CWE Demonstrative Examples.

Parses a local cwec_vX.X.xml file (extracted from the cwec_X.X.xml.zip
distributed at https://cwe.mitre.org/data/xml/) and creates ground-truth
labels in the sec-review database.

Each ``<Demonstrative_Example>`` under a ``<Weakness>`` entry produces:
- A positive ``dataset_labels`` row for each ``<Example_Code Nature="Bad">``.
- A negative ``dataset_negative_labels`` row for each
  ``<Example_Code Nature="Good">``.

The importer is idempotent: deterministic IDs mean repeated runs INSERT OR
IGNORE without effect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import defusedxml.ElementTree as ET

from sec_review_framework.data.findings import VulnClass
from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_negative_source_check_includes,
    ensure_source_check_includes,
)

# ---------------------------------------------------------------------------
# Language → file extension mapping
# ---------------------------------------------------------------------------

_LANG_TO_EXT: dict[str, str] = {
    "C": ".c",
    "C++": ".cpp",
    "C#": ".cs",
    "Java": ".java",
    "Python": ".py",
    "JavaScript": ".js",
    "PHP": ".php",
    "Ruby": ".rb",
    "Perl": ".pl",
    "ASP": ".asp",
    "ASP.NET": ".aspx",
    "Go": ".go",
    "Rust": ".rs",
    "Swift": ".swift",
    "Kotlin": ".kt",
    "Shell": ".sh",
    "SQL": ".sql",
    "XML": ".xml",
    "HTML": ".html",
}

# ---------------------------------------------------------------------------
# CWE → VulnClass mapping (extended from CVEResolver)
# ---------------------------------------------------------------------------

_CWE_TO_VULN_CLASS: dict[str, VulnClass] = {
    "CWE-89": VulnClass.SQLI,
    "CWE-79": VulnClass.XSS,
    "CWE-918": VulnClass.SSRF,
    "CWE-78": VulnClass.RCE,
    "CWE-77": VulnClass.RCE,
    "CWE-22": VulnClass.PATH_TRAVERSAL,
    "CWE-23": VulnClass.PATH_TRAVERSAL,
    "CWE-36": VulnClass.PATH_TRAVERSAL,
    "CWE-327": VulnClass.CRYPTO_MISUSE,
    "CWE-326": VulnClass.CRYPTO_MISUSE,
    "CWE-330": VulnClass.CRYPTO_MISUSE,
    "CWE-338": VulnClass.CRYPTO_MISUSE,
    "CWE-798": VulnClass.HARDCODED_SECRET,
    "CWE-259": VulnClass.HARDCODED_SECRET,
    "CWE-502": VulnClass.DESERIALIZATION,
    "CWE-611": VulnClass.XXE,
    "CWE-601": VulnClass.OPEN_REDIRECT,
    "CWE-284": VulnClass.AUTH_BYPASS,
    "CWE-285": VulnClass.AUTH_BYPASS,
    "CWE-269": VulnClass.AUTH_BYPASS,
    "CWE-287": VulnClass.AUTH_BYPASS,
    "CWE-119": VulnClass.MEMORY_SAFETY,
    "CWE-120": VulnClass.MEMORY_SAFETY,
    "CWE-121": VulnClass.MEMORY_SAFETY,
    "CWE-122": VulnClass.MEMORY_SAFETY,
    "CWE-125": VulnClass.MEMORY_SAFETY,
    "CWE-126": VulnClass.MEMORY_SAFETY,
    "CWE-127": VulnClass.MEMORY_SAFETY,
    "CWE-131": VulnClass.MEMORY_SAFETY,
    "CWE-134": VulnClass.MEMORY_SAFETY,
    "CWE-190": VulnClass.MEMORY_SAFETY,
    "CWE-191": VulnClass.MEMORY_SAFETY,
    "CWE-369": VulnClass.MEMORY_SAFETY,
    "CWE-415": VulnClass.MEMORY_SAFETY,
    "CWE-416": VulnClass.MEMORY_SAFETY,
    "CWE-476": VulnClass.MEMORY_SAFETY,
    "CWE-787": VulnClass.MEMORY_SAFETY,
    "CWE-824": VulnClass.MEMORY_SAFETY,
    "CWE-843": VulnClass.MEMORY_SAFETY,
}

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class MitreDemoExamplesImportResult:
    """Summary of a completed import run."""

    dataset_name: str
    cwec_version: str
    positives_inserted: int
    negatives_inserted: int
    cwe_count: int
    example_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CWEC_NS = "http://cwe.mitre.org/cwe-7"


def _lang_ext(language: str) -> str:
    """Map a CWE ``Language`` attribute value to a file extension."""
    return _LANG_TO_EXT.get(language, f".{language.lower()[:8]}")


def _vuln_class_for(cwe_id: str) -> str:
    """Return the vuln_class string for a CWE ID, falling back to 'other'."""
    vc = _CWE_TO_VULN_CLASS.get(cwe_id, VulnClass.OTHER)
    return str(vc)


def _text(el) -> str:  # type: ignore[no-untyped-def]  # ElementTree element
    """Return the concatenated text content of an element and its descendants."""
    return "".join(el.itertext()).strip()


def _strip_ns(tag: str) -> str:
    """Strip the Clark-notation namespace from a tag name."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _det_id(ver: str, cwe_num: str, ex_idx: int, nature: str, lang: str) -> str:
    """Deterministic label ID for idempotent INSERT OR IGNORE."""
    return f"mitre-demo::v{ver}::CWE-{cwe_num}::ex{ex_idx}::{nature}::{lang}"


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


@dataclass
class _ExampleCode:
    nature: str  # "Bad" or "Good"
    language: str
    code: str


@dataclass
class _DemoExample:
    intro_text: str
    codes: list[_ExampleCode]


@dataclass
class _WeaknessExamples:
    cwe_num: str  # e.g. "89"
    cwe_id: str  # e.g. "CWE-89"
    examples: list[_DemoExample]


def _parse_cwec_xml(
    xml_path: Path,
    languages: list[str] | None,
    cwe_filter: list[str] | None,
) -> list[_WeaknessExamples]:
    """Parse cwec XML and return structured examples, applying filters."""
    tree = ET.parse(str(xml_path))
    root = tree.getroot()

    # The root element is Weakness_Catalog; weaknesses are direct children.
    # The XML may or may not use a namespace.
    tag = _strip_ns(root.tag)
    if tag not in ("Weakness_Catalog", "Weaknesses"):
        # Try to find weaknesses anywhere
        pass

    results: list[_WeaknessExamples] = []

    # Support both namespaced and non-namespaced XML
    # Find all Weakness elements
    weaknesses = root.findall(".//{%s}Weakness" % _CWEC_NS)
    if not weaknesses:
        # Try without namespace
        weaknesses = root.findall(".//Weakness")

    for weakness in weaknesses:
        cwe_num = weakness.get("ID", "")
        if not cwe_num:
            continue

        cwe_id = f"CWE-{cwe_num}"
        if cwe_filter is not None and cwe_id not in cwe_filter:
            continue

        # Find Demonstrative_Examples section
        demo_section = weakness.find("{%s}Demonstrative_Examples" % _CWEC_NS)
        if demo_section is None:
            demo_section = weakness.find("Demonstrative_Examples")
        if demo_section is None:
            continue

        examples: list[_DemoExample] = []

        for demo_el in demo_section:
            if _strip_ns(demo_el.tag) != "Demonstrative_Example":
                continue

            # Collect intro text
            intro_parts: list[str] = []
            for child in demo_el:
                child_tag = _strip_ns(child.tag)
                if child_tag == "Intro_Text":
                    intro_parts.append(_text(child))

            intro_text = " ".join(intro_parts).strip()

            # Collect Example_Code blocks
            codes: list[_ExampleCode] = []
            for child in demo_el:
                child_tag = _strip_ns(child.tag)
                if child_tag != "Example_Code":
                    continue
                nature = child.get("Nature", "")
                language = child.get("Language", "")
                if nature not in ("Bad", "Good"):
                    continue
                if not language:
                    continue
                if languages is not None and language not in languages:
                    continue
                code_text = _text(child)
                codes.append(_ExampleCode(nature=nature, language=language, code=code_text))

            if codes:
                examples.append(_DemoExample(intro_text=intro_text, codes=codes))

        if examples:
            results.append(_WeaknessExamples(
                cwe_num=cwe_num,
                cwe_id=cwe_id,
                examples=examples,
            ))

    return results


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------


async def import_mitre_demonstrative_examples(
    db: Database,
    *,
    cwec_xml_path: Path,
    cwec_version: str,
    archive_url: str,
    archive_sha256: str,
    languages: list[str] | None = None,
    cwe_filter: list[str] | None = None,
) -> MitreDemoExamplesImportResult:
    """Import MITRE CWE Demonstrative Examples from a local cwec XML file.

    Idempotent on (cwec_version, cwe_id, example_index, language, nature).

    Args:
        db: Initialised Database instance.
        cwec_xml_path: Local path to the extracted cwec_vX.X.xml file.
        cwec_version: Version string, e.g. "4.18".
        archive_url: Source URL for traceability (the cwec_X.X.xml.zip).
        archive_sha256: SHA-256 hex digest of the zip archive.
        languages: If given, only import example codes in these languages
            (using the Language attribute value, e.g. "C", "Java").
        cwe_filter: If given, only import examples from these CWE IDs
            (e.g. ["CWE-89", "CWE-79"]).

    Returns:
        MitreDemoExamplesImportResult with counts of inserted rows.
    """
    dataset_name = f"mitre-cwe-demo-v{cwec_version.replace('.', '-')}"
    now_ts = datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # 1. Ensure source allowlists include 'mitre_demo'
    # ------------------------------------------------------------------
    await ensure_source_check_includes(db, "mitre_demo")
    await ensure_negative_source_check_includes(db, "mitre_demo")

    # ------------------------------------------------------------------
    # 2. Upsert the single archive dataset row (idempotent)
    # ------------------------------------------------------------------
    existing = await db.get_dataset(dataset_name)
    if existing is None:
        await db.create_dataset({
            "name": dataset_name,
            "kind": "archive",
            "archive_url": archive_url,
            "archive_sha256": archive_sha256,
            "archive_format": "zip",
            "metadata_json": json.dumps({
                "benchmark": "mitre-cwe-demo",
                "cwec_version": cwec_version,
                "language": "multi",
            }),
            "created_at": now_ts,
        })

    # ------------------------------------------------------------------
    # 3. Parse the XML
    # ------------------------------------------------------------------
    weakness_list = _parse_cwec_xml(cwec_xml_path, languages, cwe_filter)

    # ------------------------------------------------------------------
    # 4. Build and insert label rows
    # ------------------------------------------------------------------
    positives: list[dict] = []
    negatives: list[dict] = []

    cwe_count = len(weakness_list)
    example_count = 0

    for weakness in weakness_list:
        cwe_id = weakness.cwe_id
        cwe_num = weakness.cwe_num
        vuln_class = _vuln_class_for(cwe_id)

        for ex_idx, demo in enumerate(weakness.examples):
            example_count += 1
            intro = demo.intro_text[:500]

            # Group codes by language to handle multiple bad/good per language
            # Track per-language counters for cases with multiple bad codes
            bad_counts: dict[str, int] = {}
            good_counts: dict[str, int] = {}

            for code in demo.codes:
                nature = code.nature
                lang = code.language
                ext = _lang_ext(lang)
                line_count = max(1, len(code.code.splitlines()))

                if nature == "Bad":
                    bad_counts[lang] = bad_counts.get(lang, 0) + 1
                    lang_idx = bad_counts[lang] - 1
                    # Only first bad code per language gets a simple path;
                    # subsequent ones get a counter suffix
                    if lang_idx == 0:
                        file_path = f"examples/{cwe_id}/{ex_idx}/bad{ext}"
                    else:
                        file_path = f"examples/{cwe_id}/{ex_idx}/bad_{lang_idx}{ext}"

                    label_id = _det_id(cwec_version, cwe_num, ex_idx, f"bad{'' if lang_idx == 0 else f'_{lang_idx}'}", lang)
                    positives.append({
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": f"v{cwec_version}",
                        "file_path": file_path,
                        "line_start": 1,
                        "line_end": line_count,
                        "cwe_id": cwe_id,
                        "vuln_class": vuln_class,
                        "severity": "medium",
                        "description": intro,
                        "source": "mitre_demo",
                        "source_ref": f"CWE-{cwe_num}::ex{ex_idx}::bad",
                        "confidence": "high",
                        "created_at": now_ts,
                    })

                elif nature == "Good":
                    good_counts[lang] = good_counts.get(lang, 0) + 1
                    lang_idx = good_counts[lang] - 1
                    if lang_idx == 0:
                        file_path = f"examples/{cwe_id}/{ex_idx}/good{ext}"
                    else:
                        file_path = f"examples/{cwe_id}/{ex_idx}/good_{lang_idx}{ext}"

                    label_id = _det_id(cwec_version, cwe_num, ex_idx, f"good{'' if lang_idx == 0 else f'_{lang_idx}'}", lang)
                    negatives.append({
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": f"v{cwec_version}",
                        "file_path": file_path,
                        "cwe_id": cwe_id,
                        "vuln_class": vuln_class,
                        "source": "mitre_demo",
                        "source_ref": f"CWE-{cwe_num}::ex{ex_idx}::good",
                        "created_at": now_ts,
                    })

    if positives:
        await db.append_dataset_labels(positives)
    if negatives:
        await db.append_dataset_negative_labels(negatives)

    return MitreDemoExamplesImportResult(
        dataset_name=dataset_name,
        cwec_version=cwec_version,
        positives_inserted=len(positives),
        negatives_inserted=len(negatives),
        cwe_count=cwe_count,
        example_count=example_count,
    )
