"""Watchlist scoring rubric (B-065).

Composite per-asset daily score that synthesizes the existing
single-source signals into one number. Every future ``/research``
session reads the score at the top instead of re-running the
multi-signal synthesis by hand.

Two role-separated halves of the module, mirroring B-062 / B-090:

  * **Pure functions** (``score_equity``, ``score_crypto``,
    ``score_macro_regime``) — operate on plain dataclass inputs. No
    DB. Unit-testable on synthetic signals.
  * **Lake-loading helpers** (``load_*_inputs`` + ``compute_today``) —
    pull the inputs from Postgres and feed the pure functions.

The rubric is **additive and interpretable**: each component
contributes a signed integer (typically -2..+2), the components sum
into a composite score in roughly the -8..+8 range. Each component
carries a ``detail`` string so the breakdown is queryable: "AAPL +3
(insider cluster), -1 (revenue YoY -2%), +1 (risk-on macro)".

Per-sleeve interpretation (CLAUDE.md):

  * **Equity-core** (Buffett-style, no selling) — score reads as
    "should I add to position?" Higher is better; negative scores
    are "wait/observe."
  * **Crypto-core** (BTC/ETH/SOL/LINK) — same; multi-year hold,
    score informs add timing.
  * **Crypto-tactical** (SUI, PYTH, RENDER) — turnover-eligible;
    score reads symmetrically. Negative = trim-candidate;
    positive = add-candidate.

Rubric components (RUBRIC_VERSION = "v1"):

  * **Equity asset_class:**
    - ``insider_flow`` (-3..+3): Form 4 cluster detection over 30d.
      +3 for buy cluster ≥3 reporters; +1 for any open-market P buy
      in 14d; -2 for sell cluster ≥3; 0 otherwise.
    - ``revenue_trend`` (-2..+2): XBRL RevenueFromContractWithCustomer
      YoY change from latest 10-Q. +2 if >+15%; -2 if <0%; +1 / -1
      for moderate growth/decline.
    - ``filings_velocity`` (-2..+1): 8-K filings in last 30d.
      +1 for normal activity; -2 if >=5 8-Ks (often signals trouble
      — restatements, executive changes); 0 otherwise.
    - ``macro_regime`` (-1..+1): shared across all assets in a run.
  * **Crypto asset_class:**
    - ``relative_strength`` (-2..+2): analytics.crypto_relative_strength
      vs BTC at 30d.
    - ``tvl_trend`` (-2..+2): chain or protocol TVL 30d change.
      Falls back to None when the asset isn't a chain we cover.
    - ``volume_momentum`` (-1..+1): coingecko 7d volume vs 30d MA.
    - ``macro_regime`` (-1..+1): shared.

Macro regime is shared across all assets — computed once per
scoring run from the FRED watchlist:
  * DGS10 trend (rate spike = bearish)
  * BAMLH0A0HYM2 (HY credit spread; tight = risk-on)
  * VIXCLS (vol = risk-off)
  * DTWEXBGS (USD strength = crypto-bearish)

The rubric is intentionally additive (not multiplicative or
ML-derived) so the breakdown stays interpretable. Once we have
~6 months of persisted scores, B-066 (regime classifier
integration) and B-064 (cross-source correlation engine) can
*replace* this with more sophisticated synthesis — but the
rubric_version PK in meta.signals means the v1 history stays
queryable for comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from genkei.common import db
from genkei.common.watchlist import Watchlist, load_watchlist

RUBRIC_VERSION = "v1"


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentScore:
    """One component's contribution to a composite score."""

    name: str
    score: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "score": self.score, "detail": self.detail}


@dataclass(frozen=True)
class AssetScore:
    """One asset's composite score with per-component breakdown."""

    asset: str
    asset_class: str  # "equity" | "crypto"
    sleeve: str
    composite_score: int
    components: list[ComponentScore]
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_meta_signal_row(self, *, ingest_run_id: int) -> dict[str, Any]:
        """Shape one row for ``meta.signals`` insertion."""
        return {
            "asset": self.asset,
            "ts": self.ts,
            "rubric_version": RUBRIC_VERSION,
            "asset_class": self.asset_class,
            "sleeve": self.sleeve,
            "composite_score": self.composite_score,
            "components": {c.name: c.to_dict() for c in self.components},
            "ingest_run_id": ingest_run_id,
        }


