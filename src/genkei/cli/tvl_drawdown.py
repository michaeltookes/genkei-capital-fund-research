"""``genkei tvl-drawdown`` — TVL drawdown early-warning experiment (B-058).

Thin CLI wrapper over ``genkei.experiments.tvl_drawdown``. Runs the
rule-based threshold classifier on each (chain, native token) pair
in the watchlist, evaluating label-safe train and test periods around
the default 2024-01-01 split.

Usage:
  genkei tvl-drawdown                            # all chain/product pairs, defaults
  genkei tvl-drawdown --chain Ethereum           # single chain
  genkei tvl-drawdown --drawdown 20              # 20% drawdown threshold
  genkei tvl-drawdown --forward 60               # 60-day forward window
  genkei tvl-drawdown --json                     # machine-readable
"""

import json
from datetime import date as date_type
from decimal import Decimal
from typing import Annotated, Any, Optional

import typer

from genkei.cli._helpers import json_default as _json_default
from genkei.cli._helpers import parse_date as _parse_date
from genkei.common.watchlist import Watchlist, load_watchlist
from genkei.experiments.tvl_drawdown import (
    DEFAULT_DRAWDOWN_THRESHOLD_PCT,
    DEFAULT_FORWARD_WINDOW_DAYS,
    DEFAULT_TRAIN_END,
    ClassifierResult,
    run_chain_evaluation,
)

DEFAULT_CHAIN_SYMBOLS = ("ETH", "SOL", "SUI")


def _chain_product_pairs(watchlist: Watchlist) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    by_symbol = {entry.symbol.upper(): entry for entry in watchlist.crypto}
    for symbol in DEFAULT_CHAIN_SYMBOLS:
        entry = by_symbol.get(symbol)
        if entry is not None and entry.coinbase_product:
            pairs.append((entry.name, entry.coinbase_product))
    return tuple(pairs)


def _bitcoin_pair(watchlist: Watchlist) -> Optional[tuple[str, str]]:
    entry = watchlist.find_crypto("BTC")
    if entry is None or not entry.coinbase_product:
        return None
    return (entry.name, entry.coinbase_product)


def _format_result(label: str, r: ClassifierResult) -> str:
    return (
        f"  {label:<6} {r.days_evaluated:>5} {float(r.base_rate_pct):>7.2f}%"
        f" {float(r.signal_rate_pct):>7.2f}% {float(r.precision_pct):>7.2f}%"
        f" {float(r.recall_pct):>6.2f}% {float(r.lift):>5.2f}x"
        f"   TP={r.true_positives} FP={r.false_positives}"
        f" TN={r.true_negatives} FN={r.false_negatives}"
    )


def _result_to_dict(label: str, r: ClassifierResult) -> dict[str, Any]:
    return {
        "period": label,
        "chain": r.chain,
        "product": r.product,
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "days_evaluated": r.days_evaluated,
        "base_rate_pct": r.base_rate_pct,
        "signal_rate_pct": r.signal_rate_pct,
        "precision_pct": r.precision_pct,
        "recall_pct": r.recall_pct,
        "lift": r.lift,
        "true_positives": r.true_positives,
        "false_positives": r.false_positives,
        "true_negatives": r.true_negatives,
        "false_negatives": r.false_negatives,
    }


def tvl_drawdown_cmd(
    chain: Annotated[
        Optional[str],
        typer.Option(
            "--chain",
            help="Restrict to one chain (Ethereum / Solana / Sui / Bitcoin).",
        ),
    ] = None,
    drawdown: Annotated[
        float,
        typer.Option(
            "--drawdown",
            help="Forward drawdown threshold (percent). Default 15.",
        ),
    ] = float(DEFAULT_DRAWDOWN_THRESHOLD_PCT),
    forward: Annotated[
        int,
        typer.Option(
            "--forward",
            help=f"Forward window in days. Default {DEFAULT_FORWARD_WINDOW_DAYS}.",
        ),
    ] = DEFAULT_FORWARD_WINDOW_DAYS,
    train_end: Annotated[
        Optional[str],
        typer.Option(
            "--train-end",
            help=(
                "Train/test split date (YYYY-MM-DD). Train = data ≤ this; "
                f"test = data > this. Default {DEFAULT_TRAIN_END.isoformat()}."
            ),
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Machine-readable JSON output."),
    ] = False,
) -> None:
    """Run the TVL drawdown early-warning experiment on each (chain, token) pair."""
    parsed_train_end: date_type = (
        _parse_date(train_end, label="--train-end") if train_end else DEFAULT_TRAIN_END
    )
    drawdown_decimal = Decimal(str(drawdown))

    watchlist = load_watchlist()
    pairs = _chain_product_pairs(watchlist)
    if chain is not None:
        # Tolerate case mismatch — chain names in the lake are
        # title-case ("Ethereum"); the CLI accepts any casing.
        match = next(
            (p for p in pairs if p[0].lower() == chain.lower()),
            None,
        )
        if match is None:
            # Bitcoin is intentionally excluded from defaults but the
            # data exists. Let users opt in.
            if chain.lower() == "bitcoin":
                match = _bitcoin_pair(watchlist)
            else:
                raise typer.BadParameter(
                    f"Unknown chain {chain!r}. Known: {', '.join(c for c, _ in pairs)}, Bitcoin."
                )
            if match is None:
                raise typer.BadParameter("Bitcoin has no coinbase_product in the watchlist.")
        pairs = (match,)

    rows: list[dict[str, Any]] = []
    human_lines: list[str] = []
    for ch, product in pairs:
        train, test = run_chain_evaluation(
            ch,
            product,
            train_end=parsed_train_end,
            forward_window_days=forward,
            drawdown_threshold_pct=drawdown_decimal,
        )
        rows.append(_result_to_dict("train", train))
        rows.append(_result_to_dict("test", test))
        if not json_output:
            human_lines.append(
                f"\n{ch} ({product}) — forward window {forward}d, "
                f"drawdown threshold {drawdown:.1f}%"
            )
            human_lines.append(
                f"  {'period':<6} {'days':>5} {'base':>8} {'signal':>8} {'precision':>8}"
                f" {'recall':>7} {'lift':>6}   confusion (TP/FP/TN/FN)"
            )
            human_lines.append(_format_result("train", train))
            human_lines.append(_format_result("test", test))

    if json_output:
        typer.echo(json.dumps(rows, default=_json_default, indent=2))
    else:
        typer.echo(
            f"TVL drawdown early-warning (B-058) — split {parsed_train_end.isoformat()}, "
            f"test > split"
        )
        typer.echo("=" * 84)
        for line in human_lines:
            typer.echo(line)
