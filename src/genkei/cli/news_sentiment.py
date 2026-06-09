"""``genkei news-sentiment`` — news tone vs forward returns (B-056).

Thin CLI wrapper over ``genkei.experiments.news_sentiment``. Joins
``gdelt.gkg`` per-asset daily tone aggregates against day-(N+H)
forward returns from either ``coingecko.market_data`` (crypto) or
``yahoo.candles`` (equity), and emits Pearson + Spearman correlations
plus a per-tone-quartile mean forward-return breakdown.

Usage:
  genkei news-sentiment --asset BTC --since 2026-06-09
  genkei news-sentiment --ticker AAPL --horizon-days 5
  genkei news-sentiment --asset ETH --min-articles-per-day 5 --json

Confidence floor — when fewer than 30 aligned (sentiment, return) pairs
are available the report returns ``status="insufficient_data"`` with
all-None statistics. That's the deliberate "we don't have enough data
yet" signal — the GDELT cron started 2026-06-09 so this experiment
will return insufficient_data until ~mid-July 2026 unless a
multi-month GDELT backfill seeds the lake first.
"""

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import (
    json_default as _json_default,
)
from genkei.cli._helpers import (
    parse_date as _parse_date,
)
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    Watchlist,
    load_watchlist,
)
from genkei.experiments.news_sentiment import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_MIN_ARTICLES_PER_DAY,
    MIN_OBSERVATIONS_FOR_SIGNAL,
    CorrelationReport,
    QuartileMean,
    aggregate_daily_sentiment,
    align_sentiment_with_returns,
    compute_correlation,
    load_articles_for_asset,
    load_price_returns,
)

_HORIZON_FOOTER = (
    "  Horizon: cross-sleeve | sleeve: research/news "
    "(experimental — predictive signal, not a trade trigger)"
)


def _resolve_asset_or_exit(
    *,
    ticker: Optional[str],
    asset: Optional[str],
    config: Path,
) -> tuple[str, str, Optional[str]]:
    """Resolve the CLI input to (asset_label, asset_class, coingecko_id).

    ``asset_label`` is the upper-case ticker (equity) or upper-case
    symbol (crypto) per the GDELT collector's labeling convention.
    ``asset_class`` is "crypto" or "equity" — drives the price-source
    routing. ``coingecko_id`` is only populated for crypto.
    """
    if ticker is not None and asset is not None:
        raise typer.BadParameter(
            "--ticker and --asset are mutually exclusive — pass one or the other."
        )
    if ticker is None and asset is None:
        raise typer.BadParameter(
            "Pass --ticker <EQUITY> or --asset <CRYPTO> — single-asset analysis only in v1."
        )
    try:
        watchlist: Watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(f"failed to load watchlist: {exc}") from exc
    if ticker is not None:
        entry = watchlist.find_equity(ticker)
        if entry is None:
            raise typer.BadParameter(
                f"--ticker {ticker!r} is not in equities:; check watchlists.yml."
            )
        return entry.symbol.upper(), "equity", None
    crypto = watchlist.find_crypto(asset or "")
    if crypto is None:
        raise typer.BadParameter(
            f"--asset {asset!r} is not in crypto:; check watchlists.yml."
        )
    return crypto.symbol.upper(), "crypto", crypto.coingecko_id


def _compute_report(
    *,
    asset_label: str,
    asset_class: str,
    coingecko_id: Optional[str],
    since: Optional[date],
    until: Optional[date],
    horizon_days: int,
    min_articles_per_day: int,
) -> CorrelationReport:
    """Pull the lake data + run the pure-function pipeline."""
    articles = load_articles_for_asset(
        asset_label, since=since, until=until
    )
    # Each ArticleRow carries the (already-resolved) asset label.
    # The loader assigns asset_label uniformly, so the aggregator
    # produces a single per-asset series. Done in the loader rather
    # than here so a future cross-asset mode can re-use the math
    # without re-shaping.
    sentiment_pts = aggregate_daily_sentiment(articles)
    returns = load_price_returns(
        asset=asset_label,
        asset_class=asset_class,  # type: ignore[arg-type]
        coingecko_id=coingecko_id,
        since=since,
        until=until,
    )
    aligned = align_sentiment_with_returns(
        sentiment_pts,
        returns,
        horizon_days=horizon_days,
        min_articles_per_day=min_articles_per_day,
    )
    return compute_correlation(
        aligned, asset=asset_label, horizon_days=horizon_days
    )


