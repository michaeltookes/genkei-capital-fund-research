"""Frontmatter validator for `docs/research/decisions/` (B-049 / B-050).

The decision-log directory is an append-only audit trail. The
frontmatter on each file is the contract `/reflect-decisions` relies
on to find pending decisions, compute elapsed time, and pair outcomes
against benchmarks. If the frontmatter drifts (typo'd key, missing
field, invalid value), the reflection cycle silently mis-classifies
the file or skips it — exactly the failure mode the watchlist-health
work was added to avoid for ingest sources.

This test walks the directory and asserts the contract on every real
decision file (skipping `_template.md` and `README.md`).
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISIONS_DIR = REPO_ROOT / "docs" / "research" / "decisions"
SKIP_FILES = {"_template.md", "README.md"}

REQUIRED_KEYS = {
    "date",
    "asset",
    "sleeve",
    "horizon",
    "confidence",
    "status",
    "trigger_reassessment",
}

VALID_SLEEVES = {"equity-core", "crypto-core", "crypto-tactical", "macro-aware"}
VALID_HORIZONS = {"weeks", "months", "years"}
VALID_CONFIDENCES = {"low", "medium", "high"}
VALID_STATUSES = {"pending", "resolved", "deferred"}


def _decision_files() -> list[Path]:
    if not DECISIONS_DIR.exists():
        return []
    return sorted(
        p
        for p in DECISIONS_DIR.glob("*.md")
        if p.name not in SKIP_FILES
    )


def _parse_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing opening `---` frontmatter fence")
    # Find the closing fence after the first one
    rest = text[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        raise ValueError(f"{path.name}: missing closing `---` frontmatter fence")
    yaml_block = rest[:end]
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name}: frontmatter must parse to a mapping")
    return parsed


class DecisionDirStructureTests(unittest.TestCase):
    def test_decisions_dir_exists(self) -> None:
        self.assertTrue(
            DECISIONS_DIR.exists(),
            "docs/research/decisions/ missing — required by /research + "
            "/reflect-decisions skills",
        )

    def test_template_exists(self) -> None:
        self.assertTrue(
            (DECISIONS_DIR / "_template.md").exists(),
            "_template.md missing — /research relies on it as the file skeleton",
        )

    def test_readme_exists(self) -> None:
        self.assertTrue(
            (DECISIONS_DIR.parent / "README.md").exists(),
            "docs/research/README.md missing — documents the decision-log contract",
        )


class DecisionFrontmatterContractTests(unittest.TestCase):
    """Pin the frontmatter contract that `/reflect-decisions` relies on."""

    def test_every_decision_file_has_complete_frontmatter(self) -> None:
        files = _decision_files()
        # The directory should always have at least one real example so
        # the validator has something to validate (and so future
        # contributors have a concrete reference).
        self.assertGreater(
            len(files), 0, "no decision files found — at least one example expected"
        )
        for path in files:
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                missing = REQUIRED_KEYS - set(fm)
                self.assertFalse(
                    missing, f"{path.name}: missing required frontmatter keys: {missing}"
                )

    def test_date_is_iso(self) -> None:
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                # PyYAML parses YYYY-MM-DD into a datetime.date directly.
                self.assertIsInstance(
                    fm["date"],
                    date,
                    f"{path.name}: `date` must be ISO YYYY-MM-DD (parsed value: "
                    f"{fm['date']!r})",
                )

    def test_sleeve_is_valid(self) -> None:
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                self.assertIn(
                    fm["sleeve"],
                    VALID_SLEEVES,
                    f"{path.name}: `sleeve` must be one of {sorted(VALID_SLEEVES)}",
                )

    def test_horizon_is_valid(self) -> None:
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                self.assertIn(
                    fm["horizon"],
                    VALID_HORIZONS,
                    f"{path.name}: `horizon` must be one of {sorted(VALID_HORIZONS)}",
                )

    def test_confidence_is_valid(self) -> None:
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                self.assertIn(
                    fm["confidence"],
                    VALID_CONFIDENCES,
                    f"{path.name}: `confidence` must be one of "
                    f"{sorted(VALID_CONFIDENCES)}",
                )

    def test_status_is_valid(self) -> None:
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                self.assertIn(
                    fm["status"],
                    VALID_STATUSES,
                    f"{path.name}: `status` must be one of {sorted(VALID_STATUSES)}",
                )

    def test_trigger_reassessment_is_nonempty_string(self) -> None:
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                trigger = fm["trigger_reassessment"]
                self.assertIsInstance(
                    trigger,
                    str,
                    f"{path.name}: `trigger_reassessment` must be a string",
                )
                self.assertTrue(
                    trigger.strip(),
                    f"{path.name}: `trigger_reassessment` must not be empty — "
                    f"the reflection cycle uses it to know when to short-circuit",
                )

    def test_outcome_placeholder_is_present_when_pending(self) -> None:
        """Pending decisions must reserve the Outcome section so /reflect-decisions
        knows where to write."""
        for path in _decision_files():
            with self.subTest(path=path.name):
                fm = _parse_frontmatter(path)
                if fm["status"] != "pending":
                    continue
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "## Outcome",
                    body,
                    f"{path.name}: pending decisions must include a `## Outcome` "
                    f"section heading for /reflect-decisions to fill in",
                )


class TemplateFileShapeTests(unittest.TestCase):
    """The template itself must satisfy the contract (it's the source for new files)."""

    def test_template_frontmatter_is_complete(self) -> None:
        template = DECISIONS_DIR / "_template.md"
        fm = _parse_frontmatter(template)
        missing = REQUIRED_KEYS - set(fm)
        self.assertFalse(
            missing,
            f"_template.md missing required frontmatter keys: {missing} — would "
            f"propagate to every new decision file copied from it",
        )

    def test_template_includes_phase_a_and_phase_b_headings(self) -> None:
        template = DECISIONS_DIR / "_template.md"
        body = template.read_text(encoding="utf-8")
        self.assertIn(
            "Phase A",
            body,
            "_template.md must reference Phase A (case for + against) per the "
            "methodology",
        )
        self.assertIn(
            "Phase B",
            body,
            "_template.md must reference Phase B (counter-thesis) per the methodology",
        )


if __name__ == "__main__":
    unittest.main()