# ---------------------------------------------------------------------------
# Pure scoring — equity
# ---------------------------------------------------------------------------


def score_insider_flow(
    *,
    buy_cluster_reporters_30d: int,
    sell_cluster_reporters_30d: int,
    any_open_market_buy_14d: bool,
) -> ComponentScore:
    """Equity rubric — Form 4 insider activity signal.

    +3 buy cluster (≥3 reporters); +1 any open-market buy in 14d;
    -2 sell cluster (≥3 reporters); 0 otherwise. Buy cluster overrides
    sell cluster when both happen in the same window (rare but possible).
    """
    if buy_cluster_reporters_30d >= 3:
        return ComponentScore(
            name="insider_flow",
            score=3,
            detail=f"buy cluster: {buy_cluster_reporters_30d} reporters in 30d",
        )
    if sell_cluster_reporters_30d >= 3:
        return ComponentScore(
            name="insider_flow",
            score=-2,
            detail=f"sell cluster: {sell_cluster_reporters_30d} reporters in 30d",
        )
    if any_open_market_buy_14d:
        return ComponentScore(
            name="insider_flow",
            score=1,
            detail="open-market buy in 14d",
        )
    return ComponentScore(name="insider_flow", score=0, detail="no signal")


def score_revenue_trend(*, revenue_yoy_pct: Decimal | None) -> ComponentScore:
    """Equity rubric — XBRL revenue YoY change.

    +2 >15%; +1 5-15%; -1 -10 to 0%; -2 <-10%; 0 otherwise.
    """
    if revenue_yoy_pct is None:
        return ComponentScore(
            name="revenue_trend", score=0, detail="no recent XBRL revenue"
        )
    yoy = float(revenue_yoy_pct)
    if yoy > 15:
        return ComponentScore(name="revenue_trend", score=2, detail=f"YoY +{yoy:.1f}%")
    if yoy > 5:
        return ComponentScore(name="revenue_trend", score=1, detail=f"YoY +{yoy:.1f}%")
    if yoy < -10:
        return ComponentScore(name="revenue_trend", score=-2, detail=f"YoY {yoy:.1f}%")
    if yoy < 0:
        return ComponentScore(name="revenue_trend", score=-1, detail=f"YoY {yoy:.1f}%")
    return ComponentScore(name="revenue_trend", score=0, detail=f"YoY {yoy:.1f}% (flat)")


def score_filings_velocity(*, eight_k_count_30d: int) -> ComponentScore:
    """Equity rubric — recent 8-K activity.

    +1 1-2 8-Ks (normal); -2 ≥5 8-Ks (often signals restatements,
    executive changes, material events); 0 otherwise.
    """
    if eight_k_count_30d >= 5:
        return ComponentScore(
            name="filings_velocity",
            score=-2,
            detail=f"{eight_k_count_30d} 8-Ks in 30d (high — investigate)",
        )
    if 1 <= eight_k_count_30d <= 2:
        return ComponentScore(
            name="filings_velocity",
            score=1,
            detail=f"{eight_k_count_30d} 8-K(s) in 30d (normal cadence)",
        )
    return ComponentScore(
        name="filings_velocity",
        score=0,
        detail=f"{eight_k_count_30d} 8-Ks in 30d",
    )


# ---------------------------------------------------------------------------
# Pure scoring — crypto
# ---------------------------------------------------------------------------


def score_relative_strength(*, rel_strength_pct: Decimal | None) -> ComponentScore:
    """Crypto rubric — analytics.crypto_relative_strength vs BTC @ 30d.

    +2 outperforming BTC by 10+pp; +1 by 3-10pp; -1 underperforming by
    3-10pp; -2 by 10+pp; 0 within ±3pp.
    """
    if rel_strength_pct is None:
        return ComponentScore(
            name="relative_strength", score=0, detail="no 30d rel-strength data"
        )
    rs = float(rel_strength_pct)
    if rs >= 10:
        return ComponentScore(name="relative_strength", score=2, detail=f"+{rs:.1f}pp vs BTC")
    if rs >= 3:
        return ComponentScore(name="relative_strength", score=1, detail=f"+{rs:.1f}pp vs BTC")
    if rs <= -10:
        return ComponentScore(name="relative_strength", score=-2, detail=f"{rs:.1f}pp vs BTC")
    if rs <= -3:
        return ComponentScore(name="relative_strength", score=-1, detail=f"{rs:.1f}pp vs BTC")
    return ComponentScore(name="relative_strength", score=0, detail=f"{rs:+.1f}pp vs BTC (flat)")


