"""Shared watchlist loader for ingesters and CLI subcommands.

Loads the package's default watchlist and exposes lookup helpers that map a
user-facing ticker (BTC, AAPL, …) to per-source identifiers (coingecko_id,
cik, FRED series_id). Single source of truth: ingest collectors and CLI
subcommands both read from here so a fix propagates to every reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "watchlists.yml"
SleeveKind = Literal["crypto", "equity", "macro", "protocol"]


@dataclass(frozen=True)
class CryptoEntry:
    symbol: str
    name: str
    coingecko_id: str
    tier: str  # primary | secondary
    sleeve: str | None = None  # core | tactical


@dataclass(frozen=True)
class EquityEntry:
    symbol: str
    name: str
    cik: str | None
    tier: str  # primary | secondary
    sleeve: str = "core"


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
class Watchlist:
    crypto: list[CryptoEntry]
    equities: list[EquityEntry]
    macro: list[MacroEntry]
    protocols: list[ProtocolEntry]

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

    def classify(self, symbol_or_series: str) -> SleeveKind | None:
        """Identify whether a label is crypto / equity / macro / protocol. None if unknown."""
        if self.find_crypto(symbol_or_series) is not None:
            return "crypto"
        if self.find_equity(symbol_or_series) is not None:
            return "equity"
        if self.find_macro(symbol_or_series) is not None:
            return "macro"
        if self.find_protocol(symbol_or_series) is not None:
            return "protocol"
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
                equities.append(
                    EquityEntry(
                        symbol=symbol,
                        name=str(entry.get("name") or ""),
                        cik=cik,
                        tier=str(tier_name),
                        sleeve=str(entry.get("sleeve") or "core"),
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

    return Watchlist(crypto=crypto, equities=equities, macro=macro, protocols=protocols)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
