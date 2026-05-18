"""Unit tests for ``genkei revenue-divergence`` (B-062)."""

from __future__ import annotations

import io
import json as json_mod
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.cli import main
from genkei.experiments.protocol_revenue import (
    FeeRevenuePoint,
    PricePoint,
)

PROTOCOL_YAML = """
protocols:
  primary:
    - slug: chainlink-requests
      name: Chainlink Requests
      category: Oracle
      coingecko_id: chainlink
    - slug: aave-v3
      name: Aave V3
      category: Lending
      coingecko_id: aave
    - slug: unmapped-protocol
      name: Some Unmapped Protocol
      category: DEX
"""


def _watchlist_path(case: unittest.TestCase, body: str = PROTOCOL_YAML) -> Path:
    ctx = TemporaryDirectory()
    case.addCleanup(ctx.cleanup)
    tmp = Path(ctx.name)
    path = tmp / "watchlists.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _fake_fees(days: int = 180, daily_fees: float = 1_000.0) -> list[FeeRevenuePoint]:
    start = date(2026, 1, 1)
    return [
        FeeRevenuePoint(
            ts=start + timedelta(days=i),
            fees_usd=Decimal(str(daily_fees)),
            revenue_usd=Decimal(str(daily_fees / 2)),
        )
        for i in range(days)
    ]


def _fake_prices(
    days: int = 180,
    *,
    price_now: float = 30.0,
    price_then: float = 10.0,
    mcap_now: float = 30_000_000.0,
    mcap_then: float = 10_000_000.0,
) -> list[PricePoint]:
    """Linear ramp from then→now so divergence reports get a clean signal."""
    start = date(2026, 1, 1)
    points: list[PricePoint] = []
    for i in range(days):
        frac = i / max(days - 1, 1)
        price = price_then + (price_now - price_then) * frac
        mcap = mcap_then + (mcap_now - mcap_then) * frac
        points.append(
            PricePoint(
                ts=start + timedelta(days=i),
                price_usd=Decimal(str(price)),
                market_cap_usd=Decimal(str(mcap)),
            )
        )
    return points


class CommandGuardsTests(unittest.TestCase):
    def test_since_after_until_rejected(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "revenue-divergence",
                    "--since",
                    "2026-06-01",
                    "--until",
                    "2026-01-01",
                ]
            )
        self.assertEqual(code, 2)

    def test_unknown_slug_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "revenue-divergence",
                    "--slug",
                    "nonexistent-slug",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("not in the watchlist", err.getvalue())

    def test_unmapped_protocol_rejected(self) -> None:
        path = _watchlist_path(self)
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(
                [
                    "revenue-divergence",
                    "--slug",
                    "unmapped-protocol",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("coingecko_id", err.getvalue())


class SingleSlugTests(unittest.TestCase):
    def test_single_slug_human_output(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.revenue_divergence.load_fee_series",
                return_value=_fake_fees(),
            ),
            patch(
                "genkei.cli.revenue_divergence.load_price_series",
                return_value=_fake_prices(price_now=30.0, price_then=10.0),
            ),
            redirect_stdout(out),
        ):
            code = main(
                [
                    "revenue-divergence",
                    "--slug",
                    "chainlink-requests",
                    "--config",
                    str(path),
                ]
            )
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("chainlink-requests", text)
        self.assertIn("chainlink", text)  # token mapping
        self.assertIn("divergence:", text)
        self.assertIn("P/F now:", text)

    def test_single_slug_json_payload_shape(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.revenue_divergence.load_fee_series",
                return_value=_fake_fees(),
            ),
            patch(
                "genkei.cli.revenue_divergence.load_price_series",
                return_value=_fake_prices(price_now=30.0, price_then=10.0),
            ),
            redirect_stdout(out),
        ):
            code = main(
                [
                    "revenue-divergence",
                    "--slug",
                    "chainlink-requests",
                    "--config",
                    str(path),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json_mod.loads(out.getvalue())
        self.assertEqual(payload["slug"], "chainlink-requests")
        self.assertEqual(payload["coingecko_id"], "chainlink")
        # Fees are flat, market cap ramps 10M → 30M → price-leads-up.
        self.assertEqual(payload["kind"], "price-leads-up")
        self.assertIn("pf_ratio_now", payload)
        self.assertNotIn("snapshots", payload)  # no --since → no series

    def test_single_slug_with_since_emits_snapshot_series(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.revenue_divergence.load_fee_series",
                return_value=_fake_fees(),
            ),
            patch(
                "genkei.cli.revenue_divergence.load_price_series",
                return_value=_fake_prices(),
            ),
            redirect_stdout(out),
        ):
            code = main(
                [
                    "revenue-divergence",
                    "--slug",
                    "chainlink-requests",
                    "--since",
                    "2026-01-01",
                    "--config",
                    str(path),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json_mod.loads(out.getvalue())
        self.assertIn("snapshots", payload)
        self.assertGreater(len(payload["snapshots"]), 0)
        self.assertIn("pf_ratio", payload["snapshots"][0])

    def test_price_leads_up_is_classified(self) -> None:
        path = _watchlist_path(self)
        # Price ramps 10→30 (+200%); fees flat → annualized revenue flat
        # → divergence: price-leads-up.
        out = io.StringIO()
        with (
            patch(
                "genkei.cli.revenue_divergence.load_fee_series",
                return_value=_fake_fees(daily_fees=1_000.0),
            ),
            patch(
                "genkei.cli.revenue_divergence.load_price_series",
                # Hold market cap flat for the first 90 days then ramp up so
                # the divergence shows clearly.
                return_value=_ramp_prices_after(90),
            ),
            redirect_stdout(out),
        ):
            code = main(
                [
                    "revenue-divergence",
                    "--slug",
                    "chainlink-requests",
                    "--config",
                    str(path),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json_mod.loads(out.getvalue())
        self.assertEqual(payload["kind"], "price-leads-up")


def _ramp_prices_after(flat_days: int, total_days: int = 180) -> list[PricePoint]:
    """Flat market cap for ``flat_days``, then a 3x ramp over the remainder."""
    start = date(2026, 1, 1)
    points: list[PricePoint] = []
    for i in range(total_days):
        if i < flat_days:
            mcap = 10_000_000.0
        else:
            frac = (i - flat_days) / max(total_days - flat_days - 1, 1)
            mcap = 10_000_000.0 + 20_000_000.0 * frac
        points.append(
            PricePoint(
                ts=start + timedelta(days=i),
                price_usd=Decimal("10"),
                market_cap_usd=Decimal(str(mcap)),
            )
        )
    return points


class AllProtocolsTableTests(unittest.TestCase):
    def test_default_iterates_only_mapped_protocols(self) -> None:
        path = _watchlist_path(self)
        out = io.StringIO()
        # Each protocol gets one fee + price pull; return synthetic data for
        # both calls. (The DB-load helpers are stubbed.)
        with (
            patch(
                "genkei.cli.revenue_divergence.load_fee_series",
                return_value=_fake_fees(),
            ),
            patch(
                "genkei.cli.revenue_divergence.load_price_series",
                return_value=_fake_prices(),
            ),
            redirect_stdout(out),
        ):
            code = main(["revenue-divergence", "--config", str(path), "--json"])
        self.assertEqual(code, 0)
        payload = json_mod.loads(out.getvalue())
        slugs = {row["slug"] for row in payload}
        self.assertEqual(slugs, {"chainlink-requests", "aave-v3"})  # unmapped one excluded
