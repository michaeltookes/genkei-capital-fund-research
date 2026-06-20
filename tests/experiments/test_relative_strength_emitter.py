"""Unit tests for the relative-strength → signal_events emitter (B-098)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.common.watchlist import load_watchlist
from genkei.experiments.emitters.relative_strength_emitter import (
    LAGGARD_THRESHOLD_PCT,
    LEADER_THRESHOLD_PCT,
    PEER_PRODUCT,
    PEER_SYMBOL,
    STRENGTH_SATURATION_PP,
    WINDOW_DAYS,
    Crossing,
    _build_event,
    _crypto_assets,
    _date_ts,
    _detect_crossings,
    _state_for,
    _strength_from_rel_strength,
    compute_daily_relative_strength,
    emit_recent_crossings,
)
from genkei.experiments.relative_strength import PricePoint

WATCHLIST_YAML = (
    "version: 1\n"
    "crypto:\n"
    "  primary:\n"
    "    - symbol: BTC\n"
    "      name: Bitcoin\n"
    "      coingecko_id: bitcoin\n"
    "      coinbase_product: BTC-USD\n"
    "      sleeve: core\n"
    "    - symbol: ETH\n"
    "      name: Ethereum\n"
    "      coingecko_id: ethereum\n"
    "      coinbase_product: ETH-USD\n"
    "      sleeve: core\n"
    "    - symbol: SUI\n"
    "      name: Sui\n"
    "      coingecko_id: sui\n"
    "      coinbase_product: SUI-USD\n"
    "      sleeve: tactical\n"
)


def _load_watchlist() -> object:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "watchlists.yml"
        path.write_text(WATCHLIST_YAML, encoding="utf-8")
        return load_watchlist(path)


def _flat_series(start: date, days: int, price: Decimal) -> list[PricePoint]:
    return [
        PricePoint(ts=start, price_usd=price)
        for start in (date.fromordinal(start.toordinal() + i) for i in range(days))
    ]


def _ramped_series(
    start: date, days: int, start_price: Decimal, daily_pct: Decimal
) -> list[PricePoint]:
    """Daily price series rising/falling by ``daily_pct`` (compounded)."""
    out: list[PricePoint] = []
    price = start_price
    for i in range(days):
        out.append(PricePoint(ts=date.fromordinal(start.toordinal() + i), price_usd=price))
        price = price * (Decimal("1") + daily_pct / Decimal("100"))
    return out


class StateForTests(unittest.TestCase):
    def test_none_yields_none(self) -> None:
        self.assertIsNone(_state_for(None))

    def test_below_laggard_threshold_is_laggard(self) -> None:
        self.assertEqual(_state_for(Decimal("-15")), "laggard")
        self.assertEqual(_state_for(Decimal("-50")), "laggard")

    def test_above_leader_threshold_is_leader(self) -> None:
        self.assertEqual(_state_for(Decimal("15")), "leader")
        self.assertEqual(_state_for(Decimal("50")), "leader")

    def test_between_thresholds_is_neutral(self) -> None:
        self.assertEqual(_state_for(Decimal("0")), "neutral")
        self.assertEqual(_state_for(Decimal("14.99")), "neutral")
        self.assertEqual(_state_for(Decimal("-14.99")), "neutral")


class StrengthFromRelStrengthTests(unittest.TestCase):
    def test_at_threshold_yields_partial(self) -> None:
        # ±15pp / 20pp saturation = 0.75
        self.assertEqual(_strength_from_rel_strength(Decimal("-15")), Decimal("0.75"))
        self.assertEqual(_strength_from_rel_strength(Decimal("15")), Decimal("0.75"))

    def test_at_saturation_yields_one(self) -> None:
        self.assertEqual(_strength_from_rel_strength(Decimal("-20")), Decimal("1"))
        self.assertEqual(_strength_from_rel_strength(Decimal("20")), Decimal("1"))

    def test_above_saturation_clamps_to_one(self) -> None:
        self.assertEqual(_strength_from_rel_strength(Decimal("-100")), Decimal("1"))
        self.assertEqual(_strength_from_rel_strength(Decimal("100")), Decimal("1"))

    def test_below_threshold_magnitude(self) -> None:
        # ±10pp is below threshold but still produces a non-zero strength
        # (the magnitude is computed regardless of state).
        self.assertEqual(_strength_from_rel_strength(Decimal("-10")), Decimal("0.5"))

    def test_constants_match_design(self) -> None:
        self.assertEqual(LAGGARD_THRESHOLD_PCT, Decimal("-15"))
        self.assertEqual(LEADER_THRESHOLD_PCT, Decimal("15"))
        self.assertEqual(STRENGTH_SATURATION_PP, Decimal("20"))
        self.assertEqual(WINDOW_DAYS, 30)
        self.assertEqual(PEER_PRODUCT, "BTC-USD")
        self.assertEqual(PEER_SYMBOL, "BTC")


class DateTsTests(unittest.TestCase):
    def test_converts_to_utc_midnight(self) -> None:
        result = _date_ts(date(2024, 6, 1))
        self.assertEqual(result, datetime(2024, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(result.time(), time(0, 0))


class ComputeDailyRelativeStrengthTests(unittest.TestCase):
    def test_empty_series_yields_empty(self) -> None:
        self.assertEqual(compute_daily_relative_strength([], []), [])

    def test_flat_series_yields_zero_rel_strength(self) -> None:
        start = date(2024, 1, 1)
        asset = _flat_series(start, 60, Decimal("100"))
        peer = _flat_series(start, 60, Decimal("50"))
        rows = compute_daily_relative_strength(asset, peer, window_days=30)
        # Every day after the 30-day lookback fills should report 0 rel-strength.
        self.assertTrue(len(rows) >= 25)
        for _ts, asset_ret, peer_ret, rel in rows:
            self.assertEqual(asset_ret, Decimal("0"))
            self.assertEqual(peer_ret, Decimal("0"))
            self.assertEqual(rel, Decimal("0"))

    def test_asset_outperforming_peer_yields_positive_rel_strength(self) -> None:
        start = date(2024, 1, 1)
        # Asset rises 1%/day; peer flat. Over 30 days asset return ≈ +34.7%,
        # peer return = 0. rel_strength ≈ +34.7%.
        asset = _ramped_series(start, 60, Decimal("100"), Decimal("1"))
        peer = _flat_series(start, 60, Decimal("50"))
        rows = compute_daily_relative_strength(asset, peer, window_days=30)
        # Inspect the last row's rel_strength.
        _, asset_ret, peer_ret, rel = rows[-1]
        self.assertGreater(asset_ret, Decimal("30"))
        self.assertEqual(peer_ret, Decimal("0"))
        self.assertGreater(rel, Decimal("30"))

    def test_peer_outperforming_asset_yields_negative_rel_strength(self) -> None:
        start = date(2024, 1, 1)
        asset = _flat_series(start, 60, Decimal("100"))
        peer = _ramped_series(start, 60, Decimal("50"), Decimal("1"))
        rows = compute_daily_relative_strength(asset, peer, window_days=30)
        _, _, _, rel = rows[-1]
        self.assertLess(rel, Decimal("-30"))


class DetectCrossingsTests(unittest.TestCase):
    def test_no_crossings_in_neutral_series(self) -> None:
        daily = [
            (date(2024, 6, i + 1), Decimal("1"), Decimal("0"), Decimal("1"))
            for i in range(5)
        ]
        self.assertEqual(_detect_crossings(daily, asset="ETH"), [])

    def test_single_laggard_onset(self) -> None:
        daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("0")),  # neutral
            (date(2024, 6, 2), Decimal("-5"), Decimal("0"), Decimal("-5")),  # neutral
            (date(2024, 6, 3), Decimal("-20"), Decimal("0"), Decimal("-20")),  # laggard onset
            (date(2024, 6, 4), Decimal("-25"), Decimal("0"), Decimal("-25")),  # continuation
            (date(2024, 6, 5), Decimal("-22"), Decimal("0"), Decimal("-22")),  # continuation
        ]
        crossings = _detect_crossings(daily, asset="ETH")
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].ts, date(2024, 6, 3))
        self.assertEqual(crossings[0].kind, "laggard_crossing")
        self.assertEqual(crossings[0].rel_strength_pct, Decimal("-20"))

    def test_laggard_then_leader(self) -> None:
        daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("-20")),  # laggard onset
            (date(2024, 6, 2), Decimal("0"), Decimal("0"), Decimal("0")),    # neutral
            (date(2024, 6, 3), Decimal("0"), Decimal("0"), Decimal("20")),   # leader onset
        ]
        crossings = _detect_crossings(daily, asset="SOL")
        self.assertEqual([c.kind for c in crossings], ["laggard_crossing", "leader_crossing"])

    def test_neutral_to_neutral_emits_nothing(self) -> None:
        # State stays in neutral throughout — no crossings.
        daily = [
            (date(2024, 6, i + 1), Decimal("0"), Decimal("0"), Decimal("5"))
            for i in range(5)
        ]
        self.assertEqual(_detect_crossings(daily, asset="ETH"), [])

    def test_initial_laggard_state_is_recorded_as_crossing(self) -> None:
        # The very first row in the input is laggard — counted as a fresh
        # onset since there's no prior to compare to. The caller is
        # responsible for loading enough history that this is harmless.
        daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("-20")),
        ]
        crossings = _detect_crossings(daily, asset="ETH")
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].kind, "laggard_crossing")

    def test_return_to_neutral_does_not_emit(self) -> None:
        daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("-20")),  # laggard onset
            (date(2024, 6, 2), Decimal("0"), Decimal("0"), Decimal("0")),    # neutral (silent)
            (date(2024, 6, 3), Decimal("0"), Decimal("0"), Decimal("5")),    # neutral (silent)
        ]
        crossings = _detect_crossings(daily, asset="SOL")
        # Only the laggard onset emits.
        self.assertEqual(len(crossings), 1)


class BuildEventTests(unittest.TestCase):
    def _crossing(
        self,
        *,
        asset: str = "ethereum",
        kind: str = "laggard_crossing",
        rel: Decimal = Decimal("-20"),
        ts: date = date(2024, 6, 1),
    ) -> Crossing:
        return Crossing(
            asset=asset,
            peer="BTC",
            ts=ts,
            kind=kind,
            rel_strength_pct=rel,
            asset_return_pct=Decimal("5"),
            peer_return_pct=Decimal("25"),
        )

    def test_bearish_laggard_event_shape(self) -> None:
        event = _build_event(self._crossing(), horizon="crypto:core")
        self.assertEqual(event["asset"], "ethereum")
        self.assertEqual(event["asset_class"], "crypto")
        self.assertEqual(event["horizon"], "crypto:core")
        self.assertEqual(event["source"], "relative_strength")
        self.assertEqual(event["signal_kind"], "laggard_crossing")
        self.assertEqual(event["direction"], "bearish")
        self.assertEqual(event["ts"], datetime(2024, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(event["source_ref"], "ethereum:BTC:30d:2024-06-01")
        # ±20pp / 20pp saturation = 1.0 (full saturation at threshold + 5pp).
        self.assertEqual(event["strength"], Decimal("1"))
        # Payload carries the underlying numbers + thresholds.
        self.assertEqual(event["payload"]["window_days"], 30)
        self.assertEqual(event["payload"]["rel_strength_pct"], "-20")
        self.assertEqual(event["payload"]["thresholds"]["laggard_pct"], "-15")

    def test_bullish_leader_event_direction(self) -> None:
        event = _build_event(
            self._crossing(kind="leader_crossing", rel=Decimal("18")),
            horizon="crypto:tactical",
        )
        self.assertEqual(event["direction"], "bullish")
        self.assertEqual(event["signal_kind"], "leader_crossing")
        self.assertEqual(event["horizon"], "crypto:tactical")

    def test_source_ref_includes_peer_and_window(self) -> None:
        # Lets a future emitter run with a different peer / window without
        # colliding on source_ref.
        event = _build_event(self._crossing(asset="solana"), horizon="crypto:core")
        self.assertEqual(event["source_ref"], "solana:BTC:30d:2024-06-01")


class CryptoAssetsTests(unittest.TestCase):
    def test_excludes_btc_and_keeps_others(self) -> None:
        w = _load_watchlist()
        out = _crypto_assets(w)
        assets = sorted(
            (asset.asset_id, asset.symbol, asset.price_source, asset.price_key)
            for asset in out
        )
        self.assertEqual(
            assets,
            [
                ("ethereum", "ETH", "coinbase", "ETH-USD"),
                ("sui", "SUI", "coinbase", "SUI-USD"),
            ],
        )

    def test_sleeve_passthrough(self) -> None:
        w = _load_watchlist()
        out = {asset.asset_id: asset.sleeve for asset in _crypto_assets(w)}
        self.assertEqual(out.get("ethereum"), "core")
        self.assertEqual(out.get("sui"), "tactical")

    def test_uses_configured_coinbase_product(self) -> None:
        yaml = WATCHLIST_YAML + (
            "    - symbol: ALIAS\n"
            "      name: Alias Token\n"
            "      coingecko_id: alias-token\n"
            "      coinbase_product: ALIAS-V2-USD\n"
            "      sleeve: tactical\n"
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml, encoding="utf-8")
            price_keys = {
                asset.asset_id: (asset.price_source, asset.price_key)
                for asset in _crypto_assets(load_watchlist(path))
            }
        self.assertEqual(price_keys["alias-token"], ("coinbase", "ALIAS-V2-USD"))

    def test_keeps_coingecko_only_primary_asset(self) -> None:
        yaml = WATCHLIST_YAML + (
            "    - symbol: JUP\n"
            "      name: Jupiter\n"
            "      coingecko_id: jupiter-exchange-solana\n"
            "      sleeve: core\n"
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml, encoding="utf-8")
            targets = {
                asset.asset_id: asset for asset in _crypto_assets(load_watchlist(path))
            }
        jup = targets["jupiter-exchange-solana"]
        self.assertEqual(jup.symbol, "JUP")
        self.assertEqual(jup.price_source, "coingecko")
        self.assertEqual(jup.price_key, "jupiter-exchange-solana")
        self.assertEqual(jup.sleeve, "core")


class EmitOrchestratorTests(unittest.TestCase):
    def test_orchestrator_emits_one_event_per_crossing(self) -> None:
        class FakeRun:
            id = 42

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, _rows: int) -> None:
                return None

        # Two synthetic crossings: a laggard on ETH on day X, a leader on
        # SUI on day Y. The mocked compute returns these directly so we
        # isolate the orchestration logic.
        eth_daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("0")),  # neutral
            (date(2024, 6, 2), Decimal("-25"), Decimal("0"), Decimal("-25")),  # laggard onset
        ]
        sui_daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("0")),  # neutral
            (date(2024, 6, 2), Decimal("25"), Decimal("0"), Decimal("25")),  # leader onset
        ]

        def fake_peer_load(product: str, *, until: object = None) -> list[PricePoint]:
            self.assertEqual(product, "BTC-USD")
            return [PricePoint(ts=date(2024, 6, 1), price_usd=Decimal("100"))]

        def fake_asset_load(asset: object, *, until: object = None) -> list[PricePoint]:
            # Non-empty so the orchestrator routes through compute.
            return [PricePoint(ts=date(2024, 6, 1), price_usd=Decimal("100"))]

        def fake_compute(
            asset_series: object,
            peer_series: object,
            *,
            window_days: int = WINDOW_DAYS,
        ) -> list:
            # Asset/peer series identity unused — return the right canned
            # daily series based on call order. We track via a counter.
            fake_compute.calls += 1  # type: ignore[attr-defined]
            return eth_daily if fake_compute.calls == 1 else sui_daily  # type: ignore[attr-defined]

        fake_compute.calls = 0  # type: ignore[attr-defined]

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST_YAML, encoding="utf-8")
            with (
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.db.ingest_run",
                    return_value=FakeRun(),
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_price_series",
                    side_effect=fake_peer_load,
                ) as peer_load_mock,
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_asset_price_series",
                    side_effect=fake_asset_load,
                ) as asset_load_mock,
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.compute_daily_relative_strength",
                    side_effect=fake_compute,
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.emit_signals_bulk",
                    return_value=2,
                ) as emit_mock,
            ):
                result = emit_recent_crossings(config=path)

        emitted = emit_mock.call_args.args[0]
        self.assertEqual(len(emitted), 2)
        kinds = sorted(e["signal_kind"] for e in emitted)
        self.assertEqual(kinds, ["laggard_crossing", "leader_crossing"])
        assets = sorted(e["asset"] for e in emitted)
        self.assertEqual(assets, ["ethereum", "sui"])
        peer_products = [call.args[0] for call in peer_load_mock.call_args_list]
        self.assertEqual(peer_products, ["BTC-USD"])
        asset_keys = [call.args[0].price_key for call in asset_load_mock.call_args_list]
        self.assertEqual(asset_keys, ["ETH-USD", "SUI-USD"])
        self.assertEqual(result.crossings_emitted, 2)

    def test_orchestrator_emits_for_coingecko_only_asset(self) -> None:
        class FakeRun:
            id = 43

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, _rows: int) -> None:
                return None

        yaml = (
            "version: 1\n"
            "crypto:\n"
            "  primary:\n"
            "    - symbol: BTC\n"
            "      name: Bitcoin\n"
            "      coingecko_id: bitcoin\n"
            "      coinbase_product: BTC-USD\n"
            "      sleeve: core\n"
            "    - symbol: JUP\n"
            "      name: Jupiter\n"
            "      coingecko_id: jupiter-exchange-solana\n"
            "      sleeve: core\n"
        )
        jup_daily = [
            (date(2024, 6, 1), Decimal("0"), Decimal("0"), Decimal("0")),
            (date(2024, 6, 2), Decimal("25"), Decimal("0"), Decimal("25")),
        ]

        def fake_asset_load(asset: object, *, until: object = None) -> list[PricePoint]:
            self.assertEqual(asset.price_source, "coingecko")
            self.assertEqual(asset.price_key, "jupiter-exchange-solana")
            return [PricePoint(ts=date(2024, 6, 1), price_usd=Decimal("100"))]

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml, encoding="utf-8")
            with (
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.db.ingest_run",
                    return_value=FakeRun(),
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_price_series",
                    return_value=[PricePoint(ts=date(2024, 6, 1), price_usd=Decimal("100"))],
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_asset_price_series",
                    side_effect=fake_asset_load,
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.compute_daily_relative_strength",
                    return_value=jup_daily,
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.emit_signals_bulk",
                    return_value=1,
                ) as emit_mock,
            ):
                result = emit_recent_crossings(config=path)

        emitted = emit_mock.call_args.args[0]
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["asset"], "jupiter-exchange-solana")
        self.assertEqual(emitted[0]["horizon"], "crypto:core")
        self.assertEqual(emitted[0]["signal_kind"], "leader_crossing")
        self.assertEqual(result.crossings_emitted, 1)

    def test_orchestrator_skips_assets_with_no_data(self) -> None:
        class FakeRun:
            id = 7

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, _rows: int) -> None:
                return None

        # BTC peer series loads but asset series are empty.
        def fake_peer_load(product: str, *, until: object = None) -> list[PricePoint]:
            if product == "BTC-USD":
                return [PricePoint(ts=date(2024, 6, 1), price_usd=Decimal("60000"))]
            return []

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST_YAML, encoding="utf-8")
            with (
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.db.ingest_run",
                    return_value=FakeRun(),
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_price_series",
                    side_effect=fake_peer_load,
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_asset_price_series",
                    return_value=[],
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.emit_signals_bulk",
                    return_value=0,
                ),
            ):
                result = emit_recent_crossings(config=path)

        # ETH and SUI both load empty → both counted under no_data.
        self.assertEqual(result.crossings_emitted, 0)
        self.assertEqual(result.assets_skipped_no_data, 2)

    def test_orchestrator_short_circuits_when_no_peer_data(self) -> None:
        class FakeRun:
            id = 11

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, _rows: int) -> None:
                return None

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST_YAML, encoding="utf-8")
            with (
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.db.ingest_run",
                    return_value=FakeRun(),
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_price_series",
                    return_value=[],
                ),
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter._load_asset_price_series",
                ) as asset_load_mock,
                patch(
                    "genkei.experiments.emitters.relative_strength_emitter.emit_signals_bulk",
                    return_value=0,
                ),
            ):
                result = emit_recent_crossings(config=path)

        # No BTC data → can't compute anything; all assets counted as no_data.
        asset_load_mock.assert_not_called()
        self.assertEqual(result.crossings_emitted, 0)
        self.assertEqual(result.assets_skipped_no_data, 2)


if __name__ == "__main__":
    unittest.main()
