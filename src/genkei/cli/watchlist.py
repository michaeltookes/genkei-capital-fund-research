"""``genkei watchlist`` — watchlist coverage & data-lake health (B-044).

Five subcommands:

* **list** — dump the watchlist by sleeve (crypto / equities / macro).
* **health** — per-source ingest health: latest collect + normalize
  status, run counts, primary-table liveness. This is the command
  that should have existed when 3/4 sources went dark silently for ~4
  days (G-027/G-028/D-020). Built loud: any source with a recent
  failure or a stale primary table prints a clear FAIL or STALE tag.
  Now also folds in schema-drift findings (B-072) — each drift issue
  surfaces as a row with ``health_status: "DRIFT"`` so the B-071
  staleness alerter automatically opens GitHub issues for them.
* **drift** — focused B-072 view: just the schema-drift findings,
  no ingest-runs or liveness rows. Use when you want to verify a
  recent normalizer/spec change end-to-end without scanning the full
  health output.
* **score** — per-asset composite signal score (B-065 rubric).
  Default: compute in-memory and print sorted by composite_score
  DESC; ``--persist`` writes to ``meta.signals``; ``--since DATE``
  reads previously-persisted history.
* **gaps** — per-asset freshness: when was the most recent data point
  for each watchlist asset, and how many hours ago was that. Surfaces
  individual assets that have fallen behind even when the source as a
  whole is "healthy".

Usage:
  genkei watchlist list
  genkei watchlist list --sleeve crypto
  genkei watchlist health
  genkei watchlist health --json
  genkei watchlist drift
  genkei watchlist score
  genkei watchlist score --persist
  genkei watchlist score --ticker AAPL
  genkei watchlist score --sleeve crypto-tactical
  genkei watchlist score --since 2026-05-01
  genkei watchlist gaps
  genkei watchlist gaps --threshold-hours 48
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from psycopg import sql

from genkei.cli._helpers import json_default as _json_default, parse_date as _parse_date
from genkei.common import db
from genkei.common.schema_drift import check_recent_blobs
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    Watchlist,
    load_watchlist,
)

# Subcommand group — registered into the top-level Typer app via
# ``app.add_typer(watchlist.app, name="watchlist")`` per D-019.
app = typer.Typer(
    name="watchlist",
    help="Watchlist coverage + data-lake health.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_or_exit(config: Path) -> Watchlist:
    try:
        return load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


# ---------------------------------------------------------------------------
# `genkei watchlist list`
# ---------------------------------------------------------------------------


def _format_list_human(wl: Watchlist, *, sleeve: Optional[str]) -> str:
    sections: list[str] = []
    if sleeve in (None, "crypto"):
        sections.append(f"crypto ({len(wl.crypto)})")
        sections.append("-" * len(sections[-1]))
        for c in wl.crypto:
            sections.append(f"  {c.symbol:<8} {c.tier:<10} {c.coingecko_id:<20} {c.name}")
    if sleeve in (None, "equity", "equities"):
        if sections:
            sections.append("")
        sections.append(f"equities ({len(wl.equities)})")
        sections.append("-" * len(sections[-1]))
        for e in wl.equities:
            cik = e.cik or "-"
            sections.append(f"  {e.symbol:<8} {e.tier:<10} {cik:<12} {e.name}")
    if sleeve in (None, "macro"):
        if sections:
            sections.append("")
        sections.append(f"macro ({len(wl.macro)})")
        sections.append("-" * len(sections[-1]))
        for m in wl.macro:
            sections.append(f"  {m.series_id:<14} {m.name}")
    return "\n".join(sections) if sections else "(no entries matched)"


@app.command("list")
def list_cmd(
    sleeve: Annotated[
        Optional[str],
        typer.Option(
            "--sleeve",
            help="Filter to one sleeve: crypto | equity | macro.",
        ),
    ] = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show watchlist assets by sleeve."""
    if sleeve is not None and sleeve not in {"crypto", "equity", "equities", "macro"}:
        raise typer.BadParameter("--sleeve must be crypto, equity, or macro.")
    wl = _load_or_exit(config)
    if json_out:
        payload: dict[str, Any] = {}
        if sleeve in (None, "crypto"):
            payload["crypto"] = [
                {
                    "symbol": c.symbol,
                    "name": c.name,
                    "coingecko_id": c.coingecko_id,
                    "tier": c.tier,
                }
                for c in wl.crypto
            ]
        if sleeve in (None, "equity", "equities"):
            payload["equities"] = [
                {"symbol": e.symbol, "name": e.name, "cik": e.cik, "tier": e.tier}
                for e in wl.equities
            ]
        if sleeve in (None, "macro"):
            payload["macro"] = [
                {"series_id": m.series_id, "name": m.name} for m in wl.macro
            ]
        typer.echo(json.dumps(payload, indent=2, default=_json_default))
    else:
        typer.echo(_format_list_human(wl, sleeve=sleeve))


