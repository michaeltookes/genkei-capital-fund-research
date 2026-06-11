"""Shared watchlist loader for ingesters and CLI subcommands.

Loads the package's default watchlist and exposes lookup helpers that map a
user-facing ticker (BTC, AAPL, …) to per-source identifiers (coingecko_id,
cik, FRED series_id). Single source of truth: ingest collectors and CLI
subcommands both read from here so a fix propagates to every reader.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "watchlists.yml"
SleeveKind = Literal["crypto", "equity", "macro", "protocol", "filer"]


@dataclass(frozen=True)
class CryptoEntry:
    """A crypto asset configured for market-data collection and research routing."""

    symbol: str
    name: str
    coingecko_id: str
    tier: str  # primary | secondary
    sleeve: str | None = None  # core | tactical
    # Coinbase Exchange product identifier (B-035). Optional because not
    # every future coin will list on Coinbase; absent → Coinbase ingester
    # skips this coin with a logged note.
    coinbase_product: str | None = None


@dataclass(frozen=True)
class EquityEntry:
    """A public equity target configured for SEC and Yahoo-driven workflows."""

    symbol: str
    name: str
    cik: str | None
    tier: str  # primary | secondary
    sleeve: str = "core"
    # 9-character SEC CUSIP — the natural join key for 13F holdings
    # (B-080). Optional because the watchlist gets populated incrementally
    # as the 13F crowding monitor needs each ticker; entries without a
    # CUSIP still work for everything else (Form 4, 8-K event study, etc.).
    cusip: str | None = None
    # Free-text sector classification (e.g. "Enterprise software",
    # "Semiconductors", "Banking"). Originally informational only; B-112
    # promoted it to the peer-routing input for the equity rel-strength
    # emitter (tech-comp sectors → QQQ; everything else → SPY). The YAML
    # has carried this field for every equity entry since the watchlist
    # was first populated; B-112 connects it through to the dataclass.
    sector: str | None = None


@dataclass(frozen=True)
class MacroEntry:
    """A macroeconomic time series configured for cross-sleeve context."""

    series_id: str
    name: str
    tier: str = "primary"
    sleeve: str = "cross-sleeve"
    rationale: str | None = None


@dataclass(frozen=True)
class BeaSeriesEntry:
    """A BEA NIPA line we want as a macro time-series target (B-029).

    ``series_id`` is the canonical composite key
    ``<table_id>:<line_number>:<frequency>`` (e.g. ``T10101:1:Q`` for
    Real GDP % change). It's also the PK in ``bea.series`` /
    ``bea.observations``.

    ``frequency`` is the BEA-side cadence we want this line at: ``Q``
    (quarterly), ``A`` (annual), or ``M`` (monthly — only a few NIPA
    lines support this). BEA returns the same line at different
    cadences depending on the request, so a watchlist entry binds the
    pair we actually pull.

    ``rationale`` mirrors ``MacroEntry`` — a one-line "why is this in
    the lake?" that surfaces in research sessions.
    """

    table_id: str
    line_number: int
    name: str
    frequency: str  # 'Q' | 'A' | 'M'
    tier: str = "primary"
    sleeve: str = "cross-sleeve"
    rationale: str | None = None

    @property
    def series_id(self) -> str:
        return f"{self.table_id}:{self.line_number}:{self.frequency}"


@dataclass(frozen=True)
class ProtocolEntry:
    """A DefiLlama protocol slug we want per-protocol TVL history for (B-081).

    ``coingecko_id`` (B-062) optionally pairs the protocol with its governance /
    fee token so cross-source experiments can join ``defillama.protocol_fees``
    against ``coingecko.market_data``. Multiple protocols may share one token
    (Chainlink staking + Chainlink requests both map to ``chainlink``/LINK).
    Unset when the protocol has no tradable token or no mapping yet.
    """

    slug: str
    name: str
    category: str | None  # Lending / DEX / Oracle / Liquid Staking / etc.
    tier: str  # primary | secondary
    rationale: str | None = None
    coingecko_id: str | None = None


@dataclass(frozen=True)
class BenchmarkEntry:
    """A market-benchmark price target we want OHLCV for (B-102).

    Distinct from ``EquityEntry`` because benchmarks (SPY, QQQ, IWM) are
    *not* research targets — they're comparators for the stack-outcome
    backtest (B-101) and the future correlator benchmark adjustment
    (B-100). Listing them under ``equities:`` would route them into
    every equity-targeted analysis (insider clusters, 8-K events,
    crowding) by accident.

    Stored in the same ``yahoo.candles`` table as the watchlist
    equities — the schema is just ``(ticker, ts, …)`` and doesn't care
    about asset-class semantics, so the only mechanical difference is
    which watchlist section the ingester reads to know what to fetch.
    """

    symbol: str  # Yahoo ticker (SPY, QQQ, IWM)
    name: str
    role: str  # human-readable purpose ("S&P 500 ETF — equity-core baseline")
    asset_class: str = "equity_index_etf"


@dataclass(frozen=True)
class FilerEntry:
    """A SEC 13F filer (institutional investment manager) we want to track (B-080)."""

    filer_cik: str  # zero-padded 10-char to match sec.filers.filer_cik
    name: str
    tier: str  # primary | secondary
    rationale: str | None = None


@dataclass(frozen=True)
class EtfTickerEntry:
    """A US spot crypto ETF we track via Yahoo OHLCV (B-105).

    Pivoted from B-105's original Farside-scrape spec on 2026-06-03
    after Farside + SoSoValue both Cloudflare-walled scripted access.
    The Yahoo chart endpoint serves each ETF cleanly and the existing
    ``genkei.ingest.yahoo`` collector lands rows in ``yahoo.candles``.

    ``asset`` is the underlying (``BTC`` or ``ETH``) used to route
    per-asset aggregations in ``genkei etf-flows --asset BTC``.
    ``launch_date`` is the spot-ETF launch date used by the CLI query
    layer to ignore pre-conversion Yahoo history for tickers that existed
    before their spot ETF wrapper.
    """

    ticker: str
    name: str
    asset: str  # 'BTC' | 'ETH'
    issuer: str
    sleeve: str = "tactical"
    launch_date: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class CotMarketEntry:
    """A CFTC-regulated futures market we ingest position data for (B-031).

    ``code`` is the CFTC contract market code — the natural join key
    in the upstream Socrata API and the primary identifier in
    ``cftc.cot_reports.market_code``. ``symbol`` is the Genkei alias
    used in the ``genkei cot --market`` CLI flag and in horizon tags.

    ``report_type`` selects which CFTC publication to read for this
    market: ``tff`` (financial futures — BTC, ETH, ES, NQ, FX, rates;
    has Asset Manager / Leveraged Funds breakdown) or
    ``disaggregated`` (commodities — gold, oil, ag, livestock; has
    Managed Money / Swap Dealer / Producer Merchant breakdown).
    """

    code: str
    symbol: str
    name: str
    report_type: str  # 'tff' | 'disaggregated'
    sleeve: str  # e.g. 'crypto:core', 'macro'
    rationale: str | None = None


@dataclass(frozen=True)
class EthWhaleAddressEntry:
    """An ETH wallet we snapshot daily for whale-flow tracking (B-106).

    ``address`` is normalized to lowercase 0x-prefixed hex (checksum-
    insensitive); the collector compares against Etherscan responses
    which return lowercase addresses. ``category`` is one of
    ``exchange`` / ``custodian`` / ``foundation`` / ``whale`` — see
    ``docs/sources/eth-whale-addresses.md`` for the curation
    methodology and how each category's flow sign should be read
    (exchange inflow = sell pressure, etc.).
    """

    address: str
    label: str
    category: str  # 'exchange' | 'custodian' | 'foundation' | 'whale'
    notes: str | None = None


@dataclass(frozen=True)
class EiaSeriesEntry:
    """An EIA Open Data v2 series we want as an energy time-series target (B-032).

    Each entry binds a friendly ``series_id`` (e.g. ``WTI_SPOT``,
    ``CRUDE_INV_EXSPR``, ``HH_SPOT``) to one EIA v2 ``route`` plus the
    ``facets`` that select this specific series within that route.

    EIA v2 organizes data by route (``petroleum/stoc/wstk``,
    ``natural-gas/stor/wkly``, ``electricity/electric-power-operational-data``)
    and exposes per-route facet filters. Most legacy time series live under
    a ``series`` facet (e.g. ``WCESTUS1`` for weekly US ex-SPR crude
    inventories). Some routes — notably electricity — require multiple
    facet keys (``fueltype`` + ``location`` + ``sectorid``) to pin a
    single conceptual series. ``facets`` carries whatever (key, value)
    pairs the route requires.

    ``data_field`` is the EIA v2 column we extract per row. Most series
    use ``value``; electricity operational data uses ``generation``;
    other routes may use ``price``, ``stocks``, etc.

    ``date_field`` defaults to ``period`` — every EIA v2 route returns
    the period under that field. It's explicit so future routes with
    non-standard date fields can override.

    ``frequency`` is the cadence of this series at the source: ``D``
    (daily), ``W`` (weekly), ``M`` (monthly), ``Q`` (quarterly), or
    ``A`` (annual). EIA accepts these as the ``frequency`` query param.
    Locked per series — same underlying series at a different cadence
    would be a separate watchlist entry with a distinct ``series_id``.
    """

    series_id: str
    name: str
    route: str
    frequency: str  # 'D' | 'W' | 'M' | 'Q' | 'A'
    data_field: str = "value"
    date_field: str = "period"
    facets: Mapping[str, str] = dataclasses.field(default_factory=dict)
    units: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class TreasurySeriesEntry:
    """A Treasury Fiscal Data series we want as a public-debt / cash / cost-
    of-debt time-series target (B-030).

    Each entry binds a friendly ``series_id`` (e.g. ``TOTAL_PUBLIC_DEBT``)
    to one Fiscal Data API endpoint, one ``value_field`` to extract per
    row, and an optional ``row_filter`` for endpoints that return
    multiple rows per record_date (e.g. ``operating_cash_balance``
    returns one row per ``account_type``; the watchlist filter picks
    the specific account/value field we want).

    ``aggregate`` is intentionally narrow: ``sum`` means combine all
    matching rows for one ``(series_id, ts)`` observation. Leave unset
    for the normal "last matching row wins" dedupe behavior.

    ``date_field`` defaults to ``record_date`` — every Fiscal Data
    endpoint we use in v1 publishes the period under that field. It's
    kept explicit so future endpoints with non-standard date fields
    can override.

    ``frequency`` is the cadence of this series at the source: ``D``
    (daily), ``W`` (weekly), ``M`` (monthly), ``Q`` (quarterly), or
    ``A`` (annual). Locked per series — same underlying line at a
    different cadence would be a separate watchlist entry with a
    distinct ``series_id``.
    """

    series_id: str
    name: str
    endpoint: str
    value_field: str
    frequency: str  # 'D' | 'W' | 'M' | 'Q' | 'A'
    date_field: str = "record_date"
    row_filter: Mapping[str, str] = dataclasses.field(default_factory=dict)
    aggregate: Literal["sum"] | None = None
    units: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class Watchlist:
    """Typed watchlist data with convenience lookups by source identifier."""

    crypto: list[CryptoEntry]
    equities: list[EquityEntry]
    macro: list[MacroEntry]
    protocols: list[ProtocolEntry]
    filers: list[FilerEntry]
    benchmarks: list[BenchmarkEntry] = dataclasses.field(default_factory=list)
    cot_markets: list[CotMarketEntry] = dataclasses.field(default_factory=list)
    etf_tickers: list[EtfTickerEntry] = dataclasses.field(default_factory=list)
    eth_whale_addresses: list[EthWhaleAddressEntry] = dataclasses.field(
        default_factory=list
    )
    bea: list[BeaSeriesEntry] = dataclasses.field(default_factory=list)
    treasury: list[TreasurySeriesEntry] = dataclasses.field(default_factory=list)
    eia: list[EiaSeriesEntry] = dataclasses.field(default_factory=list)

    def find_crypto(self, symbol: str) -> CryptoEntry | None:
        """Lookup a crypto entry by symbol (case-insensitive)."""
        upper = symbol.upper()
        for entry in self.crypto:
            if entry.symbol.upper() == upper:
                return entry
        return None

    def find_equity(self, symbol: str) -> EquityEntry | None:
        """Lookup an equity entry by ticker symbol (case-insensitive)."""
        upper = symbol.upper()
        for entry in self.equities:
            if entry.symbol.upper() == upper:
                return entry
        return None

    def find_equity_by_cusip(self, cusip: str) -> EquityEntry | None:
        """Reverse lookup: CUSIP → EquityEntry (or None if not mapped)."""
        if not cusip:
            return None
        target = cusip.strip().upper()
        for entry in self.equities:
            if entry.cusip and entry.cusip.upper() == target:
                return entry
        return None

    def find_macro(self, series_id: str) -> MacroEntry | None:
        """Lookup a macro series by FRED series id (case-insensitive)."""
        lower = series_id.lower()
        for entry in self.macro:
            if entry.series_id.lower() == lower:
                return entry
        return None

    def find_bea(self, series_id: str) -> BeaSeriesEntry | None:
        """Lookup a BEA series by ``<table_id>:<line_number>:<frequency>`` id."""
        upper = series_id.strip().upper()
        for entry in self.bea:
            if entry.series_id.upper() == upper:
                return entry
        return None

    def find_treasury(self, series_id: str) -> TreasurySeriesEntry | None:
        """Lookup a Treasury series by friendly id (case-insensitive)."""
        if not series_id:
            return None
        upper = series_id.strip().upper()
        for entry in self.treasury:
            if entry.series_id.upper() == upper:
                return entry
        return None

    def find_eia(self, series_id: str) -> EiaSeriesEntry | None:
        """Lookup an EIA series by friendly id (case-insensitive)."""
        if not series_id:
            return None
        upper = series_id.strip().upper()
        for entry in self.eia:
            if entry.series_id.upper() == upper:
                return entry
        return None

    def find_protocol(self, slug: str) -> ProtocolEntry | None:
        """Lookup a protocol entry by DefiLlama slug (case-insensitive)."""
        lower = slug.lower()
        for entry in self.protocols:
            if entry.slug.lower() == lower:
                return entry
        return None

    def find_benchmark(self, symbol: str) -> BenchmarkEntry | None:
        """Lookup a benchmark by ticker (case-insensitive)."""
        upper = symbol.upper()
        for entry in self.benchmarks:
            if entry.symbol.upper() == upper:
                return entry
        return None

    def find_cot_market(self, identifier: str) -> CotMarketEntry | None:
        """Lookup a COT market by symbol (BTC) or by CFTC market code (133741)."""
        if not identifier:
            return None
        stripped = identifier.strip()
        upper = stripped.upper()
        for entry in self.cot_markets:
            if entry.symbol.upper() == upper or entry.code.upper() == upper:
                return entry
        return None

    def find_etf_ticker(self, ticker: str) -> EtfTickerEntry | None:
        """Lookup a spot ETF entry by ticker (case-insensitive)."""
        if not ticker:
            return None
        upper = ticker.strip().upper()
        for entry in self.etf_tickers:
            if entry.ticker.upper() == upper:
                return entry
        return None

    def etfs_for_asset(self, asset: str) -> list[EtfTickerEntry]:
        """Return all configured ETFs that track the given underlying (BTC / ETH)."""
        if not asset:
            return []
        upper = asset.strip().upper()
        return [e for e in self.etf_tickers if e.asset.upper() == upper]

    def find_filer(self, identifier: str) -> FilerEntry | None:
        """Lookup a filer by CIK (with or without zero-padding) or by exact name.

        CIK match is the primary path — `find_filer("1067983")` and
        `find_filer("0001067983")` both return Berkshire. Name match is a
        secondary path supporting case-insensitive exact match of the
        watchlist `name` field; the CLI uses it for `--filer "Berkshire …"`.
        """
        if not identifier:
            return None
        stripped = identifier.strip()
        if stripped.isdigit():
            padded = stripped.zfill(10)
            for entry in self.filers:
                if entry.filer_cik == padded:
                    return entry
            return None
        lowered = stripped.lower()
        for entry in self.filers:
            if entry.name.lower() == lowered:
                return entry
        return None

    def classify(self, symbol_or_series: str) -> SleeveKind | None:
        """Identify the label's sleeve. None if unknown."""
        if self.find_crypto(symbol_or_series) is not None:
            return "crypto"
        if self.find_equity(symbol_or_series) is not None:
            return "equity"
        if self.find_macro(symbol_or_series) is not None:
            return "macro"
        if self.find_protocol(symbol_or_series) is not None:
            return "protocol"
        if self.find_filer(symbol_or_series) is not None:
            return "filer"
        return None


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> Watchlist:
    """Read a watchlist YAML file into a typed ``Watchlist``."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Watchlist file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Watchlist root must be a YAML mapping.")

    crypto: list[CryptoEntry] = []
    crypto_root = data.get("crypto", {})
    if isinstance(crypto_root, dict):
        for tier_name, entries in crypto_root.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                symbol = entry.get("symbol")
                cgid = entry.get("coingecko_id")
                name = entry.get("name")
                if not isinstance(symbol, str) or not isinstance(cgid, str):
                    continue
                crypto.append(
                    CryptoEntry(
                        symbol=symbol,
                        name=str(name or ""),
                        coingecko_id=cgid,
                        tier=str(tier_name),
                        sleeve=_optional_string(entry.get("sleeve")),
                        coinbase_product=_optional_string(entry.get("coinbase_product")),
                    )
                )

    equities: list[EquityEntry] = []
    equities_root = data.get("equities", {})
    if isinstance(equities_root, dict):
        for tier_name, entries in equities_root.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                symbol = entry.get("symbol")
                if not isinstance(symbol, str):
                    continue
                raw_cik = entry.get("cik")
                if isinstance(raw_cik, bool):
                    cik: str | None = None
                elif isinstance(raw_cik, int):
                    cik = str(raw_cik)
                elif isinstance(raw_cik, str):
                    stripped_cik = raw_cik.strip()
                    cik = stripped_cik if stripped_cik else None
                else:
                    cik = None
                raw_cusip = entry.get("cusip")
                cusip: str | None
                if isinstance(raw_cusip, str):
                    stripped_cusip = raw_cusip.strip()
                    cusip = stripped_cusip.upper() if stripped_cusip else None
                else:
                    cusip = None
                equities.append(
                    EquityEntry(
                        symbol=symbol,
                        name=str(entry.get("name") or ""),
                        cik=cik,
                        tier=str(tier_name),
                        sleeve=str(entry.get("sleeve") or "core"),
                        cusip=cusip,
                        sector=_optional_string(entry.get("sector")),
                    )
                )

    macro: list[MacroEntry] = []
    macro_root = data.get("macro_series", [])
    if isinstance(macro_root, list):
        for entry in macro_root:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("id")
            if not isinstance(sid, str):
                continue
            macro.append(
                MacroEntry(
                    series_id=sid,
                    name=str(entry.get("name") or ""),
                    tier=str(entry.get("tier") or "primary"),
                    sleeve=str(entry.get("sleeve") or "cross-sleeve"),
                    rationale=_optional_string(entry.get("rationale")),
                )
            )

    protocols: list[ProtocolEntry] = []
    protocols_root = data.get("protocols", {})
    if isinstance(protocols_root, dict):
        for tier_name, entries in protocols_root.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                slug = entry.get("slug")
                if not isinstance(slug, str) or not slug:
                    continue
                protocols.append(
                    ProtocolEntry(
                        slug=slug,
                        name=str(entry.get("name") or ""),
                        category=_optional_string(entry.get("category")),
                        tier=str(tier_name),
                        rationale=_optional_string(entry.get("rationale")),
                        coingecko_id=_optional_string(entry.get("coingecko_id")),
                    )
                )

    filers: list[FilerEntry] = []
    filers_root = data.get("filers", {})
    if isinstance(filers_root, dict):
        seen_filer_ciks: set[str] = set()
        for tier_name, entries in filers_root.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_cik = entry.get("cik")
                padded_cik = _normalize_filer_cik(raw_cik)
                if padded_cik is None:
                    continue
                if padded_cik in seen_filer_ciks:
                    # Silently dedupe — the watchlist file should be the
                    # source of truth and we don't want one cut-and-paste
                    # accident to FK-violate ingestion.
                    continue
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    continue
                seen_filer_ciks.add(padded_cik)
                filers.append(
                    FilerEntry(
                        filer_cik=padded_cik,
                        name=name,
                        tier=str(tier_name),
                        rationale=_optional_string(entry.get("rationale")),
                    )
                )

    benchmarks: list[BenchmarkEntry] = []
    benchmarks_root = data.get("benchmarks", [])
    if isinstance(benchmarks_root, list):
        seen_benchmark_symbols: set[str] = set()
        for entry in benchmarks_root:
            if not isinstance(entry, dict):
                continue
            symbol = entry.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                continue
            upper = symbol.upper()
            if upper in seen_benchmark_symbols:
                # Silently dedupe — same rule as filers.
                continue
            seen_benchmark_symbols.add(upper)
            benchmarks.append(
                BenchmarkEntry(
                    symbol=symbol,
                    name=str(entry.get("name") or ""),
                    role=str(entry.get("role") or ""),
                    asset_class=str(entry.get("asset_class") or "equity_index_etf"),
                )
            )

    cot_markets: list[CotMarketEntry] = []
    cot_root = data.get("cot_markets", [])
    if isinstance(cot_root, list):
        seen_cot_codes: set[str] = set()
        for entry in cot_root:
            if not isinstance(entry, dict):
                continue
            raw_code = entry.get("code")
            if isinstance(raw_code, int):
                code = str(raw_code)
            elif isinstance(raw_code, str):
                code = raw_code.strip()
            else:
                continue
            if not code:
                continue
            symbol = entry.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                continue
            report_type = entry.get("report_type")
            if not isinstance(report_type, str) or report_type not in (
                "tff",
                "disaggregated",
            ):
                continue
            if code in seen_cot_codes:
                continue
            seen_cot_codes.add(code)
            cot_markets.append(
                CotMarketEntry(
                    code=code,
                    symbol=symbol,
                    name=str(entry.get("name") or ""),
                    report_type=report_type,
                    sleeve=str(entry.get("sleeve") or "macro"),
                    rationale=_optional_string(entry.get("rationale")),
                )
            )

    etf_tickers: list[EtfTickerEntry] = []
    etf_root = data.get("etf_tickers", [])
    if isinstance(etf_root, list):
        seen_etf_tickers: set[str] = set()
        for entry in etf_root:
            if not isinstance(entry, dict):
                continue
            raw_ticker = entry.get("ticker")
            if not isinstance(raw_ticker, str):
                continue
            ticker = raw_ticker.strip().upper()
            if not ticker:
                continue
            raw_asset = entry.get("asset")
            if not isinstance(raw_asset, str):
                continue
            asset = raw_asset.strip().upper()
            if asset not in ("BTC", "ETH"):
                # v1 only supports BTC + ETH spot ETFs; other assets get dropped.
                continue
            if ticker in seen_etf_tickers:
                continue
            seen_etf_tickers.add(ticker)
            # launch_date is YAML-parsed as a datetime.date when unquoted.
            # Keep it as ISO string so downstream code doesn't have to
            # special-case typing.
            raw_launch = entry.get("launch_date")
            if hasattr(raw_launch, "isoformat"):
                launch_str: str | None = raw_launch.isoformat()
            elif isinstance(raw_launch, str) and raw_launch.strip():
                launch_str = raw_launch.strip()
            else:
                launch_str = None
            raw_issuer = entry.get("issuer")
            if not isinstance(raw_issuer, str):
                continue
            issuer = raw_issuer.strip()
            if not issuer:
                continue
            sleeve = str(entry.get("sleeve") or "tactical").strip() or "tactical"
            etf_tickers.append(
                EtfTickerEntry(
                    ticker=ticker,
                    name=str(entry.get("name") or "").strip(),
                    asset=asset,
                    issuer=issuer,
                    sleeve=sleeve,
                    launch_date=launch_str,
                    rationale=_optional_string(entry.get("rationale")),
                )
            )

    eth_whale_addresses: list[EthWhaleAddressEntry] = []
    eth_whale_root = data.get("eth_whale_addresses", [])
    if isinstance(eth_whale_root, list):
        seen_eth_whale_addresses: set[str] = set()
        for entry in eth_whale_root:
            if not isinstance(entry, dict):
                raise ValueError(f"Invalid eth_whale_addresses entry: {entry!r}")
            raw_addr = entry.get("address")
            if not isinstance(raw_addr, str):
                raise ValueError(f"Invalid eth_whale_addresses.address: {raw_addr!r}")
            # Normalize to lowercase + 0x-prefix; Etherscan returns lowercase
            # so storing the canonical lowercase form keeps the (address, ts)
            # PK stable regardless of the YAML author's checksum-case choices.
            addr = raw_addr.strip().lower()
            try:
                int(addr[2:], 16)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid eth_whale_addresses.address: {raw_addr!r}"
                ) from exc
            if not addr.startswith("0x") or len(addr) != 42:
                raise ValueError(f"Invalid eth_whale_addresses.address: {raw_addr!r}")
            if addr in seen_eth_whale_addresses:
                continue
            seen_eth_whale_addresses.add(addr)
            raw_category = entry.get("category")
            if not isinstance(raw_category, str):
                raise ValueError(
                    f"Invalid eth_whale_addresses.category {raw_category!r} for {addr}"
                )
            category = raw_category.strip().lower()
            if category not in ("exchange", "custodian", "foundation", "whale"):
                raise ValueError(
                    f"Invalid eth_whale_addresses.category {raw_category!r} for {addr}"
                )
            label = str(entry.get("label") or "").strip() or addr
            eth_whale_addresses.append(
                EthWhaleAddressEntry(
                    address=addr,
                    label=label,
                    category=category,
                    notes=_optional_string(entry.get("notes")),
                )
            )

    bea: list[BeaSeriesEntry] = []
    bea_root = data.get("bea", [])
    if isinstance(bea_root, list):
        seen_bea_keys: set[str] = set()
        for entry in bea_root:
            if not isinstance(entry, dict):
                continue
            table_id = entry.get("table_id")
            raw_line = entry.get("line_number")
            if not isinstance(table_id, str) or not table_id.strip():
                continue
            try:
                line_number = int(raw_line)
            except (TypeError, ValueError):
                continue
            if line_number < 0:
                continue
            frequency_raw = entry.get("frequency", "Q")
            if not isinstance(frequency_raw, str):
                continue
            frequency = frequency_raw.strip().upper()
            if frequency not in ("Q", "A", "M"):
                continue
            normalized_table = table_id.strip().upper()
            key = f"{normalized_table}:{line_number}:{frequency}"
            if key in seen_bea_keys:
                continue
            seen_bea_keys.add(key)
            bea.append(
                BeaSeriesEntry(
                    table_id=normalized_table,
                    line_number=line_number,
                    name=str(entry.get("name") or ""),
                    frequency=frequency,
                    tier=str(entry.get("tier") or "primary"),
                    sleeve=str(entry.get("sleeve") or "cross-sleeve"),
                    rationale=_optional_string(entry.get("rationale")),
                )
            )

    treasury: list[TreasurySeriesEntry] = []
    treasury_root = data.get("treasury", [])
    if isinstance(treasury_root, list):
        seen_treasury_ids: set[str] = set()
        for entry in treasury_root:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("series_id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                continue
            series_id = raw_id.strip().upper()
            if series_id in seen_treasury_ids:
                continue
            endpoint = entry.get("endpoint")
            value_field = entry.get("value_field")
            if not isinstance(endpoint, str) or not endpoint.strip():
                continue
            if not isinstance(value_field, str) or not value_field.strip():
                continue
            frequency_raw = entry.get("frequency", "D")
            if not isinstance(frequency_raw, str):
                continue
            frequency = frequency_raw.strip().upper()
            if frequency not in ("D", "W", "M", "Q", "A"):
                continue
            date_field_raw = entry.get("date_field", "record_date")
            date_field = (
                date_field_raw.strip()
                if isinstance(date_field_raw, str) and date_field_raw.strip()
                else "record_date"
            )
            raw_filter = entry.get("row_filter", {})
            row_filter: dict[str, str] = {}
            if isinstance(raw_filter, dict):
                for key, value in raw_filter.items():
                    if not isinstance(key, str) or not key:
                        continue
                    if isinstance(value, bool) or value is None:
                        continue
                    row_filter[key] = str(value)
            raw_aggregate = entry.get("aggregate")
            aggregate: Literal["sum"] | None = None
            if raw_aggregate is not None:
                if not isinstance(raw_aggregate, str):
                    continue
                normalized_aggregate = raw_aggregate.strip().lower()
                if normalized_aggregate != "sum":
                    continue
                aggregate = "sum"
            seen_treasury_ids.add(series_id)
            treasury.append(
                TreasurySeriesEntry(
                    series_id=series_id,
                    name=str(entry.get("name") or ""),
                    endpoint=endpoint.strip(),
                    value_field=value_field.strip(),
                    frequency=frequency,
                    date_field=date_field,
                    row_filter=row_filter,
                    aggregate=aggregate,
                    units=_optional_string(entry.get("units")),
                    rationale=_optional_string(entry.get("rationale")),
                )
            )

    eia: list[EiaSeriesEntry] = []
    eia_root = data.get("eia", [])
    if isinstance(eia_root, list):
        seen_eia_ids: set[str] = set()
        for entry in eia_root:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("series_id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                continue
            series_id = raw_id.strip().upper()
            if series_id in seen_eia_ids:
                continue
            route = entry.get("route")
            if not isinstance(route, str) or not route.strip():
                continue
            frequency_raw = entry.get("frequency", "D")
            if not isinstance(frequency_raw, str):
                continue
            frequency = frequency_raw.strip().upper()
            if frequency not in ("D", "W", "M", "Q", "A"):
                continue
            data_field_raw = entry.get("data_field", "value")
            data_field = (
                data_field_raw.strip()
                if isinstance(data_field_raw, str) and data_field_raw.strip()
                else "value"
            )
            date_field_raw = entry.get("date_field", "period")
            date_field = (
                date_field_raw.strip()
                if isinstance(date_field_raw, str) and date_field_raw.strip()
                else "period"
            )
            raw_facets = entry.get("facets", {})
            facets: dict[str, str] = {}
            if isinstance(raw_facets, dict):
                for key, value in raw_facets.items():
                    if not isinstance(key, str) or not key:
                        continue
                    if isinstance(value, bool) or value is None:
                        continue
                    facets[key] = str(value)
            seen_eia_ids.add(series_id)
            eia.append(
                EiaSeriesEntry(
                    series_id=series_id,
                    name=str(entry.get("name") or ""),
                    route=route.strip(),
                    frequency=frequency,
                    data_field=data_field,
                    date_field=date_field,
                    facets=facets,
                    units=_optional_string(entry.get("units")),
                    rationale=_optional_string(entry.get("rationale")),
                )
            )

    return Watchlist(
        crypto=crypto,
        equities=equities,
        macro=macro,
        protocols=protocols,
        filers=filers,
        benchmarks=benchmarks,
        cot_markets=cot_markets,
        etf_tickers=etf_tickers,
        eth_whale_addresses=eth_whale_addresses,
        bea=bea,
        treasury=treasury,
        eia=eia,
    )


def _normalize_filer_cik(raw: object) -> str | None:
    """Zero-pad a numeric CIK to 10 chars; return None if not parseable."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        text = str(raw)
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        return None
    if not text.isdigit() or len(text) > 10:
        return None
    return text.zfill(10)


def _optional_string(value: object) -> str | None:
    """Return a non-empty string value or None for absent optional fields."""
    return value if isinstance(value, str) and value else None
