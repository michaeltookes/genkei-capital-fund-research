"""News sentiment vs forward returns experiment (B-056).

Phase 5 experiment over ``gdelt.gkg`` (B-033) x ``coingecko.market_data``
or ``yahoo.candles``. Tests whether the GDELT GKG tone signal on a
watchlist asset has any forward-return predictive power at a configurable
horizon (default 1 day — "next-day" per the original B-056 framing).

Two roles in this module, matching the canonical Phase 5 split codified
in D-021:

  * **Pure functions** (``aggregate_daily_sentiment``,
    ``compute_daily_returns``, ``align_sentiment_with_returns``,
    ``compute_correlation``) operate on plain dataclasses. No DB, no
    CLI. Unit-testable on synthetic series.
  * **Lake-loading helpers** (``load_articles_for_asset``,
    ``load_price_returns``) pull the underlying data from Postgres.
    Caller composes them with the pure functions.

The headline metric is the **Pearson + Spearman correlation between
day-N tone and day-(N+H) return**, plus a per-tone-quartile mean
forward return breakdown. Pearson catches linear relationships;
Spearman catches monotonic relationships that aren't linear. The
quartile table answers the practical question "do the most-negative
news days predict drawdowns?" without imposing a functional form.

v1 explicit non-goals (see the resolved.md entry for the rationale):
- No macro-regime conditioning. We're asking "does the signal exist at
  all," not "is it tradeable under regime X."
- No market-hours alignment for equities. Same-calendar-day join is
  the v1 simplification. The actual market-hours effect (overnight vs
  intraday news) is a v2 refinement.
- No weekend effect for crypto vs equity. Equities have weekend gaps
  in the return series; we drop unaligned rows, which means
  weekend-news → Monday-return relationships are silently excluded in
  v1. Worth a follow-up if equity coverage materially differs from
  crypto coverage.
- No volume-weighted tone aggregation. A 10-article tone-5 day and a
  100-article tone-5 day weight identically. The ``article_count``
  field is preserved per-day so v2 can revisit.

Confidence floor — observations < ``MIN_OBSERVATIONS_FOR_SIGNAL`` (30)
return a ``"insufficient_data"`` status. With GDELT's daily ingest
starting 2026-06-09, this experiment will return "insufficient_data"
until ~mid-July 2026 unless a multi-month backfill seeds the lake
first. That's deliberate — better to surface "we don't have enough
data yet" loud than to publish a meaningless correlation from a 5-day
sample.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from genkei.common import db

# Below this observation count the correlation is statistically empty.
# 30 is the conventional threshold for "approximately normal" sampling
# distributions of correlation coefficients under standard assumptions.
MIN_OBSERVATIONS_FOR_SIGNAL = 30

# Default min-article floor — single-article days have mean_tone == that
# one article's tone, which is exactly the kind of noise we don't want
# polluting the regression. Three is the smallest sample where one
# outlier doesn't dominate the daily mean.
DEFAULT_MIN_ARTICLES_PER_DAY = 3

# Default forward horizon — "next-day" return per B-056's framing.
DEFAULT_HORIZON_DAYS = 1


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArticleRow:
    """One row from ``gdelt.gkg`` shaped for the aggregator."""

    published_at: datetime
    asset: str  # one element of the row's matched_assets
    tone: Decimal | None
    positive_score: Decimal | None
    negative_score: Decimal | None


@dataclass(frozen=True)
class SentimentPoint:
    """Per-(asset, day) aggregate over the day's matching GKG articles."""

    ts: date
    asset: str
    mean_tone: float | None
    article_count: int
    positive_mean: float | None
    negative_mean: float | None


@dataclass(frozen=True)
class ReturnPoint:
    """One day's close + day-over-day pct return for an asset."""

    ts: date
    asset: str
    close: float | None
    pct_return: float | None


@dataclass(frozen=True)
class AlignedPoint:
    """Sentiment day-N joined to return day-(N + horizon_days)."""

    sentiment_day: date
    return_day: date
    asset: str
    mean_tone: float
    article_count: int
    forward_return_pct: float


@dataclass(frozen=True)
class QuartileMean:
    """Mean forward return for the Q-th tone quartile."""

    quartile: int  # 1..4 (1 = most negative tone, 4 = most positive)
    n: int
    mean_tone: float
    mean_forward_return_pct: float