# ---------------------------------------------------------------------------
# `genkei watchlist health`
# ---------------------------------------------------------------------------

# (source, endpoint) → primary table whose row count we surface as
# the canonical "is data flowing?" signal. Used by both health (table
# row counts) and gaps (per-asset freshness queries).
PRIMARY_TABLES: dict[str, list[str]] = {
    "defillama": [
        "defillama.chain_tvl",
        "defillama.protocol_tvl",
        "defillama.protocol_fees",
    ],
    "fred": ["fred.observations"],
    "sec": ["sec.companies", "sec.filings", "sec.facts"],
    "coingecko": ["coingecko.market_data"],
    "onchain_staking": ["onchain.staking_events"],
    # B-090 — derived view, no ingest_runs row (computed live from
    # coingecko.market_data). Intentionally absent from RECURRING_ENDPOINTS
    # so it doesn't surface as MISSING on the recurring-cron half of the
    # report; only the liveness check (EXISTS SELECT 1) applies.
    "analytics": ["analytics.crypto_relative_strength"],
}

# Recurring (daily-cron) endpoints we expect to see in meta.ingest_runs
# per source. ``health`` scopes its runs table to *just* these — silent
# absence becomes a loud MISSING tag, staleness is judged against
# ``--stale-hours``. One-shot endpoints (``backfill``,
# ``normalize_backfill``, ad-hoc replays) live in meta.ingest_runs for
# audit but don't belong in the recurring-cron monitoring view; tagging
# them STALE 159 hours after a deliberate backfill is a false positive.
RECURRING_ENDPOINTS: dict[str, list[str]] = {
    "defillama": ["collect", "normalize"],
    "fred": ["collect", "normalize"],
    "sec": ["collect", "normalize"],
    "coingecko": ["collect", "normalize"],
    # B-082 staking ingester combines collect+normalize in one step
    # (parses logs inline + writes rows directly to onchain.staking_events,
    # no raw_blobs hop), so only the 'collect' endpoint is recurring.
    "onchain_staking": ["collect"],
}


def _table_identifier(table: str) -> sql.Identifier:
    return sql.Identifier(*table.split(".", 1))


