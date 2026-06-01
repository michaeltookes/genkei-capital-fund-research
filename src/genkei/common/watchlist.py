"""Shared watchlist loader for ingesters and CLI subcommands.

Loads the package's default watchlist and exposes lookup helpers that map a
user-facing ticker (BTC, AAPL, …) to per-source identifiers (coingecko_id,
cik, FRED series_id). Single source of truth: ingest collectors and CLI
subcommands both read from here so a fix propagates to every reader.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "watchlists.yml"
SleeveKind = Literal["crypto", "equity", "macro", "protocol", "filer"]


@dataclass(frozen=True)
class CryptoEntry:
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


@dataclass(frozen=True)
class MacroEntry:
    series_id: str
    name: str
    tier: str = "primary"
    sleeve: str = "cross-sleeve"
    rationale: str | None = None


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
class Watchlist:
    crypto: list[CryptoEntry]
    equities: list[EquityEntry]
    macro: list[MacroEntry]
    protocols: list[ProtocolEntry]
    filers: list[FilerEntry]
    benchmarks: list[BenchmarkEntry] = dataclasses.field(default_factory=list)

    def find_crypto(self, symbol: str) -> CryptoEntry | None:
        upper = symbol.upper()
        for entry in self.crypto:
            if entry.symbol.upper() == upper:
                return entry
        return None

    def find_equity(self, symbol: str) -> EquityEntry | None:
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
        lower = series_id.lower()
        for entry in self.macro:
            if entry.series_id.lower() == lower:
                return entry
        return None

    def find_protocol(self, slug: str) -> ProtocolEntry | None:
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

    return Watchlist(
        crypto=crypto,
        equities=equities,
        macro=macro,
        protocols=protocols,
        filers=filers,
        benchmarks=benchmarks,
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
    return value if isinstance(value, str) and value else None
