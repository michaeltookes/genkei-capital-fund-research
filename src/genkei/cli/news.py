"""``genkei news`` — query the GDELT GKG lake for articles + clusters (B-043).

Reads ``gdelt.gkg`` populated by the daily ``genkei.ingest.gdelt`` collector
(B-033) and clusters matching articles by ``(published_at::date,
source_common_name)`` — the lightweight v1 clustering shape. Each cluster
carries article count, mean tone, matched watchlist labels, and up to
three representative article URLs.

Filters (all combinable; AND-joined at the SQL layer):
  --ticker AAPL    — watchlist equity → matched_assets @> ['AAPL']
  --asset BTC      — watchlist crypto → matched_assets @> ['BTC']
  --theme ECON_X   — themes @> ['ECON_X'] (GDELT themes are upper-case canonical)
  --topic "ai"     — case-insensitive substring across themes + document URL
  --since YYYY-MM-DD / --until YYYY-MM-DD
  --tone-min N / --tone-max N
  --source nytimes.com — exact source_common_name match (case-insensitive)
  --limit N        — max clusters returned (default 30)

Usage:
  genkei news --asset BTC --since 2026-05-01
  genkei news --ticker AAPL --tone-min -5 --tone-max 5
  genkei news --topic "AI capex" --since 2024-01-01
  genkei news --json --limit 10

**Cluster shape (lightweight v1)**: GROUP BY (UTC date, source). A
theme-aware clustering (Jaccard over the themes array) is a follow-up
worth filing if (date, source) proves too coarse in practice. The
underlying article rows are fully queryable via ``genkei query`` for
ad-hoc shapes the typed surface doesn't express.
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
from genkei.common import db
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    load_watchlist,
)

# Cap the underlying article fetch — clustering 30k articles down to N
# clusters wastes round-trip bandwidth and the long tail of low-count
# clusters isn't load-bearing. The default --limit is 30 clusters; this
# pool supports up to 30 clusters of ~67 articles each, which comfortably
# exceeds the largest watchlist-asset day we'd ever look at.
ARTICLE_POOL_CAP = 2000

# Cluster output shows up to N representative URLs per group — enough to
# eyeball the cluster's character without dumping the long tail. The
# underlying rows stay queryable via genkei query for ad-hoc inspection.
SAMPLE_URLS_PER_CLUSTER = 3

_HORIZON_FOOTER = (
    "  Horizon: cross-sleeve | sleeve: research/news (consume alongside prices, filings, on-chain)"
)


def _resolve_watchlist_label(
    *,
    ticker: Optional[str],
    asset: Optional[str],
    config: Path,
) -> Optional[str]:
    """Resolve --ticker or --asset to the canonical label landed by the ingester.

    The collector's ``build_match_terms`` writes equities under their
    upper-case ticker and crypto under their upper-case symbol, so the
    label stored in ``matched_assets`` is whatever ``.upper()`` gives.
    We don't strictly need to resolve via the watchlist file — the
    upper-case form is the label by convention — but loading the file
    catches typos (an unknown ticker fails loud rather than silently
    matching zero rows).
    """
    if ticker is not None and asset is not None:
        raise typer.BadParameter(
            "--ticker and --asset are mutually exclusive — pass one or the other."
        )
    if ticker is None and asset is None:
        return None
    raw = (ticker or asset or "").strip()
    if not raw:
        return None
    try:
        watchlist = load_watchlist(config)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(f"failed to load watchlist: {exc}") from exc
    if ticker is not None:
        entry = watchlist.find_equity(ticker)
        if entry is None:
            raise typer.BadParameter(
                f"--ticker {ticker!r} is not in equities:; check watchlists.yml."
            )
        return entry.symbol.upper()
    entry = watchlist.find_crypto(asset or "")
    if entry is None:
        raise typer.BadParameter(
            f"--asset {asset!r} is not in crypto:; check watchlists.yml."
        )
    return entry.symbol.upper()


def _build_article_query(
    *,
    matched_label: Optional[str],
    theme: Optional[str],
    topic: Optional[str],
    since: Optional[date],
    until: Optional[date],
    tone_min: Optional[float],
    tone_max: Optional[float],
    source: Optional[str],
    pool_cap: int,
) -> tuple[str, list[Any]]:
    """Compose the underlying SELECT against ``gdelt.gkg`` with WHERE filters.

    Returns ``(sql, params)``. ``params`` is the positional list aligned to
    the ``%s`` placeholders. Pool size caps the row count so a wide filter
    (e.g. just --since 2025-01-01) doesn't pull every matched article.
    """
    sql = (
        "SELECT published_at, source_common_name, document_identifier, "
        "tone, matched_assets, themes "
        "FROM gdelt.gkg WHERE 1=1"
    )
    params: list[Any] = []

    if matched_label is not None:
        # GIN index on matched_assets makes the @> probe cheap.
        sql += " AND matched_assets @> %s"
        params.append([matched_label])
    if theme is not None:
        sql += " AND themes @> %s"
        params.append([theme])
    if topic is not None:
        # Substring across both the article URL and the concatenated
        # themes. Themes are upper-case GDELT canonical strings; the URL
        # is whatever the publisher put in their HTML. ILIKE handles
        # case-insensitivity in one place.
        sql += (
            " AND (document_identifier ILIKE %s "
            "OR array_to_string(themes, ' ') ILIKE %s)"
        )
        pattern = f"%{topic}%"
        params.extend([pattern, pattern])
    if since is not None:
        sql += " AND published_at >= %s"
        params.append(since)
    if until is not None:
        # Include the full UTC day for --until — compare against the
        # date column directly so callers don't have to think about
        # timezone-shifted boundaries.
        sql += " AND (published_at AT TIME ZONE 'UTC')::date <= %s"
        params.append(until)
    if tone_min is not None:
        sql += " AND tone >= %s"
        params.append(tone_min)
    if tone_max is not None:
        sql += " AND tone <= %s"
        params.append(tone_max)
    if source is not None:
        # source_common_name is the lowercased publisher domain in GDELT
        # (e.g. 'nytimes.com'); accept either case from the caller.
        sql += " AND lower(source_common_name) = %s"
        params.append(source.strip().lower())

    sql += " ORDER BY published_at DESC LIMIT %s"
    params.append(pool_cap)
    return sql, params


def _fetch_articles(
    sql: str, params: list[Any]
) -> list[dict[str, Any]]:
    """Run the article SELECT and return a list of plain dicts."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "published_at": ts,
            "source_common_name": src,
            "document_identifier": doc,
            "tone": tone,
            "matched_assets": list(ma) if ma is not None else [],
            "themes": list(themes) if themes is not None else [],
        }
        for ts, src, doc, tone, ma, themes in rows
    ]