def score_tvl_trend(*, tvl_change_30d_pct: Decimal | None) -> ComponentScore:
    """Crypto rubric — chain or protocol TVL change over 30d.

    +2 >+15%; +1 +5-15%; -1 -15 to -5%; -2 <-15%; 0 otherwise.
    """
    if tvl_change_30d_pct is None:
        return ComponentScore(
            name="tvl_trend", score=0, detail="no chain/protocol TVL coverage"
        )
    pct = float(tvl_change_30d_pct)
    if pct > 15:
        return ComponentScore(name="tvl_trend", score=2, detail=f"TVL +{pct:.1f}% (30d)")
    if pct > 5:
        return ComponentScore(name="tvl_trend", score=1, detail=f"TVL +{pct:.1f}% (30d)")
    if pct < -15:
        return ComponentScore(name="tvl_trend", score=-2, detail=f"TVL {pct:.1f}% (30d)")
    if pct < -5:
        return ComponentScore(name="tvl_trend", score=-1, detail=f"TVL {pct:.1f}% (30d)")
    return ComponentScore(name="tvl_trend", score=0, detail=f"TVL {pct:+.1f}% (30d, flat)")


def score_volume_momentum(
    *,
    volume_7d_avg: Decimal | None,
    volume_30d_avg: Decimal | None,
) -> ComponentScore:
    """Crypto rubric — 7d vs 30d average volume ratio.

    +1 if 7d > 130% of 30d (volume surge — interest building);
    -1 if 7d < 70% of 30d (volume fade — interest waning); 0 otherwise.
    """
    if (
        volume_7d_avg is None
        or volume_30d_avg is None
        or volume_30d_avg == 0
    ):
        return ComponentScore(
            name="volume_momentum", score=0, detail="insufficient volume data"
        )
    ratio = float(volume_7d_avg) / float(volume_30d_avg)
    pct = (ratio - 1) * 100
    if ratio > 1.3:
        return ComponentScore(
            name="volume_momentum", score=1, detail=f"7d vol +{pct:.1f}% vs 30d (surge)"
        )
    if ratio < 0.7:
        return ComponentScore(
            name="volume_momentum", score=-1, detail=f"7d vol {pct:.1f}% vs 30d (fade)"
        )
    return ComponentScore(
        name="volume_momentum", score=0, detail=f"7d vol {pct:+.1f}% vs 30d"
    )


# ---------------------------------------------------------------------------
# Pure scoring — macro (shared across all assets in a run)
# ---------------------------------------------------------------------------


def score_macro_regime(
    *,
    dgs10_pct: Decimal | None,
    dgs10_pct_30d_ago: Decimal | None,
    hy_oas_pct: Decimal | None,
    vix: Decimal | None,
    usd_index: Decimal | None,
    usd_index_30d_ago: Decimal | None,
) -> ComponentScore:
    """Shared macro regime score (-1..+1) applied to every asset in a run.

    Counts directional inputs:
      * DGS10 rising 30d  → -1 (rate shock; equity headwind)
      * HY OAS < 3.5%     → +1 (credit risk-on)
      * VIX < 18          → +1 (vol benign)
      * USD weakening 30d → +1 (crypto tailwind, EM tailwind)

    Net signal: sum / 4, rounded to {-1, 0, +1}. Interpretable as
    "risk-on / mixed / risk-off." Every asset's macro_regime
    component shares this value within a single scoring run.
    """
    contributions: list[tuple[int, str]] = []
    if dgs10_pct is not None and dgs10_pct_30d_ago is not None:
        delta = float(dgs10_pct) - float(dgs10_pct_30d_ago)
        if delta > 0.30:
            contributions.append((-1, f"DGS10 +{delta:.2f}pp 30d (rate spike)"))
        elif delta < -0.30:
            contributions.append((+1, f"DGS10 {delta:.2f}pp 30d (rate ease)"))
    if hy_oas_pct is not None:
        if float(hy_oas_pct) < 3.5:
            contributions.append((+1, f"HY OAS {float(hy_oas_pct):.2f}% (tight, risk-on)"))
        elif float(hy_oas_pct) > 5.0:
            contributions.append((-1, f"HY OAS {float(hy_oas_pct):.2f}% (wide, risk-off)"))
    if vix is not None:
        if float(vix) < 18:
            contributions.append((+1, f"VIX {float(vix):.1f} (benign)"))
        elif float(vix) > 25:
            contributions.append((-1, f"VIX {float(vix):.1f} (elevated)"))
    if usd_index is not None and usd_index_30d_ago is not None:
        delta = float(usd_index) - float(usd_index_30d_ago)
        if delta > 1.0:
            contributions.append((-1, f"DXY +{delta:.2f} 30d (USD strength)"))
        elif delta < -1.0:
            contributions.append((+1, f"DXY {delta:.2f} 30d (USD weakness)"))

    if not contributions:
        return ComponentScore(
            name="macro_regime",
            score=0,
            detail="no recent macro data (FRED stale or unset)",
        )
    total = sum(c[0] for c in contributions)
    details = " | ".join(c[1] for c in contributions)
    if total >= 2:
        return ComponentScore(name="macro_regime", score=1, detail=f"risk-on: {details}")
    if total <= -2:
        return ComponentScore(name="macro_regime", score=-1, detail=f"risk-off: {details}")
    return ComponentScore(name="macro_regime", score=0, detail=f"mixed: {details}")


