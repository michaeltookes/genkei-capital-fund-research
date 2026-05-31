"""Shared test helpers for offline Genkei unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from genkei.common.watchlist import Watchlist, load_watchlist

DEFAULT_EQUITY_WATCHLIST_YAML = (
    "equities:\n"
    "  primary:\n"
    "    - symbol: AAPL\n"
    '      cik: "0000320193"\n'
    '      cusip: "037833100"\n'
    "      name: Apple Inc.\n"
    "    - symbol: NVDA\n"
    '      cik: "0001045810"\n'
    "      name: NVIDIA\n"
)


class FakeIngestRun:
    """Minimal context-manager stand-in for ``db.ingest_run`` tests."""

    def __init__(self, run_id: int = 42) -> None:
        self.id = run_id
        self.rows_added = 0

    def __enter__(self) -> FakeIngestRun:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def add_rows(self, rows: int) -> None:
        self.rows_added += rows


@contextmanager
def temporary_watchlist_path(
    body: str = DEFAULT_EQUITY_WATCHLIST_YAML,
) -> Iterator[Path]:
    """Write a watchlist YAML fixture and yield its temporary path."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "watchlists.yml"
        path.write_text(body, encoding="utf-8")
        yield path


def make_watchlist(body: str = DEFAULT_EQUITY_WATCHLIST_YAML) -> Watchlist:
    """Load a watchlist from an in-memory YAML fixture."""
    with temporary_watchlist_path(body) as path:
        return load_watchlist(path)
