"""Shared watchlist resolution for CLI subcommands.

Loads ``config/watchlists.yml`` and exposes lookup helpers that map a
user-facing ticker (BTC, AAPL, …) to per-source identifiers (coingecko_id,
cik, FRED series_id). Centralized so every subcommand resolves tickers
the same way and a fix in one place propagates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import yaml

DEFAULT_WATCHLIST_PATH = Path("config/watchlists.yml")
SleeveKind = Literal["crypto", "equity", "macro"]


@dataclass(frozen=True)
class CryptoEntry:
    symbol: str
    name: str
    coingecko_id: str
    tier: str  # primary | secondary


@dataclass(frozen=True)
class EquityEntry:
    symbol: str
    name: str
    cik: Optional[str]
    tier: str  # primary | secondary


@dataclass(frozen=True)
class MacroEntry:
    series_id: str
    name: str


@dataclass(frozen=True)
class Watchlist:
    crypto: list[CryptoEntry]
    equities: list[EquityEntry]
    macro: list[MacroEntry]

    def find_crypto(self, symbol: str) -> Optional[CryptoEntry]:
        upper = symbol.upper()
        for entry in self.crypto:
            if entry.symbol.upper() == upper:
                return entry
        return None

    def find_equity(self, symbol: str) -> Optional[EquityEntry]:
        upper = symbol.upper()
        for entry in self.equities:
            if entry.symbol.upper() == upper:
                return entry
        return None

    def find_macro(self, series_id: str) -> Optional[MacroEntry]:
        lower = series_id.lower()
        for entry in self.macro:
            if entry.series_id.lower() == lower:
                return entry
        return None

    def classify(self, symbol_or_series: str) -> Optional[SleeveKind]:
        """Identify whether a label is crypto / equity / macro. None if unknown."""
        if self.find_crypto(symbol_or_series) is not None:
            return "crypto"
        if self.find_equity(symbol_or_series) is not None:
            return "equity"
        if self.find_macro(symbol_or_series) is not None:
            return "macro"
        return None


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> Watchlist:
    """Read ``config/watchlists.yml`` into a typed ``Watchlist``."""
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
                cik = entry.get("cik")
                equities.append(
                    EquityEntry(
                        symbol=symbol,
                        name=str(entry.get("name") or ""),
                        cik=cik if isinstance(cik, str) and cik else None,
                        tier=str(tier_name),
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
            macro.append(MacroEntry(series_id=sid, name=str(entry.get("name") or "")))

    return Watchlist(crypto=crypto, equities=equities, macro=macro)