# ---------------------------------------------------------------------------
# Composite assembly
# ---------------------------------------------------------------------------


def compose_equity_score(
    *,
    ticker: str,
    sleeve: str,
    insider: ComponentScore,
    revenue: ComponentScore,
    filings: ComponentScore,
    macro: ComponentScore,
    ts: datetime | None = None,
) -> AssetScore:
    components = [insider, revenue, filings, macro]
    return AssetScore(
        asset=ticker,
        asset_class="equity",
        sleeve=sleeve,
        composite_score=sum(c.score for c in components),
        components=components,
        ts=ts or datetime.now(timezone.utc),
    )


def compose_crypto_score(
    *,
    coingecko_id: str,
    sleeve: str,
    rel_strength: ComponentScore,
    tvl: ComponentScore,
    volume: ComponentScore,
    macro: ComponentScore,
    ts: datetime | None = None,
) -> AssetScore:
    components = [rel_strength, tvl, volume, macro]
    return AssetScore(
        asset=coingecko_id,
        asset_class="crypto",
        sleeve=sleeve,
        composite_score=sum(c.score for c in components),
        components=components,
        ts=ts or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Lake loaders
# ---------------------------------------------------------------------------


def _load_macro_inputs(ts: datetime) -> dict[str, Decimal | None]:
    """Pull the four FRED series this rubric reads, at ts and ts-30d.

    Returns a dict suitable for ``score_macro_regime(**inputs)``. Missing
    series resolve to None — ``score_macro_regime`` handles that.
    """
    cutoff = ts - timedelta(days=45)  # window wide enough to find a 30d anchor
    series_ids = ("DGS10", "BAMLH0A0HYM2", "VIXCLS", "DTWEXBGS")
    out: dict[str, Decimal | None] = {
        "dgs10_pct": None,
        "dgs10_pct_30d_ago": None,
        "hy_oas_pct": None,
        "vix": None,
        "usd_index": None,
        "usd_index_30d_ago": None,
    }
    field_map = {
        "DGS10": ("dgs10_pct", "dgs10_pct_30d_ago"),
        "BAMLH0A0HYM2": ("hy_oas_pct", None),
        "VIXCLS": ("vix", None),
        "DTWEXBGS": ("usd_index", "usd_index_30d_ago"),
    }
    with db.connection() as conn, conn.cursor() as cur:
        for sid in series_ids:
            cur.execute(
                # Latest-vintage per ts; same shape as `genkei macro` default.
                "SELECT DISTINCT ON (ts) ts, value FROM fred.observations "
                "WHERE series_id = %s AND ts >= %s "
                "ORDER BY ts DESC, realtime_start DESC LIMIT 2",
                [sid, cutoff],
            )
            rows = cur.fetchall()
            now_field, then_field = field_map[sid]
            if rows:
                out[now_field] = rows[0][1]
            if then_field and len(rows) >= 2:
                # rows[0] is most recent; pick the most recent ≤ ts-30d.
                target = ts - timedelta(days=30)
                for ts_row, value in rows:
                    if ts_row <= target:
                        out[then_field] = value
                        break
                else:
                    # No row 30d back within the window; use whatever's oldest.
                    out[then_field] = rows[-1][1]
    return out


def _load_equity_signals(ticker: str, cik: str | None, ts: datetime) -> dict[str, Any]:
    """Pull insider + revenue + filings inputs for one equity asset."""
    inputs: dict[str, Any] = {
        "buy_cluster_reporters_30d": 0,
        "sell_cluster_reporters_30d": 0,
        "any_open_market_buy_14d": False,
        "revenue_yoy_pct": None,
        "eight_k_count_30d": 0,
    }
    if cik is None:
        return inputs

    thirty_d_ago = (ts - timedelta(days=30)).date()
    fourteen_d_ago = (ts - timedelta(days=14)).date()

    with db.connection() as conn, conn.cursor() as cur:
        # Insider flow — count distinct reporters per direction in 30d.
        cur.execute(
            "SELECT count(DISTINCT reporter_cik) FROM sec.form4_transactions "
            "WHERE issuer_cik = %s "
            "  AND transaction_date >= %s "
            "  AND transaction_code = 'P' "
            "  AND acquired_disposed = 'A' "
            "  AND is_derivative = false",
            [cik, thirty_d_ago],
        )
        inputs["buy_cluster_reporters_30d"] = cur.fetchone()[0] or 0
        cur.execute(
            "SELECT count(DISTINCT reporter_cik) FROM sec.form4_transactions "
            "WHERE issuer_cik = %s "
            "  AND transaction_date >= %s "
            "  AND transaction_code = 'S' "
            "  AND acquired_disposed = 'D' "
            "  AND is_derivative = false",
            [cik, thirty_d_ago],
        )
        inputs["sell_cluster_reporters_30d"] = cur.fetchone()[0] or 0
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM sec.form4_transactions "
            "WHERE issuer_cik = %s "
            "  AND transaction_date >= %s "
            "  AND transaction_code = 'P' "
            "  AND acquired_disposed = 'A' "
            "  AND is_derivative = false)",
            [cik, fourteen_d_ago],
        )
        inputs["any_open_market_buy_14d"] = bool(cur.fetchone()[0])

        # Revenue YoY — match the two most recent quarterly values for
        # the canonical RevenueFromContractWithCustomer concept; YoY
        # means latest period_end vs same period 1y ago.
        cur.execute(
            """
            WITH recent AS (
                SELECT period_end, value
                FROM sec.facts
                WHERE cik = %s
                  AND concept = 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax'
                  AND form_type IN ('10-Q', '10-K')
                  AND unit = 'USD'
                ORDER BY period_end DESC
                LIMIT 8
            )
            SELECT period_end, value FROM recent ORDER BY period_end DESC
            """,
            [cik],
        )
        revenue_rows = cur.fetchall()
        if len(revenue_rows) >= 2:
            latest_end, latest_val = revenue_rows[0]
            target_year_ago = latest_end - timedelta(days=365)
            year_ago_val = None
            for r_end, r_val in revenue_rows[1:]:
                # Pick the period_end closest to ~365 days before latest.
                if abs((r_end - target_year_ago).days) <= 60:
                    year_ago_val = r_val
                    break
            if year_ago_val is not None and year_ago_val != 0:
                inputs["revenue_yoy_pct"] = Decimal(
                    str(((latest_val - year_ago_val) / year_ago_val) * 100)
                )

        # 8-K filings in 30d.
        cur.execute(
            "SELECT count(*) FROM sec.filings "
            "WHERE cik = %s AND form_type = '8-K' AND filed_at >= %s",
            [cik, thirty_d_ago],
        )
        inputs["eight_k_count_30d"] = cur.fetchone()[0] or 0
    return inputs