def _query_source_health() -> list[dict[str, Any]]:
    """Per (source, endpoint) latest run with status + age, plus table liveness.

    Scoped to ``RECURRING_ENDPOINTS`` — one-shot endpoints (backfill,
    normalize_backfill, ad-hoc replays) are intentionally filtered out
    so they don't pollute the recurring-cron monitoring view with
    stale-since-the-last-backfill false positives.
    """
    runs_sql = """
        SELECT source, endpoint, status, started_at, finished_at,
               substr(coalesce(error, ''), 1, 200) AS error_snippet
        FROM meta.ingest_runs r1
        WHERE started_at = (
            SELECT max(started_at) FROM meta.ingest_runs r2
            WHERE r2.source = r1.source AND r2.endpoint = r1.endpoint
        )
        ORDER BY source, endpoint
    """
    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(runs_sql)
        seen: set[tuple[str, str]] = set()
        for source, endpoint, status, started, finished, err in cur.fetchall():
            if endpoint not in RECURRING_ENDPOINTS.get(source, []):
                continue  # one-shot endpoint — not part of cron health.
            age_h = (now - started).total_seconds() / 3600 if started else None
            out.append(
                {
                    "source": source,
                    "endpoint": endpoint,
                    "status": status,
                    "last_started_at": started.isoformat() if started else None,
                    "last_finished_at": finished.isoformat() if finished else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                    "error": err if err else None,
                }
            )
            seen.add((source, endpoint))
        # Surface recurring-but-never-run pairs as explicit MISSING entries
        # — silent absence is what bit us last time.
        for source, endpoints in RECURRING_ENDPOINTS.items():
            for endpoint in endpoints:
                if (source, endpoint) not in seen:
                    out.append(
                        {
                            "source": source,
                            "endpoint": endpoint,
                            "status": "missing",
                            "last_started_at": None,
                            "last_finished_at": None,
                            "age_hours": None,
                            "error": None,
                        }
                    )
        # Per-table liveness without exact row-count scans on large hypertables.
        for source, tables in PRIMARY_TABLES.items():
            for table in tables:
                try:
                    cur.execute(
                        sql.SQL("SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)").format(
                            _table_identifier(table)
                        )
                    )
                    has_rows = bool(cur.fetchone()[0])
                except Exception as exc:  # noqa: BLE001 — defensive
                    has_rows = None
                    err_msg = str(exc)
                else:
                    err_msg = None
                out.append(
                    {
                        "source": source,
                        "table": table,
                        "has_rows": has_rows,
                        "error": err_msg,
                    }
                )
    return out


def _health_status_tag(row: dict[str, Any], *, stale_hours: float) -> str:
    """Render one of OK / STALE / FAIL / MISSING / EMPTY / DRIFT for a row."""
    # Drift rows (B-072) come pre-tagged from `_drift_rows`; preserve.
    if row.get("drift_kind"):
        return "DRIFT"
    if "has_rows" in row:
        if row.get("error"):
            return "FAIL"
        if not row["has_rows"]:
            return "EMPTY"
        return "OK"
    if "row_count" in row:
        if row.get("error"):
            return "FAIL"
        if row["row_count"] == 0:
            return "EMPTY"
        return "OK"
    status = row.get("status")
    if status == "missing":
        return "MISSING"
    if status != "success":
        return "FAIL"
    age = row.get("age_hours")
    if age is not None and age > stale_hours:
        return "STALE"
    return "OK"


def _drift_rows(max_age_hours: int = 72) -> list[dict[str, Any]]:
    """Run the B-072 schema-drift check and shape results as health rows.

    Each row has `source`, `endpoint`, `endpoint_kind`, `drift_kind`,
    `detail`, `error`, and `sample_endpoint_name` — enough for
    `_health_status_tag` to tag it DRIFT, for the human/JSON formatters
    to render it, and for the B-071 staleness alerter
    (`.github/workflows/ingest-staleness-check.yml`) picks them up via
    its existing "filter out OK rows, open issues for the rest" pass.
    """
    try:
        issues = check_recent_blobs(max_age_hours=max_age_hours)
    except Exception as exc:  # noqa: BLE001 — defensive
        # Don't let a drift-check failure mask the rest of the health
        # output. Surface it as one DRIFT row instead.
        return [
            {
                "source": "schema_drift",
                "endpoint": "(checker)",
                "endpoint_kind": "(checker)",
                "drift_kind": "CHECKER_ERROR",
                "detail": str(exc)[:300],
                "error": str(exc)[:300],
                "sample_endpoint_name": None,
            }
        ]
    return [
        {
            "source": issue.source,
            "endpoint": issue.endpoint_kind,
            "endpoint_kind": issue.endpoint_kind,
            "drift_kind": issue.kind,
            "detail": issue.detail,
            "error": issue.detail,
            "sample_endpoint_name": issue.sample_endpoint_name,
        }
        for issue in issues
    ]


def _with_health_status(
    rows: list[dict[str, Any]], *, stale_hours: float
) -> list[dict[str, Any]]:
    return [
        {**row, "health_status": _health_status_tag(row, stale_hours=stale_hours)}
        for row in rows
    ]


