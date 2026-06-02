"""Unit tests for the TVL-drawdown → signal_events emitter (B-095)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from genkei.common.watchlist import load_watchlist
from genkei.experiments.emitters.tvl_drawdown_emitter import (
    CHAIN_TO_CRYPTO_SYMBOL,
    TVL_CHANGE_30D_SATURATION_RANGE_PCT,
    TVL_DRAWDOWN_SATURATION_RANGE_PCT,
    TVL_ZSCORE_SATURATION_RANGE,
    _build_event,
    _detect_episode_starts,
    _feature_ts,
    _horizon_for_symbol,
    _normalized_excess,
    _strength_from_features,
    emit_recent_drawdown_stress,
)
from genkei.experiments.tvl_drawdown import (
    DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT,
    DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT,
    DEFAULT_TVL_ZSCORE_THRESHOLD,
    AlignedRow,
    FeatureRow,
)

WATCHLIST_YAML = (
    "version: 1\n"
    "crypto:\n"
    "  primary:\n"
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


def _stress_row(
    *,
    ts: date = date(2024, 6, 1),
    tvl_usd: Decimal = Decimal("100000000"),
    price_usd: Decimal = Decimal("100"),
    change_30d: Decimal | None = Decimal("-15"),
    drawdown: Decimal | None = Decimal("25"),
    zscore: Decimal | None = Decimal("-1.5"),
) -> FeatureRow:
    """Default row breaches all three thresholds (classifier fires)."""
    return FeatureRow(
        ts=ts,
        tvl_usd=tvl_usd,
        price_usd=price_usd,
        tvl_change_7d_pct=None,
        tvl_change_30d_pct=change_30d,
        tvl_change_90d_pct=None,
        tvl_drawdown_from_peak_90d_pct=drawdown,
        tvl_zscore_90d=zscore,
        forward_drawdown_pct=None,
    )


class NormalizedExcessTests(unittest.TestCase):
    def test_no_value_yields_zero(self) -> None:
        self.assertEqual(
            _normalized_excess(
                value=None,
                threshold=Decimal("-10"),
                saturation_range=Decimal("30"),
                sign=-1,
            ),
            Decimal("0"),
        )

    def test_below_threshold_lower_bound_yields_zero(self) -> None:
        # value (-5) is ABOVE threshold (-10) → not in stress → excess 0
        self.assertEqual(
            _normalized_excess(
                value=Decimal("-5"),
                threshold=Decimal("-10"),
                saturation_range=Decimal("30"),
                sign=-1,
            ),
            Decimal("0"),
        )

    def test_at_threshold_yields_zero(self) -> None:
        self.assertEqual(
            _normalized_excess(
                value=Decimal("-10"),
                threshold=Decimal("-10"),
                saturation_range=Decimal("30"),
                sign=-1,
            ),
            Decimal("0"),
        )

    def test_partial_excess_lower_bound(self) -> None:
        # value -25 vs threshold -10 → excess 15 of 30 → 0.5
        self.assertEqual(
            _normalized_excess(
                value=Decimal("-25"),
                threshold=Decimal("-10"),
                saturation_range=Decimal("30"),
                sign=-1,
            ),
            Decimal("0.5"),
        )

    def test_full_saturation_lower_bound(self) -> None:
        # value -40 vs threshold -10 → excess 30 of 30 → 1.0
        self.assertEqual(
            _normalized_excess(
                value=Decimal("-40"),
                threshold=Decimal("-10"),
                saturation_range=Decimal("30"),
                sign=-1,
            ),
            Decimal("1"),
        )

    def test_over_saturation_clamps_to_one(self) -> None:
        self.assertEqual(
            _normalized_excess(
                value=Decimal("-100"),
                threshold=Decimal("-10"),
                saturation_range=Decimal("30"),
                sign=-1,
            ),
            Decimal("1"),
        )

    def test_upper_bound_sign_positive(self) -> None:
        # value 30 vs threshold 15 → excess 15 of 30 → 0.5
        self.assertEqual(
            _normalized_excess(
                value=Decimal("30"),
                threshold=Decimal("15"),
                saturation_range=Decimal("30"),
                sign=+1,
            ),
            Decimal("0.5"),
        )


class StrengthFromFeaturesTests(unittest.TestCase):
    def test_at_thresholds_yields_zero(self) -> None:
        row = _stress_row(
            change_30d=Decimal("-10"),
            drawdown=Decimal("15"),
            zscore=Decimal("-1"),
        )
        self.assertEqual(_strength_from_features(row), Decimal("0"))

    def test_canonical_stress_row(self) -> None:
        # change -25 (excess 15/30 = 0.5), drawdown 30 (excess 15/30 = 0.5),
        # zscore -2 (excess 1/2 = 0.5). Mean = 0.5.
        row = _stress_row(
            change_30d=Decimal("-25"),
            drawdown=Decimal("30"),
            zscore=Decimal("-2"),
        )
        self.assertEqual(_strength_from_features(row), Decimal("0.5"))

    def test_full_saturation_yields_one(self) -> None:
        row = _stress_row(
            change_30d=Decimal("-40"),
            drawdown=Decimal("45"),
            zscore=Decimal("-3"),
        )
        self.assertEqual(_strength_from_features(row), Decimal("1"))

    def test_one_condition_saturated_others_at_threshold(self) -> None:
        # change at threshold (0), drawdown saturated (1), zscore at threshold (0).
        # Mean = 1/3.
        row = _stress_row(
            change_30d=Decimal("-10"),
            drawdown=Decimal("45"),
            zscore=Decimal("-1"),
        )
        result = _strength_from_features(row)
        self.assertEqual(result, Decimal("1") / Decimal("3"))

    def test_saturation_constants_match_design(self) -> None:
        # Pin the named saturation magnitudes so a future tuner notices
        # they're load-bearing.
        self.assertEqual(TVL_CHANGE_30D_SATURATION_RANGE_PCT, Decimal("30"))
        self.assertEqual(TVL_DRAWDOWN_SATURATION_RANGE_PCT, Decimal("30"))
        self.assertEqual(TVL_ZSCORE_SATURATION_RANGE, Decimal("2"))

    def test_thresholds_match_classifier_defaults(self) -> None:
        # The emitter's strength normalization reads B-058's exported
        # threshold constants. Pinning here so a future B-058 retune
        # doesn't silently drift this emitter's strength meaning.
        self.assertEqual(DEFAULT_TVL_CHANGE_30D_THRESHOLD_PCT, Decimal("-10"))
        self.assertEqual(DEFAULT_TVL_DRAWDOWN_THRESHOLD_PCT, Decimal("15"))
        self.assertEqual(DEFAULT_TVL_ZSCORE_THRESHOLD, Decimal("-1"))


class FeatureTsTests(unittest.TestCase):
    def test_converts_to_utc_midnight(self) -> None:
        row = _stress_row(ts=date(2024, 6, 1))
        self.assertEqual(
            _feature_ts(row),
            datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(_feature_ts(row).time(), time(0, 0))


class HorizonForSymbolTests(unittest.TestCase):
    def _watchlist(self) -> object:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(WATCHLIST_YAML, encoding="utf-8")
            return load_watchlist(path)

    def test_core_sleeve_yields_crypto_core(self) -> None:
        w = self._watchlist()
        self.assertEqual(_horizon_for_symbol("ETH", w), "crypto:core")

    def test_tactical_sleeve_yields_crypto_tactical(self) -> None:
        w = self._watchlist()
        self.assertEqual(_horizon_for_symbol("SUI", w), "crypto:tactical")

    def test_non_watchlist_symbol_yields_none(self) -> None:
        w = self._watchlist()
        self.assertIsNone(_horizon_for_symbol("DOGE", w))


class DetectEpisodeStartsTests(unittest.TestCase):
    def _aligned(self, ts: date) -> AlignedRow:
        return AlignedRow(ts=ts, tvl_usd=Decimal("100"), price_usd=Decimal("1"))

    def _firing_row(self, ts: date) -> FeatureRow:
        return _stress_row(ts=ts)

    def _quiet_row(self, ts: date) -> FeatureRow:
        return _stress_row(
            ts=ts,
            change_30d=Decimal("-5"),  # above threshold
            drawdown=Decimal("5"),  # below threshold
            zscore=Decimal("0"),  # above threshold
        )

    def test_no_episodes_in_quiet_series(self) -> None:
        rows = [self._quiet_row(date(2024, 6, i)) for i in (1, 2, 3, 4)]
        self.assertEqual(_detect_episode_starts(rows), [])

    def test_one_onset_at_first_firing_day(self) -> None:
        rows = [
            self._quiet_row(date(2024, 6, 1)),
            self._quiet_row(date(2024, 6, 2)),
            self._firing_row(date(2024, 6, 3)),  # onset
            self._firing_row(date(2024, 6, 4)),  # continuation
            self._firing_row(date(2024, 6, 5)),  # continuation
            self._quiet_row(date(2024, 6, 6)),
        ]
        onsets = _detect_episode_starts(rows)
        self.assertEqual(len(onsets), 1)
        self.assertEqual(onsets[0].ts, date(2024, 6, 3))

    def test_multiple_episodes_each_count_once(self) -> None:
        rows = [
            self._firing_row(date(2024, 6, 1)),  # onset (treated as a new run)
            self._firing_row(date(2024, 6, 2)),  # continuation
            self._quiet_row(date(2024, 6, 3)),
            self._firing_row(date(2024, 6, 4)),  # new onset
            self._quiet_row(date(2024, 6, 5)),
            self._firing_row(date(2024, 6, 6)),  # new onset
        ]
        onsets = _detect_episode_starts(rows)
        self.assertEqual(
            [o.ts for o in onsets],
            [date(2024, 6, 1), date(2024, 6, 4), date(2024, 6, 6)],
        )

    def test_first_row_firing_counts_as_onset(self) -> None:
        # Documented edge case — the very first row is treated as a fresh
        # start (no prior to compare to). The caller pre-loads enough
        # history that this is safe.
        rows = [
            self._firing_row(date(2024, 6, 1)),
            self._firing_row(date(2024, 6, 2)),
        ]
        self.assertEqual([o.ts for o in _detect_episode_starts(rows)], [date(2024, 6, 1)])


class BuildEventTests(unittest.TestCase):
    def test_canonical_bearish_event_shape(self) -> None:
        onset = _stress_row(ts=date(2024, 6, 1))
        event = _build_event(
            onset,
            chain="Ethereum",
            asset="ethereum",
            symbol="ETH",
            horizon="crypto:core",
        )
        self.assertEqual(event["asset"], "ethereum")
        self.assertEqual(event["asset_class"], "crypto")
        self.assertEqual(event["horizon"], "crypto:core")
        self.assertEqual(event["source"], "tvl_drawdown")
        self.assertEqual(event["signal_kind"], "tvl_drawdown_stress")
        self.assertEqual(event["direction"], "bearish")
        self.assertEqual(event["ts"], datetime(2024, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(event["source_ref"], "Ethereum:2024-06-01")
        # Payload preserves the feature values + the thresholds (so a
        # consumer can see how the strength was derived).
        self.assertEqual(event["payload"]["chain"], "Ethereum")
        self.assertEqual(event["payload"]["asset"], "ethereum")
        self.assertEqual(event["payload"]["asset_symbol"], "ETH")
        self.assertEqual(event["payload"]["episode_start"], "2024-06-01")
        self.assertEqual(event["payload"]["tvl_change_30d_pct"], "-15")
        self.assertEqual(
            event["payload"]["thresholds"]["tvl_drawdown_from_peak_90d_pct"],
            "15",
        )

    def test_source_ref_uses_chain_not_asset(self) -> None:
        # Multiple chains could in principle pin the same asset (e.g. if a
        # future entry maps two L1s to one watchlist token). Using the
        # chain in source_ref keeps episodes distinguishable.
        onset = _stress_row(ts=date(2024, 6, 1))
        eth_event = _build_event(
            onset,
            chain="Ethereum",
            asset="ethereum",
            symbol="ETH",
            horizon="crypto:core",
        )
        sol_event = _build_event(
            onset,
            chain="Solana",
            asset="solana",
            symbol="SOL",
            horizon="crypto:core",
        )
        self.assertNotEqual(eth_event["source_ref"], sol_event["source_ref"])

    def test_chain_to_crypto_symbol_excludes_btc(self) -> None:
        # B-058 design choice: BTC's price drivers aren't on-chain DeFi
        # so the TVL-stress signal is not meaningful for it.
        self.assertNotIn("Bitcoin", CHAIN_TO_CRYPTO_SYMBOL)
        # ETH / SOL / SUI are the live chains.
        self.assertEqual(
            sorted(CHAIN_TO_CRYPTO_SYMBOL.keys()),
            ["Ethereum", "Solana", "Sui"],
        )


class EmitOrchestratorTests(unittest.TestCase):
    def test_orchestrator_emits_one_event_per_episode_per_chain(self) -> None:
        class FakeRun:
            id = 99

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, _rows: int) -> None:
                return None

        # Mock load_aligned_series to return a small canned series per chain,
        # then mock engineer_features to return synthetic firing rows so we
        # can isolate the orchestration logic from B-058's feature engine.
        aligned_eth = [
            AlignedRow(ts=date(2024, 5, 30), tvl_usd=Decimal("100"), price_usd=Decimal("3000")),
            AlignedRow(ts=date(2024, 5, 31), tvl_usd=Decimal("100"), price_usd=Decimal("3000")),
            AlignedRow(ts=date(2024, 6, 1), tvl_usd=Decimal("100"), price_usd=Decimal("3000")),
        ]
        aligned_sol = [
            AlignedRow(
                ts=date(2024, 6, 1),
                tvl_usd=Decimal("100"),
                price_usd=Decimal("150"),
            )
        ]

        # ETH: one episode, one onset. SOL: no firing.
        quiet = _stress_row(
            ts=date(2024, 5, 30),
            change_30d=Decimal("-5"),
            drawdown=Decimal("5"),
            zscore=Decimal("0"),
        )
        firing = _stress_row(ts=date(2024, 5, 31))
        cont = _stress_row(ts=date(2024, 6, 1))

        def fake_load(chain: str, product: str, *, until: object = None) -> object:
            if chain == "Ethereum":
                return aligned_eth
            if chain == "Solana":
                return aligned_sol
            return []

        def fake_engineer(aligned: object, **_kw: object) -> list[FeatureRow]:
            if aligned is aligned_eth:
                return [quiet, firing, cont]
            return [
                _stress_row(
                    ts=date(2024, 6, 1),
                    change_30d=Decimal("-5"),
                    drawdown=Decimal("5"),
                    zscore=Decimal("0"),
                )
            ]

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                WATCHLIST_YAML + (
                    "    - symbol: SOL\n"
                    "      name: Solana\n"
                    "      coingecko_id: solana\n"
                    "      coinbase_product: SOL-USD\n"
                    "      sleeve: core\n"
                ),
                encoding="utf-8",
            )
            with (
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.db.ingest_run",
                    return_value=FakeRun(),
                ),
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.load_aligned_series",
                    side_effect=fake_load,
                ),
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.engineer_features",
                    side_effect=fake_engineer,
                ),
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.emit_signals_bulk",
                    return_value=1,
                ) as emit_mock,
            ):
                result = emit_recent_drawdown_stress(config=path)

        # Single ETH onset emitted; SOL had quiet features (no firing);
        # Sui chain is in CHAIN_TO_CRYPTO_SYMBOL AND the watchlist fixture
        # (the base WATCHLIST_YAML carries SUI) but the fake_load default
        # branch returns [] for it → counted under chains_skipped_no_data.
        emitted = emit_mock.call_args.args[0]
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["asset"], "ethereum")
        self.assertEqual(emitted[0]["payload"]["asset_symbol"], "ETH")
        self.assertEqual(emitted[0]["source_ref"], "Ethereum:2024-05-31")
        self.assertEqual(result.episodes_emitted, 1)
        self.assertEqual(result.chains_skipped_no_watchlist, 0)
        self.assertEqual(result.chains_skipped_no_data, 1)

    def test_orchestrator_skips_chains_with_no_data(self) -> None:
        class FakeRun:
            id = 7

            def __enter__(self) -> FakeRun:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def add_rows(self, _rows: int) -> None:
                return None

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(
                WATCHLIST_YAML + (
                    "    - symbol: SOL\n"
                    "      name: Solana\n"
                    "      coingecko_id: solana\n"
                    "      coinbase_product: SOL-USD\n"
                    "      sleeve: core\n"
                ),
                encoding="utf-8",
            )
            with (
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.db.ingest_run",
                    return_value=FakeRun(),
                ),
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.load_aligned_series",
                    return_value=[],
                ),
                patch(
                    "genkei.experiments.emitters.tvl_drawdown_emitter.emit_signals_bulk",
                    return_value=0,
                ),
            ):
                result = emit_recent_drawdown_stress(config=path)

        # All three chains in the watchlist→symbol map fall through to the
        # empty-load branch: ETH + SOL + SUI all counted under no_data
        # (the base fixture includes all three).
        self.assertEqual(result.episodes_emitted, 0)
        self.assertEqual(result.chains_skipped_no_data, 3)
        self.assertEqual(result.chains_skipped_no_watchlist, 0)


if __name__ == "__main__":
    unittest.main()