def _quartile_to_dict(q: QuartileMean) -> dict[str, Any]:
    return {
        "quartile": q.quartile,
        "n": q.n,
        "mean_tone": q.mean_tone,
        "mean_forward_return_pct": q.mean_forward_return_pct,
    }


def _report_to_dict(
    report: CorrelationReport, *, asset_class: str
) -> dict[str, Any]:
    return {
        "asset": report.asset,
        "asset_class": asset_class,
        "horizon_days": report.horizon_days,
        "n_observations": report.n_observations,
        "status": report.status,
        "pearson": report.pearson,
        "spearman": report.spearman,
        "quartiles": [_quartile_to_dict(q) for q in report.quartiles],
    }


def _format_human(report: CorrelationReport, *, asset_class: str) -> str:
    """Render the correlation report as a human-readable block."""
    header = (
        f"News sentiment vs forward returns | "
        f"asset={report.asset} ({asset_class}) | "
        f"horizon={report.horizon_days}d | "
        f"n={report.n_observations}"
    )
    lines = [header, "-" * len(header)]
    if report.status == "insufficient_data":
        lines.append(
            f"  status=insufficient_data "
            f"(need ≥ {MIN_OBSERVATIONS_FOR_SIGNAL} aligned observations, "
            f"got {report.n_observations})"
        )
        lines.append(
            "  Hint: the GDELT cron started 2026-06-09. "
            "Backfill via `python3 -m genkei.ingest.gdelt --backfill --since YYYY-MM-DD` "
            "seeds historical days; otherwise wait for organic accumulation."
        )
        lines.append("")
        lines.append(_HORIZON_FOOTER)
        return "\n".join(lines)
    pearson = (
        f"{report.pearson:+.4f}" if report.pearson is not None else "n/a"
    )
    spearman = (
        f"{report.spearman:+.4f}" if report.spearman is not None else "n/a"
    )
    lines.append(f"  Pearson  (linear)   = {pearson}")
    lines.append(f"  Spearman (monotonic)= {spearman}")
    if report.quartiles:
        lines.append("")
        lines.append("  Per tone-quartile mean forward return:")
        lines.append(
            f"    {'quartile':<10}{'n':>6}  {'mean_tone':>12}  "
            f"{'mean_fwd_return_pct':>22}"
        )
        for q in report.quartiles:
            lines.append(
                f"    Q{q.quartile:<9}{q.n:>6}  "
                f"{q.mean_tone:>+12.3f}  "
                f"{q.mean_forward_return_pct:>+22.4f}"
            )
    lines.append("")
    lines.append(_HORIZON_FOOTER)
    return "\n".join(lines)


def news_sentiment_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            "-t",
            help="Watchlist equity ticker (mutually exclusive with --asset).",
        ),
    ] = None,
    asset: Annotated[
        Optional[str],
        typer.Option(
            "--asset",
            "-a",
            help="Watchlist crypto symbol (mutually exclusive with --ticker).",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option(
            "--since", help="Earliest publication / trading date (YYYY-MM-DD)."
        ),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option(
            "--until", help="Latest publication / trading date (YYYY-MM-DD)."
        ),
    ] = None,
    horizon_days: Annotated[
        int,
        typer.Option(
            "--horizon-days",
            help="Forward return horizon in calendar days (default 1 = next-day).",
            min=1,
        ),
    ] = DEFAULT_HORIZON_DAYS,
    min_articles_per_day: Annotated[
        int,
        typer.Option(
            "--min-articles-per-day",
            help="Drop days with fewer matching articles (noise filter; default 3).",
            min=1,
        ),
    ] = DEFAULT_MIN_ARTICLES_PER_DAY,
    json_out: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit machine-readable JSON instead of human table.",
        ),
    ] = False,
    config: Annotated[
        Path,
        typer.Option(
            "--config", help="Watchlist path.", show_default=True
        ),
    ] = DEFAULT_WATCHLIST_PATH,
) -> None:
    """Correlate GDELT GKG daily tone against day-(N+H) forward returns."""
    asset_label, asset_class, coingecko_id = _resolve_asset_or_exit(
        ticker=ticker, asset=asset, config=config
    )
    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    report = _compute_report(
        asset_label=asset_label,
        asset_class=asset_class,
        coingecko_id=coingecko_id,
        since=since_d,
        until=until_d,
        horizon_days=horizon_days,
        min_articles_per_day=min_articles_per_day,
    )

    if json_out:
        typer.echo(
            json.dumps(
                _report_to_dict(report, asset_class=asset_class),
                indent=2,
                default=_json_default,
            )
        )
        return
    typer.echo(_format_human(report, asset_class=asset_class))