@dataclass(frozen=True)
class CorrelationReport:
    """Headline output: correlation + quartile breakdown + confidence."""

    asset: str
    horizon_days: int
    n_observations: int
    pearson: float | None
    spearman: float | None
    quartiles: list[QuartileMean]
    status: Literal["ok", "insufficient_data"]


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def aggregate_daily_sentiment(
    articles: list[ArticleRow],
) -> list[SentimentPoint]:
    """Group articles by ``(asset, day)`` → one SentimentPoint per bucket.

    Articles with ``tone = None`` contribute to ``article_count`` but
    NOT to ``mean_tone`` — the daily mean is over non-NULL tones only.
    A bucket with every article NULL-toned still emits a row (so the
    aggregate count is preserved) but ``mean_tone`` is None.

    ``positive_mean`` / ``negative_mean`` average the per-article
    positive_score / negative_score columns from GDELT's V1.5 tone
    vector. They're a more granular view of the same signal — mean_tone
    is the headline, the split is the breakdown.
    """
    groups: dict[tuple[str, date], dict[str, Any]] = {}
    for art in articles:
        if art.published_at is None:
            continue
        day = art.published_at.astimezone(timezone.utc).date()
        key = (art.asset, day)
        bucket = groups.setdefault(
            key,
            {
                "ts": day,
                "asset": art.asset,
                "tone_sum": 0.0,
                "tone_n": 0,
                "article_count": 0,
                "pos_sum": 0.0,
                "pos_n": 0,
                "neg_sum": 0.0,
                "neg_n": 0,
            },
        )
        bucket["article_count"] += 1
        if art.tone is not None:
            bucket["tone_sum"] += float(art.tone)
            bucket["tone_n"] += 1
        if art.positive_score is not None:
            bucket["pos_sum"] += float(art.positive_score)
            bucket["pos_n"] += 1
        if art.negative_score is not None:
            bucket["neg_sum"] += float(art.negative_score)
            bucket["neg_n"] += 1

    points: list[SentimentPoint] = []
    for bucket in groups.values():
        mean_tone = (
            bucket["tone_sum"] / bucket["tone_n"]
            if bucket["tone_n"] > 0
            else None
        )
        pos_mean = (
            bucket["pos_sum"] / bucket["pos_n"]
            if bucket["pos_n"] > 0
            else None
        )
        neg_mean = (
            bucket["neg_sum"] / bucket["neg_n"]
            if bucket["neg_n"] > 0
            else None
        )
        points.append(
            SentimentPoint(
                ts=bucket["ts"],
                asset=bucket["asset"],
                mean_tone=mean_tone,
                article_count=bucket["article_count"],
                positive_mean=pos_mean,
                negative_mean=neg_mean,
            )
        )
    # Stable order — date asc — so downstream join + tests are
    # deterministic.
    points.sort(key=lambda p: (p.asset, p.ts))
    return points


def compute_daily_returns(
    asset: str,
    *,
    days: list[date],
    closes: list[float | None],
) -> list[ReturnPoint]:
    """Compute day-over-day pct returns from a parallel (days, closes) series.

    The first day's ``pct_return`` is None (no prior close). Subsequent
    days compute ``(close_t - close_{t-1}) / close_{t-1} * 100``. Days
    where the prior close is None or zero get ``pct_return = None`` to
    avoid divide-by-zero / fake-zero-return rows.

    Calendar-gap behavior: if the input series has missing days (e.g.
    weekends in equity data), the returned series uses the previous
    *present* close as the denominator — i.e. Friday → Monday return
    is "Monday close / Friday close - 1", not the Saturday close
    (which doesn't exist). The caller decides whether multi-day gaps
    are biologically meaningful for their use case.
    """
    if len(days) != len(closes):
        raise ValueError(
            f"days and closes must align; got {len(days)} vs {len(closes)}"
        )
    out: list[ReturnPoint] = []
    prev_close: float | None = None
    for d, c in zip(days, closes):  # noqa: B905 — length guard above
        if c is None or prev_close is None or prev_close == 0.0:
            out.append(
                ReturnPoint(ts=d, asset=asset, close=c, pct_return=None)
            )
        else:
            ret = (c - prev_close) / prev_close * 100.0
            out.append(
                ReturnPoint(ts=d, asset=asset, close=c, pct_return=ret)
            )
        if c is not None:
            prev_close = c
    return out