def _cluster_articles(
    articles: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    """Group articles by (UTC date, source_common_name) → cluster rows.

    Each cluster carries article_count, mean_tone (None when every
    article in the cluster has a NULL tone), matched_assets (set across
    all articles, sorted), and up to ``SAMPLE_URLS_PER_CLUSTER`` URLs
    (the most recent N by published_at).

    Sorted by article_count DESC, then by day DESC for tie-break (recent
    big clusters surface first). Truncated to ``limit`` clusters.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for art in articles:
        ts = art["published_at"]
        # published_at is timestamptz from Postgres — psycopg returns it
        # as an aware datetime. Bucket by UTC date for the cluster key.
        day_key = ts.date().isoformat() if ts is not None else "unknown"
        src_key = (art["source_common_name"] or "").lower() or "unknown"
        key = (day_key, src_key)
        bucket = groups.setdefault(
            key,
            {
                "day": day_key,
                "source": src_key,
                "article_count": 0,
                "tone_sum": 0.0,
                "tone_n": 0,
                "matched_assets": set(),
                "sample_urls": [],
                "_sort_ts": ts,
            },
        )
        bucket["article_count"] += 1
        if art["tone"] is not None:
            bucket["tone_sum"] += float(art["tone"])
            bucket["tone_n"] += 1
        for label in art["matched_assets"]:
            bucket["matched_assets"].add(label)
        if (
            len(bucket["sample_urls"]) < SAMPLE_URLS_PER_CLUSTER
            and art["document_identifier"]
            and art["document_identifier"] not in bucket["sample_urls"]
        ):
            bucket["sample_urls"].append(art["document_identifier"])
        # Articles arrive sorted by published_at DESC, so the first ts we
        # see for a bucket is its max — leave it; later writes are older.

    clusters: list[dict[str, Any]] = []
    for bucket in groups.values():
        mean_tone = (
            bucket["tone_sum"] / bucket["tone_n"]
            if bucket["tone_n"] > 0
            else None
        )
        clusters.append(
            {
                "day": bucket["day"],
                "source": bucket["source"],
                "article_count": bucket["article_count"],
                "mean_tone": mean_tone,
                "matched_assets": sorted(bucket["matched_assets"]),
                "sample_urls": bucket["sample_urls"],
            }
        )
    clusters.sort(key=lambda c: (-c["article_count"], c["day"]), reverse=False)
    # The tuple above sorts article_count ASC because we negated it for
    # the primary key, but day needs DESC. Re-sort explicitly:
    clusters.sort(key=lambda c: (-c["article_count"], -_day_sort_key(c["day"])))
    return clusters[:limit]


def _day_sort_key(day_iso: str) -> int:
    """Encode YYYY-MM-DD → 20260609 for stable integer-based DESC sort."""
    try:
        y, m, d = day_iso.split("-")
        return int(y) * 10000 + int(m) * 100 + int(d)
    except (ValueError, AttributeError):
        # 'unknown' bucket sorts last.
        return -1


def _format_clusters_human(
    clusters: list[dict[str, Any]],
    *,
    summary: dict[str, Any],
) -> str:
    """Render clusters as a human-readable block list."""
    if not clusters:
        return (
            "No matching clusters. "
            "Is gdelt.gkg populated yet? — "
            "`python3 -m genkei.ingest.gdelt --hours 24` seeds the lake."
        )
    header_filters = ", ".join(
        f"{k}={v}" for k, v in summary.items() if v is not None
    )
    if not header_filters:
        header_filters = "no filters"
    header = (
        f"GDELT news clusters | {header_filters} | "
        f"{len(clusters)} cluster{'s' if len(clusters) != 1 else ''}"
    )
    lines = [header, "-" * len(header)]
    for c in clusters:
        tone_display = (
            f"{c['mean_tone']:+.2f}" if c["mean_tone"] is not None else "n/a"
        )
        assets_display = ",".join(c["matched_assets"]) if c["matched_assets"] else "-"
        lines.append(
            f"  {c['day']}  {c['source']:<28} "
            f"articles={c['article_count']:>3}  tone={tone_display:>7}  "
            f"assets={assets_display}"
        )
        for url in c["sample_urls"]:
            lines.append(f"    - {url}")
    lines.append("")
    lines.append(_HORIZON_FOOTER)
    return "\n".join(lines)


def news_cmd(
    ticker: Annotated[
        Optional[str],
        typer.Option(
            "--ticker",
            "-t",
            help="Filter to a watchlist equity (matched_assets contains TICKER).",
        ),
    ] = None,
    asset: Annotated[
        Optional[str],
        typer.Option(
            "--asset",
            "-a",
            help="Filter to a watchlist crypto (matched_assets contains SYMBOL).",
        ),
    ] = None,
    theme: Annotated[
        Optional[str],
        typer.Option(
            "--theme",
            help="Filter to a GDELT theme (themes array contains THEME).",
        ),
    ] = None,
    topic: Annotated[
        Optional[str],
        typer.Option(
            "--topic",
            help="Free-text substring across document URL + themes (ILIKE).",
        ),
    ] = None,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Earliest publication date (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        Optional[str],
        typer.Option("--until", help="Latest publication date (YYYY-MM-DD)."),
    ] = None,
    tone_min: Annotated[
        Optional[float],
        typer.Option(
            "--tone-min", help="Minimum article tone (-100..100; GDELT V1.5 avg)."
        ),
    ] = None,
    tone_max: Annotated[
        Optional[float],
        typer.Option(
            "--tone-max", help="Maximum article tone (-100..100; GDELT V1.5 avg)."
        ),
    ] = None,
    source: Annotated[
        Optional[str],
        typer.Option(
            "--source",
            help="Exact-match source_common_name (e.g. 'nytimes.com').",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max clusters to return.", min=1),
    ] = 30,
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
    """Query the GDELT GKG lake for clusters of watchlist-mentioning articles."""
    matched_label = _resolve_watchlist_label(
        ticker=ticker, asset=asset, config=config
    )

    since_d = _parse_date(since, label="since")
    until_d = _parse_date(until, label="until")
    if since_d is not None and until_d is not None and since_d > until_d:
        raise typer.BadParameter("--since must be on or before --until.")

    if tone_min is not None and tone_max is not None and tone_min > tone_max:
        raise typer.BadParameter("--tone-min must be ≤ --tone-max.")

    sql, params = _build_article_query(
        matched_label=matched_label,
        theme=theme,
        topic=topic,
        since=since_d,
        until=until_d,
        tone_min=tone_min,
        tone_max=tone_max,
        source=source,
        pool_cap=ARTICLE_POOL_CAP,
    )
    articles = _fetch_articles(sql, params)
    clusters = _cluster_articles(articles, limit=limit)

    summary = {
        "ticker": ticker,
        "asset": asset,
        "theme": theme,
        "topic": topic,
        "since": since_d.isoformat() if since_d else None,
        "until": until_d.isoformat() if until_d else None,
        "tone_min": tone_min,
        "tone_max": tone_max,
        "source": source,
        "pool_size": len(articles),
    }

    if json_out:
        typer.echo(
            json.dumps(
                {"summary": summary, "clusters": clusters},
                indent=2,
                default=_json_default,
            )
        )
        return
    typer.echo(_format_clusters_human(clusters, summary=summary))
