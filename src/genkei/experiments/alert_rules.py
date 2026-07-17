"""Loader + validator for threshold alert rules (B-068).

Reads ``src/genkei/data/alert_rules.yml`` into a list of :class:`AlertRule`
objects for :func:`genkei.experiments.alert_engine.evaluate_alerts`. Kept
separate from ``alert_engine`` (which holds the pure evaluator + persistence)
for the same reason ``signal_rules`` is separate from ``signal_store``: config
can evolve without code changes, and tests can exercise the evaluator on
synthetic rules without pulling ``yaml``.

Validation rules:
  * ``version`` must be 1 (bumped if/when the schema changes).
  * Every rule must declare ``name`` and ``severity``.
  * ``severity`` must be one of ``alert_engine.SEVERITIES``.
  * ``match`` is optional; when present each of ``rules`` / ``asset_class`` /
    ``horizon`` / ``direction`` must be a list of non-empty strings.
  * ``asset_class`` values must be valid signal asset classes; ``direction``
    values must be valid signal directions — a typo here silently never
    matches, so fail loud at load time instead.
  * ``min_score`` / ``min_distinct_sources`` default to 0 (no extra floor) and
    are validated non-negative.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from genkei.experiments.alert_engine import SEVERITIES, AlertRule
from genkei.experiments.signal_store import ASSET_CLASSES, DIRECTIONS

DEFAULT_ALERT_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "alert_rules.yml"
)

_MATCH_KEYS = ("rules", "asset_class", "horizon", "direction")


def load_alert_rules(path: Path = DEFAULT_ALERT_RULES_PATH) -> list[AlertRule]:
    """Load alert rules from a YAML file; raise on malformed content."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Alert rules file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    return parse_alert_rules(data, source=str(path))


def parse_alert_rules(data: object, *, source: str = "<inline>") -> list[AlertRule]:
    """Parse a YAML-loaded mapping into :class:`AlertRule` instances.

    Split from :func:`load_alert_rules` so tests can feed a dict directly.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{source}: root must be a mapping")
    version = data.get("version")
    if version != 1:
        raise ValueError(f"{source}: unsupported version {version!r} (expected 1)")
    raw_rules = data.get("alerts")
    if not isinstance(raw_rules, list):
        raise ValueError(f"{source}: `alerts` must be a list")
    out: list[AlertRule] = []
    seen_names: set[str] = set()
    for idx, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: alert[{idx}] must be a mapping")
        rule = _parse_alert(raw, idx=idx, source=source)
        if rule.name in seen_names:
            raise ValueError(f"{source}: duplicate alert name {rule.name!r}")
        seen_names.add(rule.name)
        out.append(rule)
    return out


def _parse_alert(raw: dict, *, idx: int, source: str) -> AlertRule:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: alert[{idx}] missing `name`")
    severity = raw.get("severity")
    if severity not in SEVERITIES:
        raise ValueError(
            f"{source}: alert {name!r} has invalid severity {severity!r} "
            f"(expected one of {list(SEVERITIES)})"
        )
    description = str(raw.get("description") or "").strip()

    match = raw.get("match") or {}
    if not isinstance(match, dict):
        raise ValueError(f"{source}: alert {name!r} `match` must be a mapping")
    unknown = set(match) - set(_MATCH_KEYS)
    if unknown:
        raise ValueError(
            f"{source}: alert {name!r} `match` has unknown keys "
            f"{sorted(unknown)} (expected {list(_MATCH_KEYS)})"
        )
    match_rules = _parse_str_list(match.get("rules"), field="rules", name=name, source=source)
    match_asset_classes = _parse_str_list(
        match.get("asset_class"), field="asset_class", name=name, source=source
    )
    _reject_unknown(
        match_asset_classes, valid=ASSET_CLASSES, field="asset_class", name=name, source=source
    )
    match_horizons = _parse_str_list(
        match.get("horizon"), field="horizon", name=name, source=source
    )
    match_directions = _parse_str_list(
        match.get("direction"), field="direction", name=name, source=source
    )
    _reject_unknown(
        match_directions, valid=DIRECTIONS, field="direction", name=name, source=source
    )

    min_score = _parse_decimal(
        raw.get("min_score", "0"), label="min_score", name=name, source=source
    )
    if min_score < Decimal("0"):
        raise ValueError(f"{source}: alert {name!r} min_score must be >= 0")
    min_distinct_sources = _parse_non_negative_int(
        raw.get("min_distinct_sources", 0),
        label="min_distinct_sources",
        name=name,
        source=source,
    )
    return AlertRule(
        name=name,
        description=description,
        severity=severity,
        match_rules=match_rules,
        match_asset_classes=match_asset_classes,
        match_horizons=match_horizons,
        match_directions=match_directions,
        min_score=min_score,
        min_distinct_sources=min_distinct_sources,
    )


def _parse_str_list(
    value: object, *, field: str, name: str, source: str
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(v, str) and v.strip() for v in value
    ):
        raise ValueError(
            f"{source}: alert {name!r} match.{field} must be a list of non-empty strings"
        )
    return tuple(v.strip() for v in value)


def _reject_unknown(
    values: tuple[str, ...], *, valid: frozenset[str], field: str, name: str, source: str
) -> None:
    bad = [v for v in values if v not in valid]
    if bad:
        raise ValueError(
            f"{source}: alert {name!r} match.{field} has invalid value(s) {bad} "
            f"(expected any of {sorted(valid)})"
        )


def _parse_decimal(value: object, *, label: str, name: str, source: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"{source}: alert {name!r} {label} not a number: {value!r}"
        ) from exc


def _parse_non_negative_int(value: object, *, label: str, name: str, source: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{source}: alert {name!r} {label} not an integer: {value!r}")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{source}: alert {name!r} {label} not an integer: {value!r}"
        ) from exc
    if parsed < 0:
        raise ValueError(
            f"{source}: alert {name!r} {label} must be >= 0, got {parsed}"
        )
    return parsed