def align_sentiment_with_returns(
    sentiment: list[SentimentPoint],
    returns: list[ReturnPoint],
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    min_articles_per_day: int = DEFAULT_MIN_ARTICLES_PER_DAY,
) -> list[AlignedPoint]:
    """Left-join sentiment day-N against return day-(N + horizon_days).

    Drops sentiment rows where:
      * ``article_count < min_articles_per_day`` (single/sparse-article
        noise filter)
      * ``mean_tone is None`` (no tone signal to correlate against)
      * The sentiment or horizon day has no matching close row (calendar
        gap on return side — common for equities over weekends/holidays)
      * Either close is NULL, or the sentiment-day close is zero

    ``compute_daily_returns`` still emits day-over-day returns for
    diagnostics, but this alignment computes the actual forward return
    from sentiment-day close to horizon-day close so multi-day horizons
    measure the requested cumulative outcome.

    The dropped-rows count is implicit in ``len(returned) /
    len(sentiment)``; callers wanting verbose diagnostics should
    compute it themselves and surface it in the report.
    """
    if horizon_days < 1:
        raise ValueError(f"horizon_days must be >= 1, got {horizon_days}")
    # Index returns by (asset, ts) for O(1) lookup.
    return_index: dict[tuple[str, date], ReturnPoint] = {
        (r.asset, r.ts): r for r in returns
    }
    aligned: list[AlignedPoint] = []
    for s in sentiment:
        if s.mean_tone is None or s.article_count < min_articles_per_day:
            continue
        target_day = _add_days(s.ts, horizon_days)
        start = return_index.get((s.asset, s.ts))
        target = return_index.get((s.asset, target_day))
        if (
            start is None
            or target is None
            or start.close is None
            or target.close is None
            or start.close == 0.0
        ):
            continue
        forward_return_pct = (target.close - start.close) / start.close * 100.0
        aligned.append(
            AlignedPoint(
                sentiment_day=s.ts,
                return_day=target_day,
                asset=s.asset,
                mean_tone=s.mean_tone,
                article_count=s.article_count,
                forward_return_pct=forward_return_pct,
            )
        )
    return aligned


def compute_correlation(
    aligned: list[AlignedPoint],
    *,
    asset: str,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    n_quartiles: int = 4,
) -> CorrelationReport:
    """Pearson + Spearman + per-quartile mean forward return.

    Returns ``status="insufficient_data"`` and all-None statistics
    when fewer than ``MIN_OBSERVATIONS_FOR_SIGNAL`` aligned rows are
    available. This is a load-bearing floor — the experiment isn't
    answering "is sentiment correlated" when the sample size is too
    small to distinguish any correlation from noise.

    Quartile bucketing splits the tone series into N equal-sized
    quantile buckets. The mean forward return per bucket is the
    practical "buy the most-negative-tone days" signal the agent
    would actually act on; Pearson + Spearman give the statistical
    summary.
    """
    n = len(aligned)
    if n < MIN_OBSERVATIONS_FOR_SIGNAL:
        return CorrelationReport(
            asset=asset,
            horizon_days=horizon_days,
            n_observations=n,
            pearson=None,
            spearman=None,
            quartiles=[],
            status="insufficient_data",
        )
    tones = [p.mean_tone for p in aligned]
    returns = [p.forward_return_pct for p in aligned]
    pearson = _pearson_correlation(tones, returns)
    spearman = _spearman_correlation(tones, returns)
    quartiles = _quartile_means(aligned, n_quartiles=n_quartiles)
    return CorrelationReport(
        asset=asset,
        horizon_days=horizon_days,
        n_observations=n,
        pearson=pearson,
        spearman=spearman,
        quartiles=quartiles,
        status="ok",
    )


# ---------------------------------------------------------------------------
# Statistical helpers (pure math, no deps)
# ---------------------------------------------------------------------------


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson product-moment correlation. None for zero-variance inputs."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))  # noqa: B905 — guard above
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return None
    return cov / denom