def _load_crypto_signals(coingecko_id: str, ts: datetime) -> dict[str, Any]:
    """Pull rel-strength + TVL + volume inputs for one crypto asset."""
    inputs: dict[str, Any] = {
        "rel_strength_pct": None,
        "tvl_change_30d_pct": None,
        "volume_7d_avg": None,
        "volume_30d_avg": None,
    }
    with db.connection() as conn, conn.cursor() as cur:
        # Relative-strength vs BTC at 30d — read directly from the view.
        cur.execute(
            "SELECT relative_strength_pct FROM analytics.crypto_relative_strength "
            "WHERE asset = %s AND peer = 'bitcoin' AND window_days = 30",
            [coingecko_id],
        )
        row = cur.fetchone()
        if row is not None:
            inputs["rel_strength_pct"] = row[0]

        # TVL trend — try chain_tvl first (for L1 tokens whose
        # coingecko_id matches a defillama chain name lowercased), then
        # fall back to protocol_tvl (for protocol tokens).
        coingecko_to_chain = {
            "bitcoin": "Bitcoin",
            "ethereum": "Ethereum",
            "solana": "Solana",
            "sui": "Sui",
        }
        chain_name = coingecko_to_chain.get(coingecko_id)
        if chain_name is not None:
            cur.execute(
                """
                WITH recent AS (
                    SELECT ts, tvl_usd FROM defillama.chain_tvl
                    WHERE chain = %s AND ts >= %s
                    ORDER BY ts DESC LIMIT 35
                )
                SELECT
                    (SELECT tvl_usd FROM recent ORDER BY ts DESC LIMIT 1) AS latest,
                    (SELECT tvl_usd FROM recent ORDER BY ts ASC LIMIT 1) AS earliest
                """,
                [chain_name, ts - timedelta(days=40)],
            )
            row = cur.fetchone()
            if row and row[0] and row[1] and row[1] != 0:
                latest, earliest = row
                inputs["tvl_change_30d_pct"] = Decimal(
                    str(((latest - earliest) / earliest) * 100)
                )

        # Volume momentum — coingecko.market_data daily volume.
        # CTE named `recent_volume` because `window` is a reserved
        # PostgreSQL keyword (caught during live smoke).
        cur.execute(
            """
            WITH recent_volume AS (
                SELECT ts, volume_usd FROM coingecko.market_data
                WHERE coingecko_id = %s AND ts >= %s AND volume_usd IS NOT NULL
                ORDER BY ts DESC
            ),
            top_7 AS (SELECT volume_usd FROM recent_volume LIMIT 7),
            top_30 AS (SELECT volume_usd FROM recent_volume LIMIT 30)
            SELECT
                (SELECT AVG(volume_usd) FROM top_7) AS avg_7d,
                (SELECT AVG(volume_usd) FROM top_30) AS avg_30d
            """,
            [coingecko_id, ts - timedelta(days=35)],
        )
        row = cur.fetchone()
        if row:
            inputs["volume_7d_avg"], inputs["volume_30d_avg"] = row
    return inputs