def _format_health_human(rows: list[dict[str, Any]], *, stale_hours: float) -> str:
    runs = [r for r in rows if "endpoint" in r and "drift_kind" not in r]
    counts = [r for r in rows if "table" in r]
    drift = [r for r in rows if r.get("drift_kind")]
    lines: list[str] = []
    lines.append(f"Source ingest runs (stale = no successful run in {stale_hours}h)")
    lines.append("-" * len(lines[-1]))
    lines.append(f"  {'source':<11} {'endpoint':<10} {'status':<8} {'age':>10}  notes")
    for r in runs:
        tag = _health_status_tag(r, stale_hours=stale_hours)
        age = (
            f"{r['age_hours']}h"
            if r["age_hours"] is not None
            else "-"
        )
        notes = r["error"][:70] if r["error"] else ""
        lines.append(
            f"  {r['source']:<11} {r['endpoint']:<10} {tag:<8} {age:>10}  {notes}"
        )
    lines.append("")
    lines.append("Primary table liveness (EMPTY = downstream queries return nothing)")
    lines.append("-" * len(lines[-1]))
    lines.append(f"  {'source':<11} {'table':<26} {'status':<8} {'data':>8}")
    for r in counts:
        tag = _health_status_tag(r, stale_hours=stale_hours)
        if "has_rows" in r:
            data = "yes" if r["has_rows"] else "no" if r["has_rows"] is not None else "-"
        else:
            data = f"{r['row_count']:,}" if r["row_count"] is not None else "-"
        lines.append(f"  {r['source']:<11} {r['table']:<26} {tag:<8} {data:>8}")
    if drift:
        lines.append("")
        lines.append(
            "Schema drift (B-072 — load-bearing keys missing or wrong type in recent raw_blobs)"
        )
        lines.append("-" * len(lines[-1]))
        lines.append(f"  {'source':<11} {'endpoint_kind':<26} {'kind':<22}  detail")
        for r in drift:
            kind = r["drift_kind"]
            ep_kind = r["endpoint_kind"]
            sample = r.get("sample_endpoint_name")
            detail = r["detail"]
            if sample:
                detail = f"({sample}) {detail}"
            lines.append(f"  {r['source']:<11} {ep_kind:<26} {kind:<22}  {detail[:120]}")
    return "\n".join(lines)


