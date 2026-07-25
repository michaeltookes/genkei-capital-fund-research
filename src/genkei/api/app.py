"""FastAPI read layer over the lake — endpoints (B-131).

Every handler is a thin presenter over the shared query modules the CLI
already uses; no SQL is duplicated here and nothing writes. The design
rationale (direct-DB over subprocess, engine-enforced read-only) lives in the
package docstring (:mod:`genkei.api`).

Endpoints
=========
* ``GET /health``             — service up + DB reachable (SELECT 1).
* ``GET /watchlist``          — the crypto/equity/macro/price watchlist.
* ``GET /prices/{ticker}``    — price series (coingecko / coinbase / yahoo).
* ``GET /signals``            — signal-event history from meta.signal_events.
* ``GET /digest/weekly``      — the latest weekly signal digest markdown.
* ``GET /research/decisions`` — the research decision-log frontmatter index.
* ``GET /lake/health``        — per-source ingest health + table liveness.

Read-only enforcement + row caps
================================
* The DB-issuing readers reused here (price readers, ``query_events``,
  ``_query_source_health``) issue plain parameterized ``SELECT``s through
  ``db.connection()`` — no write path is importable from this module.
* ``/health`` routes its ``SELECT 1`` through ``db.run_readonly`` (the shared
  READ ONLY + statement_timeout guard).
* List endpoints cap rows server-side: ``/signals`` and ``/prices`` clamp
  their ``limit`` to ``MAX_ROW_LIMIT`` and push it into the SQL ``LIMIT``;
  ``/watchlist``, ``/research/decisions`` are naturally bounded and return
  the full (small) set.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query

from genkei.api.pool import configure_pool
from genkei.api.serialize import GenkeiJSONResponse

# Default + hard ceiling for any endpoint that returns a row list. Mirrors the
# spirit of `genkei query`'s row cap (B-045): a client can ask for fewer, never
# more than the ceiling, so no endpoint can pull an unbounded result over the
# wire and pin a pool slot.
DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000

# Statement timeout applied to the /health probe (seconds). The reused CLI
# readers manage their own (short) queries; the probe is the one query this
# module issues directly, so it carries the shared guard explicitly.
HEALTH_TIMEOUT_SECONDS = 5

_DIGEST_DIR = Path("reports/signals")
_DECISIONS_DIR = Path("docs/research/decisions")
_DECISION_SKIP_FILES = {"_template.md", "README.md"}


def _clamp_limit(limit: int) -> int:
    """Clamp a requested row limit to ``[1, MAX_ROW_LIMIT]``."""
    if limit < 1:
        return 1
    return min(limit, MAX_ROW_LIMIT)


def _parse_iso_date(raw: Optional[str], *, field: str) -> Optional[date]:
    """Parse a YYYY-MM-DD query param, 400 on malformed input."""
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DD: {raw!r}") from exc


# ---------------------------------------------------------------------------
# Handlers — each reuses a shared query module; none writes.
# ---------------------------------------------------------------------------


def _health() -> dict[str, Any]:
    """Service liveness + DB reachability via a READ ONLY SELECT 1."""
    from genkei.common import db

    db_ok = True
    detail: Optional[str] = None
    try:
        db.run_readonly("SELECT 1", timeout_seconds=HEALTH_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — surface any DB failure as unhealthy
        db_ok = False
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
    payload: dict[str, Any] = {
        "service": "genkei-api",
        "status": "ok" if db_ok else "degraded",
        "database": "reachable" if db_ok else "unreachable",
    }
    if detail is not None:
        payload["detail"] = detail
    return payload


def _watchlist(sleeve: Optional[str]) -> dict[str, Any]:
    """The watchlist, optionally scoped to one sleeve. Reuses load_watchlist()."""
    from genkei.common.watchlist import load_watchlist

    wl = load_watchlist()
    payload: dict[str, Any] = {}
    if sleeve in (None, "crypto"):
        payload["crypto"] = [
            {"symbol": c.symbol, "name": c.name, "coingecko_id": c.coingecko_id, "tier": c.tier}
            for c in wl.crypto
        ]
    if sleeve in (None, "equity", "equities"):
        payload["equities"] = [
            {"symbol": e.symbol, "name": e.name, "cik": e.cik, "tier": e.tier} for e in wl.equities
        ]
    if sleeve in (None, "macro"):
        payload["macro"] = [{"series_id": m.series_id, "name": m.name} for m in wl.macro]
    if sleeve in (None, "prices"):
        payload["crypto_price_targets"] = [
            {
                "symbol": t.symbol,
                "name": t.name,
                "coingecko_id": t.coingecko_id,
                "role": t.role,
                "asset_class": t.asset_class,
            }
            for t in wl.crypto_price_targets
        ]
        payload["yahoo_price_targets"] = [
            {"symbol": t.symbol, "name": t.name, "role": t.role, "asset_class": t.asset_class}
            for t in wl.yahoo_price_targets
        ]
    return payload


_VALID_PRICE_SOURCES = ("coingecko", "coinbase", "yahoo")


def _prices(
    ticker: str,
    *,
    source: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> dict[str, Any]:
    """Price series for a watchlist ticker. Reuses the CLI price readers."""
    from genkei.cli import prices as prices_cli
    from genkei.common.watchlist import load_watchlist

    if source is not None and source not in _VALID_PRICE_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source must be one of {', '.join(_VALID_PRICE_SOURCES)}",
        )
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=400, detail="since must be on or before until")

    wl = load_watchlist()
    crypto = wl.find_crypto(ticker)
    crypto_price_target = wl.find_crypto_price_target(ticker)
    coingecko_target = crypto if crypto is not None else crypto_price_target
    equity = wl.find_equity(ticker)
    benchmark = wl.find_benchmark(ticker)
    price_target = wl.find_yahoo_price_target(ticker)
    yahoo_target = equity is not None or benchmark is not None or price_target is not None

    if coingecko_target is None and not yahoo_target:
        raise HTTPException(status_code=404, detail=f"ticker {ticker!r} not in watchlist")

    resolved_source = source
    if resolved_source is None:
        if coingecko_target is not None and yahoo_target:
            raise HTTPException(
                status_code=400,
                detail=f"ticker {ticker!r} is ambiguous; pass ?source=",
            )
        resolved_source = "yahoo" if yahoo_target else "coingecko"

    if resolved_source == "coingecko":
        if coingecko_target is None:
            raise HTTPException(status_code=400, detail=f"{ticker} is not CoinGecko-backed")
        rows = prices_cli._query_coingecko_market_data(
            coingecko_target.coingecko_id, since=since, until=until, limit=limit
        )
    elif resolved_source == "coinbase":
        if crypto is None or not crypto.coinbase_product:
            raise HTTPException(
                status_code=400,
                detail=f"{ticker} has no coinbase_product; use source=coingecko",
            )
        rows = prices_cli._query_coinbase_candles(
            crypto.coinbase_product, since=since, until=until, limit=limit
        )
    else:  # yahoo
        if not yahoo_target:
            raise HTTPException(status_code=400, detail=f"{ticker} is not Yahoo-backed")
        if equity is not None:
            symbol = equity.symbol
        elif benchmark is not None:
            symbol = benchmark.symbol
        else:
            assert price_target is not None
            symbol = price_target.symbol
        rows = prices_cli._query_yahoo_candles(symbol, since=since, until=until, limit=limit)

    return {"ticker": ticker.upper(), "source": resolved_source, "rows": rows}


def _signals(
    *,
    asset: Optional[str],
    source: Optional[str],
    signal_kind: Optional[str],
    direction: Optional[str],
    since: Optional[date],
    until: Optional[date],
    limit: int,
) -> list[dict[str, Any]]:
    """Signal-event history from meta.signal_events. Reuses query_events()."""
    from datetime import time, timezone

    from genkei.experiments.signal_store import query_events

    since_dt = (
        datetime.combine(since, time(0, 0, tzinfo=timezone.utc)) if since is not None else None
    )
    until_dt = (
        datetime.combine(until, time(23, 59, 59, 999999, tzinfo=timezone.utc))
        if until is not None
        else None
    )
    events = query_events(
        asset=asset,
        source=source,
        signal_kind=signal_kind,
        direction=direction,
        since=since_dt,
        until=until_dt,
        limit=limit,
    )
    return [
        {
            "event_id": ev.event_id,
            "asset": ev.asset,
            "asset_class": ev.asset_class,
            "horizon_tag": ev.horizon,
            "ts": ev.ts.isoformat(),
            "source": ev.source,
            "signal_kind": ev.signal_kind,
            "direction": ev.direction,
            "strength": ev.strength,
            "payload": ev.payload,
            "source_ref": ev.source_ref,
        }
        for ev in events
    ]


def _latest_digest() -> dict[str, Any]:
    """Return the newest weekly signal digest markdown from reports/signals/."""
    if not _DIGEST_DIR.exists():
        raise HTTPException(status_code=404, detail="no weekly digest directory")
    files = sorted(_DIGEST_DIR.glob("weekly-*.md"))
    if not files:
        raise HTTPException(status_code=404, detail="no weekly digest generated yet")
    latest = files[-1]
    return {
        "filename": latest.name,
        "markdown": latest.read_text(encoding="utf-8"),
    }


def _research_decisions() -> list[dict[str, Any]]:
    """Frontmatter index of docs/research/decisions/*.md.

    Reuses the frontmatter-parse shape pinned by tests/test_research_decisions.py
    (leading `---` fence, PyYAML safe_load). Serves the metadata, not the full
    body, so the cockpit can list/filter decisions cheaply.
    """
    import yaml

    if not _DECISIONS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(_DECISIONS_DIR.glob("*.md")):
        if path.name in _DECISION_SKIP_FILES:
            continue
        fm = _parse_decision_frontmatter(path, yaml)
        if fm is None:
            continue
        out.append({"file": path.name, **fm})
    return out


def _parse_decision_frontmatter(path: Path, yaml_module: Any) -> Optional[dict[str, Any]]:
    """Parse the leading `---` YAML frontmatter block; None if absent/malformed."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    rest = text[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        return None
    try:
        parsed = yaml_module.safe_load(rest[:end])
    except yaml_module.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    # date-typed values (PyYAML parses YYYY-MM-DD to datetime.date) serialize
    # via the shared json_default's isoformat branch.
    return parsed


def _lake_health(stale_hours: float) -> list[dict[str, Any]]:
    """Per-source ingest health + primary-table liveness. Reuses watchlist CLI."""
    from genkei.cli.watchlist import _query_source_health, _with_health_status

    return _with_health_status(_query_source_health(), stale_hours=stale_hours)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build the FastAPI app with all read endpoints mounted.

    ``configure_pool`` runs in the startup lifespan so the shared pool is
    capped before the first request opens a connection — unless a test has
    already injected its own pool, in which case get_pool leaves it be.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_pool()
        yield

    app = FastAPI(
        title="Genkei read API",
        description="Read-only HTTP layer over the Genkei data lake (B-131).",
        version="0.1.0",
        default_response_class=GenkeiJSONResponse,
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return _health()

    @app.get("/watchlist")
    def watchlist(
        sleeve: Optional[str] = Query(
            default=None,
            description="Filter to one sleeve: crypto | equity | macro | prices.",
        ),
    ) -> dict[str, Any]:
        if sleeve is not None and sleeve not in {
            "crypto",
            "equity",
            "equities",
            "macro",
            "prices",
        }:
            raise HTTPException(
                status_code=400, detail="sleeve must be crypto, equity, macro, or prices"
            )
        return _watchlist(sleeve)

    @app.get("/prices/{ticker}")
    def prices(
        ticker: str,
        source: Optional[str] = Query(default=None),
        since: Optional[str] = Query(default=None, description="Start date YYYY-MM-DD."),
        until: Optional[str] = Query(default=None, description="End date YYYY-MM-DD."),
        limit: int = Query(default=DEFAULT_ROW_LIMIT, ge=1),
    ) -> dict[str, Any]:
        return _prices(
            ticker,
            source=source,
            since=_parse_iso_date(since, field="since"),
            until=_parse_iso_date(until, field="until"),
            limit=_clamp_limit(limit),
        )

    @app.get("/signals")
    def signals(
        asset: Optional[str] = Query(default=None),
        source: Optional[str] = Query(default=None),
        signal_kind: Optional[str] = Query(default=None),
        direction: Optional[str] = Query(default=None),
        since: Optional[str] = Query(default=None, description="Earliest event date YYYY-MM-DD."),
        until: Optional[str] = Query(default=None, description="Latest event date YYYY-MM-DD."),
        limit: int = Query(default=DEFAULT_ROW_LIMIT, ge=1),
    ) -> list[dict[str, Any]]:
        if direction is not None and direction not in {"bullish", "bearish", "neutral"}:
            raise HTTPException(
                status_code=400, detail="direction must be bullish, bearish, or neutral"
            )
        return _signals(
            asset=asset,
            source=source,
            signal_kind=signal_kind,
            direction=direction,
            since=_parse_iso_date(since, field="since"),
            until=_parse_iso_date(until, field="until"),
            limit=_clamp_limit(limit),
        )

    @app.get("/digest/weekly")
    def digest_weekly() -> dict[str, Any]:
        return _latest_digest()

    @app.get("/research/decisions")
    def research_decisions() -> list[dict[str, Any]]:
        return _research_decisions()

    @app.get("/lake/health")
    def lake_health(
        stale_hours: float = Query(default=36.0, ge=1),
    ) -> list[dict[str, Any]]:
        return _lake_health(stale_hours)

    return app
