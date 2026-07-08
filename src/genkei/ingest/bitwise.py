"""Bitwise spot crypto ETF daily snapshot collector (B-113).

Second-issuer expansion of the spot-crypto-ETF net-flow signal that B-107
shipped for BlackRock/iShares. Lands one row per ``(ticker, snapshot_date)``
in ``etf.fund_snapshots`` for each watchlist ``etf_tickers`` entry where
``issuer == "Bitwise"``. v1 covers BITB (Bitwise Bitcoin ETF, ~$2.2B AUM) —
see ``docs/sources/spot-etf-net-flow.md`` for the issuer survey that flagged
Bitwise as the cleanest non-BlackRock path.

**Why HTML, not JSON.** Unlike iShares' single product-screener JSON feed,
Bitwise serves each fund on its own statically-generated (Next.js) product
site (``bitbetf.com``). The fund financials — NAV, net assets, and shares
outstanding — are server-rendered straight into the page HTML; there is no
public JSON API behind it (the only client-side API calls are a Salesforce
contact form + a Turnstile widget, neither carrying fund data). So this
collector fetches the HTML and extracts the labeled values, anchoring every
pattern on the **label text** (e.g. ``Shares Outstanding``) rather than the
build-generated ``c-*`` CSS class names, which churn on every site rebuild.

**Why store all three published values (vs deriving one).** iShares publishes
NAV + total-net-assets and we *derive* shares = TNA / NAV. Bitwise publishes
all three independently (NAV, net assets, AND shares outstanding), so we store
each as published — and use date + value coherence as the gate: the Fund
Details and NAV ``Data as of`` stamps must agree within
``MAX_SECTION_DATE_SKEW_DAYS`` (a day or two of NAV/AUM refresh skew is normal
ETF timing — seen on ETHW — not parse drift), then ``nav x shares`` must
reconcile to ``net_assets`` within ``RECONCILE_TOLERANCE``. That value
reconciliation is the real coherence gate — the Bitwise analog of the iShares
"navAmountAsOf must equal totalNetAssetsFundAsOf" check. The observed
reconciliation gap is ~0.01% at a shared date and ~0.5% at a one-day skew.

Daily net flow is NOT stored — it's computed at query time in
``genkei etf-flows --net-flow`` via ``(shares - LAG(shares)) x nav`` exactly
as for the iShares rows, so BITB joins the existing net-flow surface with no
query change (the query filters by ``asset`` + watchlist ticker, not issuer).

Modes:
  - **incremental** (default) — fetch the current product page(s). The page
    publishes the most recent NAV strike (T+1/T+2); idempotent via the
    ``(ticker, snapshot_date)`` PK, so re-running the same day is a no-op
    upsert. There is no backfill mode — the page carries only the current
    snapshot (same constraint as iShares; historical backfill is the SEC
    10-Q v2.1 follow-up, B-114).

No API key required. The product site is unauthenticated, no Cloudflare
challenge, no rate limit observed (verified 2026-06-30).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from genkei.common import db
from genkei.common.http import HttpClient, RateLimit
from genkei.common.watchlist import (
    DEFAULT_WATCHLIST_PATH,
    EtfTickerEntry,
    load_watchlist,
)

SOURCE_NAME = "bitwise"
# "collect" matches the convention pinned by
# test_every_source_expects_at_least_a_collect_endpoint in
# tests/cli/test_watchlist_cmd.py — the single-step ingester parses the HTML
# inline and writes directly to etf.fund_snapshots (no separate normalize).
COLLECT_ENDPOINT_LABEL = "collect"
ISSUER_FILTER = "Bitwise"

# Per-ticker Bitwise product pages. Each spot-crypto ETF has its own
# statically-generated product site that server-renders fund financials into
# the HTML. BITB verified live 2026-06-30; ETHW (Bitwise Ethereum ETF, B-129)
# verified 2026-07-07 — same page shape, same labels. A watchlist Bitwise
# ticker with no entry here is soft-skipped with a WARNING (so adding a new
# ticker to the watchlist before its URL is pinned here doesn't break the run).
PRODUCT_URLS: dict[str, str] = {
    "BITB": "https://bitbetf.com/",
    "ETHW": "https://ethwetf.com/",
}

# Published NAV x published shares must reconcile to published net assets
# within this fraction, else the snapshot is internally inconsistent (parse
# drift, or a NAV struck too far from the shares/AUM refresh) and is skipped
# rather than stored. Observed gap is ~0.01% when the sections share a date and
# ~0.5% at a one-day NAV/AUM skew; 2% is generous headroom for both.
RECONCILE_TOLERANCE = Decimal("0.02")

# The NAV strike and the Fund Details (shares/AUM) section can carry dates a
# day or two apart — the per-share NAV is struck T+1 while the AUM/shares
# refresh can lead or lag it by a day (seen on ETHW 2026-07-07: NAV as-of 7/5,
# Fund Details as-of 7/6). A small skew is normal ETF operational timing, not
# parse drift; the value reconciliation above is the real coherence gate. Only
# a *large* gap — a stray date picked up from elsewhere on the page — should
# void the snapshot. ``snapshot_date`` is taken from the Fund Details section
# because shares_outstanding (the net-flow driver) and net_assets both come
# from there, so the stored shares series is dated by its own refresh, not by
# the NAV strike (which keeps net-flow's LAG(shares) sequencing correct when
# the NAV date lags). BITB, whose sections share a date, sees skew=0 → no
# behavior change.
MAX_SECTION_DATE_SKEW_DAYS = 3

# A browser User-Agent — the static site serves scripted requests fine, but a
# default httpx UA invites future bot-walling; mirror a real browser.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Polite ceiling for a daily-cron use case; the site has no observed limit.
DEFAULT_RATE_LIMIT = RateLimit.per_second(1)

LOGGER = logging.getLogger(__name__)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class _FundSnapshot:
    """Normalized snapshot extracted from one Bitwise product page."""

    ticker: str
    snapshot_date: date
    issuer: str
    asset: str
    cusip: str | None
    isin: str | None
    nav_per_share_usd: Decimal
    total_net_assets_usd: Decimal
    shares_outstanding: Decimal


def _strip_html_comments(html: str) -> str:
    """Drop ``<!-- -->`` comment nodes that Next.js sprinkles between text and
    ``<span>`` values (e.g. ``NAV: <!-- --><span>$32.71</span>``). Removing
    them up front lets every value pattern match clean adjacent markup."""
    return _COMMENT_RE.sub("", html)


def _safe_decimal(text: str, *, field: str) -> Decimal | None:
    """``Decimal(text)`` where an unparseable number yields ``None`` silently
    but any *unexpected* failure logs a WARNING instead of vanishing — in
    unattended daily ingest a swallowed surprise is the difference between
    noticing bad data and not (B-121)."""
    try:
        return Decimal(text)
    except (ValueError, InvalidOperation):
        return None
    except Exception:  # pragma: no cover - defensive
        LOGGER.warning(
            "bitwise: unexpected error coercing %s=%r to Decimal",
            field,
            text,
            exc_info=True,
        )
        return None


def _parse_money(raw: str | None, *, field: str) -> Decimal | None:
    """Parse a ``$1,234.56`` / ``1,234`` money string into a Decimal.

    Strips a leading ``$`` and thousands separators. Empty / ``-`` → None.
    """
    if raw is None:
        return None
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    return _safe_decimal(cleaned, field=field)


def _find_labeled_value(html: str, label: str) -> str | None:
    """Pull the value cell of a ``<h4>LABEL</h4><p>VALUE</p>`` key-facts pair.

    Anchored on the label *text*, so the build-generated ``class="c-..."``
    attributes (which change on every Bitwise site rebuild) don't matter.
    """
    pattern = re.compile(
        rf"<h4[^>]*>\s*{re.escape(label)}\s*</h4>\s*<p[^>]*>([^<]+)</p>",
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if not m:
        return None
    return m.group(1).strip()


def _find_nav(html: str) -> Decimal | None:
    """Extract the NAV-per-share from the ``NAV: <span>$32.71</span>`` block.

    ``html`` must already have comments stripped. Anchored on the ``NAV:``
    label so the surrounding flex-layout class names are irrelevant. The
    ``Market Price:`` value sits in an identical sibling span — the ``NAV:``
    prefix (with no trailing letter) is what disambiguates the two.
    """
    m = re.search(r">\s*NAV:\s*<span[^>]*>\s*\$?([0-9,]+\.?[0-9]*)\s*<", html)
    if not m:
        return None
    return _parse_money(m.group(1), field="nav")


def _parse_as_of_match(m: re.Match[str], *, section: str) -> date | None:
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        LOGGER.warning("bitwise: unparseable %s as-of date %s", section, m.group(0))
        return None


def _find_section_as_of(
    html: str,
    header_pattern: str,
    *,
    section: str,
    window_chars: int = 500,
) -> date | None:
    """Extract the first ``Data as of MM/DD/YYYY`` after a section header."""
    header = re.search(
        rf"<h[1-6][^>]*>\s*{header_pattern}[^<]*</h[1-6]>",
        html,
        re.IGNORECASE,
    )
    if not header:
        return None
    window = html[header.end() : header.end() + window_chars]
    m = re.search(r"Data as of\s*(\d{1,2})/(\d{1,2})/(\d{4})", window, re.IGNORECASE)
    if not m:
        return None
    return _parse_as_of_match(m, section=section)


def _find_fund_details_as_of(html: str) -> date | None:
    """Extract the Fund Details section date for shares/net-assets fields."""
    return _find_section_as_of(
        html,
        re.escape("Fund Details"),
        section="Fund Details",
    )


def _find_nav_as_of(html: str) -> date | None:
    """Extract the NAV strike date from the NAV section's ``Data as of`` stamp.

    Anchors on the NAV section header first, then takes the next
    ``Data as of MM/DD/YYYY`` within a bounded window so it can't pick up the
    Fund Details, portfolio-characteristics, or marketing dates elsewhere on
    the page. ``html`` must already have comments stripped.
    """
    return _find_section_as_of(
        html,
        r"Net Asset Value\s*\(NAV\)",
        section="NAV",
    )


def _reconciles(
    nav: Decimal, shares: Decimal, net_assets: Decimal, *, ticker: str
) -> bool:
    """True when published ``nav x shares`` is within tolerance of ``net_assets``.

    Net assets of zero is a valid terminal state (fund fully redeemed) and
    reconciles only if ``shares`` is also zero. A non-zero implied value
    against zero reported net assets fails — that's an incoherent page.
    """
    implied = nav * shares
    if net_assets == 0:
        return implied == 0
    gap = abs(implied - net_assets) / net_assets
    if gap > RECONCILE_TOLERANCE:
        LOGGER.warning(
            "bitwise %s: NAV x shares (%.0f) vs net assets (%.0f) differ by "
            "%.2f%% > %.0f%% tolerance — skipping incoherent snapshot",
            ticker,
            implied,
            net_assets,
            gap * 100,
            RECONCILE_TOLERANCE * 100,
        )
        return False
    return True


def parse_snapshot(
    html: str,
    *,
    ticker: str,
    watchlist_entry: EtfTickerEntry,
) -> _FundSnapshot | None:
    """Decode one Bitwise product page into a snapshot row, or None.

    Returns None (with a WARNING) when any required financial is missing,
    non-positive, struck on mixed section dates, or the three published values
    fail to reconcile — the same skip-rather-than-store-garbage discipline the
    iShares parser uses. The
    ``cusip`` / ``isin`` identifiers are best-effort: absent ones become NULL
    without dropping the row.
    """
    clean = _strip_html_comments(html)

    shares = _parse_money(
        _find_labeled_value(clean, "Shares Outstanding"), field="shares"
    )
    net_assets = _parse_money(
        _find_labeled_value(clean, "Net Assets (AUM)"), field="net_assets"
    )
    nav = _find_nav(clean)
    fund_details_as_of = _find_fund_details_as_of(clean)
    as_of = _find_nav_as_of(clean)

    if (
        shares is None
        or net_assets is None
        or nav is None
        or fund_details_as_of is None
        or as_of is None
    ):
        LOGGER.warning(
            "bitwise %s: missing required fields "
            "(shares=%s net_assets=%s nav=%s fund_details_as_of=%s "
            "nav_as_of=%s) — skipping",
            ticker,
            shares,
            net_assets,
            nav,
            fund_details_as_of,
            as_of,
        )
        return None
    skew_days = abs((fund_details_as_of - as_of).days)
    if skew_days > MAX_SECTION_DATE_SKEW_DAYS:
        LOGGER.warning(
            "bitwise %s: Fund Details as-of %s and NAV as-of %s differ by %s days "
            "> %s tolerance — skipping mixed-date snapshot",
            ticker,
            fund_details_as_of,
            as_of,
            skew_days,
            MAX_SECTION_DATE_SKEW_DAYS,
        )
        return None
    if nav <= 0:
        LOGGER.warning("bitwise %s: non-positive NAV %s — skipping", ticker, nav)
        return None
    if shares < 0 or net_assets < 0:
        LOGGER.warning(
            "bitwise %s: negative shares (%s) or net assets (%s) — skipping",
            ticker,
            shares,
            net_assets,
        )
        return None
    if not _reconciles(nav, shares, net_assets, ticker=ticker):
        return None

    return _FundSnapshot(
        ticker=ticker.upper(),
        # Date by the Fund Details section — shares_outstanding + net_assets
        # come from there; the NAV strike may lag by a day (see
        # MAX_SECTION_DATE_SKEW_DAYS). Equal for BITB, so unchanged there.
        snapshot_date=fund_details_as_of,
        issuer=watchlist_entry.issuer,
        asset=watchlist_entry.asset.upper(),
        cusip=_find_labeled_value(clean, "CUSIP"),
        isin=_find_labeled_value(clean, "ISIN"),
        nav_per_share_usd=nav,
        total_net_assets_usd=net_assets,
        shares_outstanding=shares.quantize(Decimal("0.0001")),
    )


def _snapshot_to_row(
    snap: _FundSnapshot,
    *,
    ingest_run_id: int,
    source_endpoint: str,
    fetched_at: datetime,
) -> dict[str, Any]:
    """Convert a _FundSnapshot to a bulk_upsert row dict."""
    return {
        "ticker": snap.ticker,
        "snapshot_date": snap.snapshot_date,
        "issuer": snap.issuer,
        "asset": snap.asset,
        "cusip": snap.cusip,
        "isin": snap.isin,
        "nav_per_share_usd": snap.nav_per_share_usd,
        "total_net_assets_usd": snap.total_net_assets_usd,
        "shares_outstanding": snap.shares_outstanding,
        "source_endpoint": source_endpoint,
        "fetched_at": fetched_at,
        "ingest_run_id": ingest_run_id,
    }


def collect(
    config_path: Path = DEFAULT_WATCHLIST_PATH,
    *,
    http: HttpClient | None = None,
) -> int:
    """Run the Bitwise collector once. Returns the meta.ingest_runs id.

    Iterates every watchlist ``etf_tickers`` entry with ``issuer == "Bitwise"``,
    fetches its product page from ``PRODUCT_URLS``, and upserts the parsed
    snapshot. A single fund's fetch/parse failure is soft — it's recorded as a
    partial endpoint and the run continues to the next fund, so one fund's
    site outage doesn't drop the others (the per-(slug, kind) soft-failure
    discipline the DefiLlama collector uses).
    """
    watchlist = load_watchlist(config_path)
    bitwise_etfs = [
        e for e in watchlist.etf_tickers if e.issuer.strip().lower() == ISSUER_FILTER.lower()
    ]
    if not bitwise_etfs:
        raise SystemExit(
            "watchlists.yml has no etf_tickers with issuer=Bitwise — nothing to fetch."
        )
    # PRODUCT_URLS defines v1 coverage. A watchlist Bitwise ticker without a
    # pinned URL (e.g. ETHW before its page is verified) is simply out of
    # scope — not a failure — so it's logged once at INFO and excluded, never
    # recorded as a partial-endpoint (which would flag the daily run as
    # perpetually degraded over work that hasn't been built yet).
    covered = [e for e in bitwise_etfs if e.ticker.upper() in PRODUCT_URLS]
    uncovered = [e.ticker for e in bitwise_etfs if e.ticker.upper() not in PRODUCT_URLS]
    if uncovered:
        LOGGER.info(
            "bitwise: %s watchlist ticker(s) not yet covered (no pinned product URL): %s",
            len(uncovered),
            uncovered,
        )
    if not covered:
        raise SystemExit(
            "No Bitwise watchlist ticker has a pinned product URL in PRODUCT_URLS — "
            f"nothing to fetch (watchlist Bitwise tickers: {[e.ticker for e in bitwise_etfs]})."
        )

    owns_http = http is None
    if http is None:
        http = HttpClient(
            SOURCE_NAME,
            rate_limit=DEFAULT_RATE_LIMIT,
            user_agent=_BROWSER_UA,
        )

    try:
        with db.ingest_run(
            SOURCE_NAME,
            endpoint=COLLECT_ENDPOINT_LABEL,
            metadata={"watchlist_etfs": [e.ticker for e in bitwise_etfs]},
        ) as run:
            snapshots: list[tuple[_FundSnapshot, str, datetime]] = []
            partials: list[dict[str, str]] = []
            for entry in covered:
                ticker = entry.ticker.upper()
                url = PRODUCT_URLS[ticker]
                try:
                    html = http.get_text(url)
                    fetched_at = datetime.now(timezone.utc)
                except (
                    httpx.TimeoutException,
                    httpx.NetworkError,
                    httpx.HTTPStatusError,
                ) as exc:
                    LOGGER.error("bitwise %s: fetch failed: %s", ticker, exc)
                    partials.append(
                        {
                            "name": f"{COLLECT_ENDPOINT_LABEL}:{ticker}",
                            "url": url,
                            "error": str(exc),
                        }
                    )
                    continue

                # raw_blobs JSON-serializes its payload; HTML must be wrapped
                # in a single-key object (db.store_raw_blob convention).
                db.store_raw_blob(
                    run.id,
                    f"{COLLECT_ENDPOINT_LABEL}:{ticker}",
                    url,
                    {"html": html},
                )
                snap = parse_snapshot(html, ticker=ticker, watchlist_entry=entry)
                if snap is None:
                    partials.append(
                        {
                            "name": f"{COLLECT_ENDPOINT_LABEL}:{ticker}",
                            "url": url,
                            "error": "no usable snapshot parsed from product page",
                        }
                    )
                    continue
                snapshots.append((snap, url, fetched_at))

            if partials:
                db.record_partial_endpoints(run.id, partials)

            if not snapshots:
                LOGGER.warning(
                    "bitwise: no usable snapshots for %s",
                    [e.ticker for e in covered],
                )
                run.add_rows(0)
                raise RuntimeError(
                    "Bitwise fetch/parse failed for every covered ETF; "
                    "see meta.ingest_runs.metadata.partial_endpoints for details."
                )

            rows = [
                _snapshot_to_row(
                    snap,
                    ingest_run_id=run.id,
                    source_endpoint=url,
                    fetched_at=fetched_at,
                )
                for snap, url, fetched_at in snapshots
            ]
            with db.connection() as conn:
                written = db.bulk_upsert(
                    conn,
                    "etf.fund_snapshots",
                    rows,
                    conflict_keys=("ticker", "snapshot_date"),
                )
            run.add_rows(written)
            LOGGER.info(
                "bitwise: +%s rows (%s snapshots, tickers=%s)",
                written,
                len(snapshots),
                [s.ticker for s, _, _ in snapshots],
            )
            return run.id
    finally:
        if owns_http:
            http.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI flags for the collector entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Collect Bitwise spot crypto ETF daily snapshots into etf.fund_snapshots."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_WATCHLIST_PATH,
        help="Watchlist path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the collector from ``python -m genkei.ingest.bitwise``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    run_id = collect(args.config)
    print(f"Bitwise collector wrote ingest_run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