def compute_today(
    *,
    watchlist: Watchlist | None = None,
    ts: datetime | None = None,
) -> list[AssetScore]:
    """Compute today's scores for the full watchlist (no DB writes).

    Caller decides whether to persist via ``persist_scores``.
    """
    if watchlist is None:
        watchlist = load_watchlist()
    now = ts or datetime.now(timezone.utc)

    macro_inputs = _load_macro_inputs(now)
    shared_macro = score_macro_regime(**macro_inputs)

    scores: list[AssetScore] = []

    for equity in watchlist.equities:
        signals = _load_equity_signals(equity.symbol, equity.cik, now)
        scores.append(
            compose_equity_score(
                ticker=equity.symbol,
                sleeve=f"equity-{equity.sleeve}",
                insider=score_insider_flow(
                    buy_cluster_reporters_30d=signals["buy_cluster_reporters_30d"],
                    sell_cluster_reporters_30d=signals["sell_cluster_reporters_30d"],
                    any_open_market_buy_14d=signals["any_open_market_buy_14d"],
                ),
                revenue=score_revenue_trend(
                    revenue_yoy_pct=signals["revenue_yoy_pct"]
                ),
                filings=score_filings_velocity(
                    eight_k_count_30d=signals["eight_k_count_30d"]
                ),
                macro=shared_macro,
                ts=now,
            )
        )

    for crypto in watchlist.crypto:
        if not crypto.coingecko_id:
            continue
        signals = _load_crypto_signals(crypto.coingecko_id, now)
        sleeve_label = f"crypto-{crypto.sleeve or 'core'}"
        scores.append(
            compose_crypto_score(
                coingecko_id=crypto.coingecko_id,
                sleeve=sleeve_label,
                rel_strength=score_relative_strength(
                    rel_strength_pct=signals["rel_strength_pct"]
                ),
                tvl=score_tvl_trend(
                    tvl_change_30d_pct=signals["tvl_change_30d_pct"]
                ),
                volume=score_volume_momentum(
                    volume_7d_avg=signals["volume_7d_avg"],
                    volume_30d_avg=signals["volume_30d_avg"],
                ),
                macro=shared_macro,
                ts=now,
            )
        )

    return scores


