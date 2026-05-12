"""Genkei CLI — typed subcommand surface over the data lake (Phase 3, B-037+).

The CLI is the canonical query layer. Every subcommand maps to a per-source
schema in Postgres (``defillama.*``, ``sec.*``, ``fred.*``, ``coingecko.*``)
or to ``config/watchlists.yml``. Each subcommand supports ``--json`` for
agent consumption + a human-readable default.

Conventions (D-019 in docs/architecture.md):
- Built on Typer (which sits on Click). Type-hint-driven matches the rest
  of the codebase; auto-generated help from docstrings + types.
- Every subcommand reads ``GENKEI_DATABASE_URL`` from the environment via
  the existing ``genkei.common.db`` helpers — the CLI is *not* its own
  configuration system.
- Subcommands that need a ticker resolve via the watchlist
  (``genkei.cli._watchlist``) so the same name (BTC, AAPL, …) works
  regardless of which underlying schema holds the data.
- Output: human-readable rich tables by default; JSON via ``--json``.
  JSON shape is one row per result with all columns; never wrap in an
  envelope (so jq pipes stay clean).

Subcommand surface (B-037):
- ``genkei prices``    crypto + (later) equity prices         [B-039 ✓]
- ``genkei filings``   SEC EDGAR filings                      [B-040, stub]
- ``genkei tvl``       DeFiLlama chain + protocol TVL         [B-041, stub]
- ``genkei macro``     FRED macro series                      [B-042, stub]
- ``genkei news``      GDELT news / events                    [B-043, stub]
- ``genkei watchlist`` Watchlist coverage / health            [B-044, stub]
- ``genkei query``     SQL escape hatch                       [B-045, stub]
"""

from __future__ import annotations

import sys

import typer

from genkei.cli import prices

app = typer.Typer(
    name="genkei",
    help="Genkei Capital research-desk CLI — query the data lake.",
    no_args_is_help=True,
    add_completion=False,
)

# Real subcommands export a callable; we register them as top-level commands
# so options bind correctly. Stub groups use the placeholder factory below.
app.command("prices", help="Asset prices (crypto today; equities later).")(prices.prices_cmd)


def _stub(group_name: str, item: str) -> typer.Typer:
    """Build a placeholder Typer sub-app for not-yet-implemented surfaces."""
    sub = typer.Typer(name=group_name, help=f"(not yet implemented — see backlog {item}).")

    @sub.callback(invoke_without_command=True)
    def _entry() -> None:
        typer.echo(
            f"`genkei {group_name}` is not yet implemented. See docs/backlog.md item {item}.",
            err=True,
        )
        raise typer.Exit(code=1)

    return sub


app.add_typer(_stub("filings", "B-040"), name="filings")
app.add_typer(_stub("tvl", "B-041"), name="tvl")
app.add_typer(_stub("macro", "B-042"), name="macro")
app.add_typer(_stub("news", "B-043"), name="news")
app.add_typer(_stub("watchlist", "B-044"), name="watchlist")
app.add_typer(_stub("query", "B-045"), name="query")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point invoked by ``genkei`` on the user's PATH.

    Lets Typer/Click run in default standalone mode (so error messages
    render with rich formatting), then catches ``SystemExit`` so callers
    can inspect the exit code rather than the process being killed.
    """
    try:
        app(args=argv if argv is not None else None)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