@app.command("health")
def health_cmd(
    stale_hours: Annotated[
        float,
        typer.Option(
            "--stale-hours",
            help="A successful run older than this is tagged STALE.",
            min=1,
        ),
    ] = 36.0,
    drift_max_age_hours: Annotated[
        int,
        typer.Option(
            "--drift-max-age-hours",
            help="Schema-drift check ignores raw_blobs older than this (B-072).",
            min=1,
        ),
    ] = 72,
    skip_drift: Annotated[
        bool,
        typer.Option(
            "--skip-drift",
            help="Skip the B-072 schema-drift check (faster, but blind to silent payload changes).",
        ),
    ] = False,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show per-source ingest health + primary-table liveness + schema drift."""
    rows = _query_source_health()
    if not skip_drift:
        rows = rows + _drift_rows(max_age_hours=drift_max_age_hours)
    if json_out:
        typer.echo(
            json.dumps(
                _with_health_status(rows, stale_hours=stale_hours),
                indent=2,
                default=_json_default,
            )
        )
    else:
        typer.echo(_format_health_human(rows, stale_hours=stale_hours))


# ---------------------------------------------------------------------------
# `genkei watchlist drift`
# ---------------------------------------------------------------------------


@app.command("drift")
def drift_cmd(
    max_age_hours: Annotated[
        int,
        typer.Option(
            "--max-age-hours",
            help="Ignore raw_blobs older than this when sampling.",
            min=1,
        ),
    ] = 72,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """B-072 schema-drift check — load-bearing keys per (source, endpoint_kind).

    Surfaces drift findings only — no ingest-runs or primary-table
    liveness rows. For the full picture run ``genkei watchlist health``,
    which folds these same rows in (the staleness alerter in
    ``.github/workflows/ingest-staleness-check.yml`` reads the JSON
    shape that health emits).
    """
    rows = _drift_rows(max_age_hours=max_age_hours)
    if json_out:
        # Match the health-JSON shape so consumers can pipe both paths.
        typer.echo(
            json.dumps(
                [{**r, "health_status": "DRIFT"} for r in rows],
                indent=2,
                default=_json_default,
            )
        )
        return
    if not rows:
        typer.echo("No schema drift detected across recent raw_blobs. ✓")
        return
    lines = [
        f"Schema drift across recent raw_blobs (max age: {max_age_hours}h)",
        "-" * 80,
        f"  {'source':<11} {'endpoint_kind':<26} {'kind':<22}  detail",
    ]
    for r in rows:
        kind = r["drift_kind"]
        ep_kind = r["endpoint_kind"]
        sample = r.get("sample_endpoint_name")
        detail = r["detail"]
        if sample:
            detail = f"({sample}) {detail}"
        lines.append(f"  {r['source']:<11} {ep_kind:<26} {kind:<22}  {detail[:120]}")
    typer.echo("\n".join(lines))


# ---------------------------------------------------------------------------
# `genkei watchlist gaps`
# ---------------------------------------------------------------------------


def _query_asset_gaps(wl: Watchlist) -> list[dict[str, Any]]:
    """Per asset: when was the last data point we have."""
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    with db.connection() as conn, conn.cursor() as cur:
        # Crypto → coingecko.market_data keyed by coingecko_id
        for c in wl.crypto:
            cur.execute(
                "SELECT max(ts) FROM coingecko.market_data WHERE coingecko_id = %s",
                [c.coingecko_id],
            )
            last_ts = cur.fetchone()[0]
            age_h = (now - last_ts).total_seconds() / 3600 if last_ts else None
            out.append(
                {
                    "sleeve": "crypto",
                    "asset": c.symbol,
                    "key": c.coingecko_id,
                    "source": "coingecko.market_data",
                    "last_ts": last_ts.isoformat() if last_ts else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                }
            )
        # Equity → sec.filings keyed by cik (skip entries without cik)
        for e in wl.equities:
            if e.cik is None:
                out.append(
                    {
                        "sleeve": "equity",
                        "asset": e.symbol,
                        "key": None,
                        "source": "sec.filings",
                        "last_ts": None,
                        "age_hours": None,
                        "note": "no CIK in watchlist",
                    }
                )
                continue
            cur.execute(
                "SELECT max(filed_at) FROM sec.filings WHERE cik = %s",
                [e.cik],
            )
            last_d = cur.fetchone()[0]
            if last_d is not None:
                last_dt = datetime.combine(last_d, datetime.min.time(), tzinfo=timezone.utc)
                age_h = (now - last_dt).total_seconds() / 3600
            else:
                last_dt = None
                age_h = None
            out.append(
                {
                    "sleeve": "equity",
                    "asset": e.symbol,
                    "key": e.cik,
                    "source": "sec.filings",
                    "last_ts": last_dt.isoformat() if last_dt else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                }
            )
        # Macro → fred.observations keyed by series_id
        for m in wl.macro:
            cur.execute(
                "SELECT max(ts) FROM fred.observations WHERE series_id = %s",
                [m.series_id],
            )
            last_ts = cur.fetchone()[0]
            age_h = (now - last_ts).total_seconds() / 3600 if last_ts else None
            out.append(
                {
                    "sleeve": "macro",
                    "asset": m.series_id,
                    "key": m.series_id,
                    "source": "fred.observations",
                    "last_ts": last_ts.isoformat() if last_ts else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                }
            )
    return out


def _format_gaps_human(rows: list[dict[str, Any]], *, threshold_hours: float) -> str:
    lines = [
        f"Per-asset freshness (GAP = no data within {threshold_hours}h, "
        "NONE = no data at all)"
    ]
    lines.append("-" * len(lines[-1]))
    lines.append(
        f"  {'sleeve':<8} {'asset':<10} {'source':<25} "
        f"{'last_ts':<25} {'age':>10}  status"
    )
    gap_count = none_count = 0
    for r in _with_gap_status(rows, threshold_hours=threshold_hours):
        if r["status"] == "NONE":
            none_count += 1
        elif r["status"] == "GAP":
            gap_count += 1
        last = r["last_ts"][:25] if r["last_ts"] else "-"
        age = f"{r['age_hours']}h" if r["age_hours"] is not None else "-"
        note = f"  {r.get('note', '')}" if r.get("note") else ""
        lines.append(
            f"  {r['sleeve']:<8} {r['asset']:<10} {r['source']:<25} "
            f"{last:<25} {age:>10}  {r['status']}{note}"
        )
    lines.append("")
    lines.append(
        f"Summary: {len(rows)} assets, {gap_count} GAP, {none_count} NONE"
    )
    return "\n".join(lines)


def _gap_status_tag(row: dict[str, Any], *, threshold_hours: float) -> str:
    if row["last_ts"] is None:
        return "NONE"
    if row["age_hours"] is not None and row["age_hours"] > threshold_hours:
        return "GAP"
    return "OK"


def _with_gap_status(
    rows: list[dict[str, Any]], *, threshold_hours: float
) -> list[dict[str, Any]]:
    return [
        {**row, "status": _gap_status_tag(row, threshold_hours=threshold_hours)}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# `genkei watchlist score` (B-065)
# ---------------------------------------------------------------------------


def _format_score_human(scores: list[Any], *, show_breakdown: bool) -> str:
    """Render AssetScores or persisted-row dicts as a sorted table."""
    if not scores:
        return (
            "No scores match. Either the rubric returned nothing or "
            "`meta.signals` is empty (run `genkei watchlist score --persist` first)."
        )
    sorted_scores = sorted(scores, key=lambda s: -_score_value(s))
    if show_breakdown:
        header = (
            f"  {'asset':<14} {'sleeve':<22} {'class':<8} {'score':>6}  breakdown"
        )
    else:
        header = f"  {'asset':<14} {'sleeve':<22} {'class':<8} {'score':>6}"
    lines = [header, "-" * len(header)]
    for s in sorted_scores:
        asset, sleeve, klass, score, components = _score_fields(s)
        if show_breakdown:
            breakdown = " ".join(
                f"{c['name'].replace('_', '-')}={c['score']:+d}"
                for c in _components_iter(components)
            )
            lines.append(
                f"  {asset:<14} {sleeve:<22} {klass:<8} {score:>+6}  {breakdown}"
            )
        else:
            lines.append(f"  {asset:<14} {sleeve:<22} {klass:<8} {score:>+6}")
    return "\n".join(lines)


def _score_value(score: Any) -> int:
    """Pull composite_score from either AssetScore or a meta.signals dict."""
    if hasattr(score, "composite_score"):
        return int(score.composite_score)
    return int(score["composite_score"])


def _score_fields(score: Any) -> tuple[str, str, str, int, Any]:
    """(asset, sleeve, asset_class, composite, components) — works for both shapes."""
    if hasattr(score, "composite_score"):
        return (
            score.asset,
            score.sleeve,
            score.asset_class,
            int(score.composite_score),
            score.components,
        )
    return (
        score["asset"],
        score["sleeve"],
        score["asset_class"],
        int(score["composite_score"]),
        score["components"],
    )


def _components_iter(components: Any) -> list[dict[str, Any]]:
    """Normalize components to list[{name, score, detail}] regardless of source."""
    if isinstance(components, list):
        return [
            c.to_dict() if hasattr(c, "to_dict") else dict(c)
            for c in components
        ]
    if isinstance(components, dict):
        # meta.signals.components JSONB — dict[name, {score, detail, ...}].
        normalized: list[dict[str, Any]] = []
        for name, component in components.items():
            row = dict(component) if isinstance(component, dict) else {"score": component}
            row.setdefault("name", str(name))
            normalized.append(row)
        return normalized
    return []


def _score_to_json_dict(score: Any) -> dict[str, Any]:
    asset, sleeve, klass, composite, components = _score_fields(score)
    return {
        "asset": asset,
        "sleeve": sleeve,
        "asset_class": klass,
        "composite_score": composite,
        "components": _components_iter(components),
    }


_SCORE_SLEEVES = {"equity-core", "crypto-core", "crypto-tactical"}


def _validate_score_sleeve(sleeve: Optional[str]) -> Optional[str]:
    if sleeve is None:
        return None
    if sleeve not in _SCORE_SLEEVES:
        allowed = ", ".join(sorted(_SCORE_SLEEVES))
        raise typer.BadParameter(f"--sleeve must be one of: {allowed}")
    return sleeve


@app.command("score")
def score_cmd(
    persist: Annotated[
        bool,
        typer.Option(
            "--persist",
            help=(
                "Write today's computed scores to meta.signals. Without "
                "this flag the scores are computed and rendered but not "
                "written — useful for dry-run inspection."
            ),
        ),
    ] = False,
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            help=(
                "Filter to a single asset by equity ticker (AAPL) or "
                "coingecko_id (bitcoin)."
            ),
        ),
    ] = None,
    sleeve: Annotated[
        Optional[str],
        typer.Option(
            "--sleeve",
            help=(
                "Filter to one sleeve: equity-core, crypto-core, "
                "crypto-tactical."
            ),
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option(
            "--since",
            help=(
                "Read history from meta.signals from this date (YYYY-MM-DD) "
                "rather than computing today's scores. Implies read-only."
            ),
        ),
    ] = None,
    no_breakdown: Annotated[
        bool,
        typer.Option(
            "--no-breakdown",
            help="Suppress the per-component breakdown column.",
        ),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Compute or read per-asset composite scores (B-065 rubric).

    Default mode computes today's scores in-memory and prints them
    (most positive at top). Pass --persist to also write to
    meta.signals. Pass --since to read previously-persisted history.
    """
    from genkei.experiments.watchlist_scoring import (
        compute_today,
        load_latest_scores,
        persist_scores,
    )

    sleeve = _validate_score_sleeve(sleeve)

    if since is not None:
        since_date = _parse_date(since, label="since")
        if persist:
            raise typer.BadParameter(
                "--since reads history; it cannot be combined with --persist."
            )
        rows = load_latest_scores(sleeve=sleeve, asset=ticker, since=since_date)
        if json_out:
            typer.echo(json.dumps(
                [_score_to_json_dict(r) for r in rows],
                indent=2,
                default=_json_default,
            ))
            return
        typer.echo(_format_score_human(rows, show_breakdown=not no_breakdown))
        return

    # Compute today's scores in-memory.
    wl = _load_or_exit(config)
    scores = compute_today(watchlist=wl)
    if ticker is not None:
        scores = [s for s in scores if s.asset == ticker]
    if sleeve is not None:
        scores = [s for s in scores if s.sleeve == sleeve]
    if persist:
        ingest_run_id = persist_scores(scores)
        if json_out:
            typer.echo(json.dumps(
                {
                    "ingest_run_id": ingest_run_id,
                    "rows_written": len(scores),
                    "scores": [_score_to_json_dict(s) for s in scores],
                },
                indent=2,
                default=_json_default,
            ))
            return
        typer.echo(f"Persisted {len(scores)} score(s) (ingest_run_id={ingest_run_id})")
        typer.echo("")
        typer.echo(_format_score_human(scores, show_breakdown=not no_breakdown))
        return
    if json_out:
        typer.echo(json.dumps(
            [_score_to_json_dict(s) for s in scores],
            indent=2,
            default=_json_default,
        ))
        return
    typer.echo(_format_score_human(scores, show_breakdown=not no_breakdown))


@app.command("gaps")
def gaps_cmd(
    threshold_hours: Annotated[
        float,
        typer.Option(
            "--threshold-hours",
            help=(
                "Per-asset last-data older than this is tagged GAP. "
                "Default 36h fits daily series; for monthly/quarterly FRED "
                "series pass --threshold-hours 720 (30d) or higher."
            ),
            min=1,
        ),
    ] = 36.0,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    config: Annotated[
        Path,
        typer.Option("--config", help="Watchlist path.", show_default=True),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Show per-asset freshness across all sleeves."""
    wl = _load_or_exit(config)
    rows = _query_asset_gaps(wl)
    if json_out:
        typer.echo(
            json.dumps(
                _with_gap_status(rows, threshold_hours=threshold_hours),
                indent=2,
                default=_json_default,
            )
        )
    else:
        typer.echo(_format_gaps_human(rows, threshold_hours=threshold_hours))