def persist_scores(scores: list[AssetScore]) -> int:
    """Insert scored rows into ``meta.signals`` under an ingest_run.

    Returns the ingest_run id. Idempotent on (asset, ts, rubric_version)
    via ON CONFLICT DO UPDATE — re-running the same day refreshes the
    breakdown rather than failing.
    """
    if not scores:
        return -1

    with db.ingest_run(
        "watchlist_scoring",
        endpoint="score",
        metadata={
            "rubric_version": RUBRIC_VERSION,
            "asset_count": len(scores),
        },
    ) as run:
        rows = [s.to_meta_signal_row(ingest_run_id=run.id) for s in scores]
        # `db.bulk_upsert` doesn't auto-serialize dict→JSONB (G-028), so
        # we route the `components` field through json.dumps via a
        # local helper instead of the generic upsert.
        _bulk_upsert_signals(rows)
        run.add_rows(len(rows))
    return run.id


def _bulk_upsert_signals(rows: list[dict[str, Any]]) -> None:
    """Insert rows into meta.signals with JSONB serialization for components."""
    import json
    with db.connection() as conn, conn.cursor() as cur:
        sql_text = (
            "INSERT INTO meta.signals "
            "(asset, ts, rubric_version, asset_class, sleeve, "
            " composite_score, components, ingest_run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) "
            "ON CONFLICT (asset, ts, rubric_version) DO UPDATE SET "
            "  asset_class = EXCLUDED.asset_class, "
            "  sleeve = EXCLUDED.sleeve, "
            "  composite_score = EXCLUDED.composite_score, "
            "  components = EXCLUDED.components, "
            "  ingest_run_id = EXCLUDED.ingest_run_id"
        )
        cur.executemany(
            sql_text,
            [
                [
                    r["asset"],
                    r["ts"],
                    r["rubric_version"],
                    r["asset_class"],
                    r["sleeve"],
                    r["composite_score"],
                    json.dumps(r["components"]),
                    r["ingest_run_id"],
                ]
                for r in rows
            ],
        )


def load_latest_scores(
    *,
    rubric_version: str = RUBRIC_VERSION,
    sleeve: str | None = None,
    asset: str | None = None,
    since: date | None = None,
) -> list[dict[str, Any]]:
    """Read persisted scores back from ``meta.signals``.

    Without filters, returns the latest score per asset for the given
    rubric_version. With ``since``, returns the daily history for the
    matching subset.
    """
    if since is None:
        sql_text = (
            "SELECT DISTINCT ON (asset) "
            "  asset, ts, rubric_version, asset_class, sleeve, "
            "  composite_score, components "
            "FROM meta.signals "
            "WHERE rubric_version = %s"
        )
        params: list[Any] = [rubric_version]
        if sleeve is not None:
            sql_text += " AND sleeve = %s"
            params.append(sleeve)
        if asset is not None:
            sql_text += " AND asset = %s"
            params.append(asset)
        sql_text += " ORDER BY asset, ts DESC"
    else:
        sql_text = (
            "SELECT asset, ts, rubric_version, asset_class, sleeve, "
            "  composite_score, components "
            "FROM meta.signals "
            "WHERE rubric_version = %s AND ts >= %s"
        )
        params = [rubric_version, since]
        if sleeve is not None:
            sql_text += " AND sleeve = %s"
            params.append(sleeve)
        if asset is not None:
            sql_text += " AND asset = %s"
            params.append(asset)
        sql_text += " ORDER BY asset, ts DESC"

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql_text, params)
        rows = cur.fetchall()
    return [
        {
            "asset": r[0],
            "ts": r[1],
            "rubric_version": r[2],
            "asset_class": r[3],
            "sleeve": r[4],
            "composite_score": r[5],
            "components": r[6],
        }
        for r in rows
    ]
