"""Loader + validator for signal correlation rules (B-064).

Reads ``src/genkei/data/signal_rules.yml`` into a list of
``CorrelationRule`` objects suitable for ``signal_store.detect_stacks``.
The loader exists as a separate module from the rules YAML so the
config can evolve without code changes and from the store module so
callers don't have to pull `yaml` for tests that exercise the pure
correlator on synthetic rules.

Validation rules:
  * ``version`` must be 1 (bumped if/when the schema changes).
  * Every rule must declare ``name`` / ``direction`` / ``components``.
  * ``direction`` must be one of ``signal_store.DIRECTIONS``.
  * Each component must declare ``source`` and ``weight``;
    ``signal_kind`` is optional (None = wildcard).
  * ``min_score`` and ``window_days`` get sensible defaults if absent
    but are validated to be positive.
  * ``decay_half_life_days`` is optional (B-099). Absent → age-decay off
    (flat scoring); when present it must be strictly positive.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from genkei.experiments.signal_store import (
    DIRECTIONS,
    CorrelationRule,
    RuleComponent,
)

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_rules.yml"


def load_rules(path: Path = DEFAULT_RULES_PATH) -> list[CorrelationRule]:
    """Load correlation rules from a YAML file; raise on malformed content."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Signal rules file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    return parse_rules(data, source=str(path))


def parse_rules(data: object, *, source: str = "<inline>") -> list[CorrelationRule]:
    """Parse a YAML-loaded mapping into ``CorrelationRule`` instances.

    Split from ``load_rules`` so tests can feed a dict directly without
    writing to disk.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{source}: root must be a mapping")
    version = data.get("version")
    if version != 1:
        raise ValueError(f"{source}: unsupported version {version!r} (expected 1)")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise ValueError(f"{source}: `rules` must be a list")
    out: list[CorrelationRule] = []
    seen_names: set[str] = set()
    for idx, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: rule[{idx}] must be a mapping")
        rule = _parse_rule(raw, idx=idx, source=source)
        if rule.name in seen_names:
            raise ValueError(f"{source}: duplicate rule name {rule.name!r}")
        seen_names.add(rule.name)
        out.append(rule)
    return out


def _parse_rule(raw: dict, *, idx: int, source: str) -> CorrelationRule:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: rule[{idx}] missing `name`")
    direction = raw.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError(
            f"{source}: rule {name!r} has invalid direction {direction!r} "
            f"(expected one of {sorted(DIRECTIONS)})"
        )
    description = str(raw.get("description") or "").strip()
    raw_components = raw.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ValueError(f"{source}: rule {name!r} missing `components`")
    components = [
        _parse_component(c, idx=i, rule_name=name, source=source)
        for i, c in enumerate(raw_components)
    ]
    window_days = _parse_positive_int(
        raw.get("window_days", 7), label="window_days", rule_name=name, source=source
    )
    min_score = _parse_decimal(
        raw.get("min_score", "1.5"),
        label="min_score",
        rule_name=name,
        source=source,
    )
    if min_score < Decimal("0"):
        raise ValueError(f"{source}: rule {name!r} min_score must be >= 0")
    min_distinct_sources = _parse_positive_int(
        raw.get("min_distinct_sources", 2),
        label="min_distinct_sources",
        rule_name=name,
        source=source,
    )
    horizon = raw.get("horizon", "equity:core")
    if not isinstance(horizon, str) or not horizon.strip():
        raise ValueError(f"{source}: rule {name!r} horizon must be a non-empty string")
    decay_half_life_days = _parse_optional_decay(
        raw.get("decay_half_life_days"), rule_name=name, source=source
    )
    return CorrelationRule(
        name=name,
        description=description,
        direction=direction,
        components=components,
        horizon=horizon.strip(),
        window_days=window_days,
        min_score=min_score,
        min_distinct_sources=min_distinct_sources,
        decay_half_life_days=decay_half_life_days,
    )


def _parse_component(
    raw: object, *, idx: int, rule_name: str, source: str
) -> RuleComponent:
    if not isinstance(raw, dict):
        raise ValueError(
            f"{source}: rule {rule_name!r} component[{idx}] must be a mapping"
        )
    component_source = raw.get("source")
    if not isinstance(component_source, str) or not component_source:
        raise ValueError(
            f"{source}: rule {rule_name!r} component[{idx}] missing `source`"
        )
    raw_kind = raw.get("signal_kind")
    if raw_kind is not None and (not isinstance(raw_kind, str) or not raw_kind):
        raise ValueError(
            f"{source}: rule {rule_name!r} component[{idx}] `signal_kind` must be "
            "a non-empty string or null"
        )
    weight = _parse_decimal(
        raw.get("weight", "1.0"),
        label=f"component[{idx}].weight",
        rule_name=rule_name,
        source=source,
    )
    if weight <= Decimal("0"):
        raise ValueError(
            f"{source}: rule {rule_name!r} component[{idx}] weight must be > 0"
        )
    return RuleComponent(
        source=component_source,
        signal_kind=raw_kind,
        weight=weight,
    )


def _parse_optional_decay(
    value: object, *, rule_name: str, source: str
) -> Decimal | None:
    """Parse the optional ``decay_half_life_days`` field (B-099).

    Absent / null → ``None`` (age-decay off, flat scoring). When present it
    must parse as a strictly-positive number of days; a half-life of zero or
    below has no meaning.
    """
    if value is None:
        return None
    half_life = _parse_decimal(
        value, label="decay_half_life_days", rule_name=rule_name, source=source
    )
    if half_life <= Decimal("0"):
        raise ValueError(
            f"{source}: rule {rule_name!r} decay_half_life_days must be > 0"
        )
    return half_life


def _parse_decimal(value: object, *, label: str, rule_name: str, source: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"{source}: rule {rule_name!r} {label} not a number: {value!r}"
        ) from exc


def _parse_positive_int(
    value: object, *, label: str, rule_name: str, source: str
) -> int:
    if isinstance(value, bool):
        raise ValueError(
            f"{source}: rule {rule_name!r} {label} not an integer: {value!r}"
        )
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{source}: rule {rule_name!r} {label} not an integer: {value!r}"
        ) from exc
    if parsed < 1:
        raise ValueError(
            f"{source}: rule {rule_name!r} {label} must be >= 1, got {parsed}"
        )
    return parsed