def _spearman_correlation(
    xs: list[float], ys: list[float]
) -> float | None:
    """Spearman rank correlation = Pearson on rank-transformed inputs."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx = _rank_with_ties(xs)
    ry = _rank_with_ties(ys)
    return _pearson_correlation(rx, ry)


def _rank_with_ties(xs: list[float]) -> list[float]:
    """Average-rank for ties so Spearman handles repeated tone values."""
    n = len(xs)
    # (value, original_index) pairs sorted by value.
    indexed = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        # Find end of the tied run.
        while j + 1 < n and xs[indexed[j + 1]] == xs[indexed[i]]:
            j += 1
        # Assign each tied position the average of their 1-based ranks.
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _quartile_means(
    aligned: list[AlignedPoint], *, n_quartiles: int
) -> list[QuartileMean]:
    """Split the tone-sorted series into N buckets; mean forward return per bucket.

    Ties at quartile boundaries are split by sort position (stable) —
    not statistically perfect but deterministic and good enough for the
    "which tone bucket beats the others on average" question.
    """
    if n_quartiles < 2:
        raise ValueError(f"n_quartiles must be >= 2, got {n_quartiles}")
    sorted_aligned = sorted(aligned, key=lambda a: a.mean_tone)
    n = len(sorted_aligned)
    bucket_size = n // n_quartiles
    out: list[QuartileMean] = []
    for q in range(n_quartiles):
        start = q * bucket_size
        # Last bucket grabs the remainder so we don't drop trailing rows
        # to integer-division truncation.
        end = (q + 1) * bucket_size if q < n_quartiles - 1 else n
        bucket = sorted_aligned[start:end]
        if not bucket:
            continue
        mean_tone = sum(p.mean_tone for p in bucket) / len(bucket)
        mean_ret = sum(p.forward_return_pct for p in bucket) / len(bucket)
        out.append(
            QuartileMean(
                quartile=q + 1,
                n=len(bucket),
                mean_tone=mean_tone,
                mean_forward_return_pct=mean_ret,
            )
        )
    return out


def _add_days(d: date, n: int) -> date:
    """Calendar-day add — equity weekends are handled by the return-side join, not here."""
    return d + timedelta(days=n)


# ---------------------------------------------------------------------------
# Lake loaders
# ---------------------------------------------------------------------------


def load_articles_for_asset(
    asset_label: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[ArticleRow]:
    """Pull every ``gdelt.gkg`` row mentioning ``asset_label`` (case-sensitive).

    ``asset_label`` is the upper-case ticker (equity) or upper-case
    symbol (crypto) per the GDELT collector's labeling convention
    (see ``genkei.ingest.gdelt.build_match_terms``). The GIN index on
    ``matched_assets`` makes the ``@>`` probe cheap.
    """
    if since is not None and until is not None and since > until:
        raise ValueError(f"since must be on or before until: {since} > {until}")
    sql = (
        "SELECT published_at, tone, positive_score, negative_score "
        "FROM gdelt.gkg WHERE matched_assets @> %s"
    )
    params: list[Any] = [[asset_label]]
    if since is not None:
        sql += " AND (published_at AT TIME ZONE 'UTC')::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND (published_at AT TIME ZONE 'UTC')::date <= %s"
        params.append(until)
    sql += " ORDER BY published_at"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        ArticleRow(
            published_at=published_at,
            asset=asset_label,
            tone=tone,
            positive_score=pos,
            negative_score=neg,
        )
        for (published_at, tone, pos, neg) in rows
    ]


def load_price_returns(
    *,
    asset: str,
    asset_class: Literal["crypto", "equity"],
    coingecko_id: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[ReturnPoint]:
    """Pull a daily-close series and return day-over-day pct returns.

    ``asset_class='crypto'`` reads ``coingecko.market_data.price_usd``
    keyed on ``coingecko_id`` (caller resolves via the watchlist).
    ``asset_class='equity'`` reads ``yahoo.candles.adj_close`` keyed on
    ``asset`` (the Yahoo ticker). adj_close is the split/dividend-
    adjusted close — the right field for total-return analysis.
    Yahoo rows missing adj_close (rare; older history) fall back to
    ``close`` so the experiment still runs.
    """
    if since is not None and until is not None and since > until:
        raise ValueError(f"since must be on or before until: {since} > {until}")
    if asset_class == "crypto":
        if coingecko_id is None:
            raise ValueError("asset_class='crypto' requires coingecko_id")
        sql = (
            "SELECT (ts AT TIME ZONE 'UTC')::date, price_usd "
            "FROM coingecko.market_data WHERE coingecko_id = %s"
        )
        params: list[Any] = [coingecko_id]
    elif asset_class == "equity":
        # COALESCE(adj_close, close) — adj_close is preferred but the
        # rare older row missing it shouldn't drop out silently.
        sql = (
            "SELECT (ts AT TIME ZONE 'UTC')::date, COALESCE(adj_close, close) "
            "FROM yahoo.candles WHERE ticker = %s"
        )
        params = [asset.upper()]
    else:
        raise ValueError(
            f"asset_class must be 'crypto' or 'equity', got {asset_class!r}"
        )
    if since is not None:
        sql += " AND (ts AT TIME ZONE 'UTC')::date >= %s"
        params.append(since)
    if until is not None:
        sql += " AND (ts AT TIME ZONE 'UTC')::date <= %s"
        params.append(until)
    sql += " ORDER BY ts"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    days = [r[0] for r in rows]
    closes = [float(r[1]) if r[1] is not None else None for r in rows]
    return compute_daily_returns(asset, days=days, closes=closes)
