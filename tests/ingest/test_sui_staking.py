"""Unit tests for the Sui staking collector (B-088)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.sui_staking import (
    COLLECT_ENDPOINT_LABEL,
    METHOD_SYSTEM_STATE,
    METHOD_VALIDATORS_APY,
    SOURCE_NAME,
    SUI_RPC_URL,
    _coerce_decimal,
    _coerce_int,
    _ms_to_utc_datetime,
    collect,
    parse_validator_rows,
)

# Minimal-but-realistic fragment of a suix_getLatestSuiSystemState response.
# Field shape matches what the live mainnet RPC returned on 2026-06-07
# (epoch 1151). Includes two active validators with full field coverage
# plus one defective row (missing suiAddress) to confirm the skip path.
SAMPLE_SYSTEM_STATE = {
    "epoch": "1151",
    "epochStartTimestampMs": "1780790717256",  # Jun 6 2026 around 18:45 UTC
    "epochDurationMs": "86400000",
    "totalStake": "7250402731876294287",
    "referenceGasPrice": "100",
    "activeValidators": [
        {
            "suiAddress": "0xmysten01" + "a" * 56,
            "name": "Mysten-1",
            "votingPower": 302,
            "stakingPoolSuiBalance": "218350824320000000",
            "nextEpochStake": "218400000000000000",
            "pendingStake": "5000000000000",
            "pendingPoolTokenWithdraw": "1000000000000",
            "pendingTotalSuiWithdraw": "1100000000000",
            "commissionRate": "200",
            "nextEpochCommissionRate": "200",
            "gasPrice": "750",
            "nextEpochGasPrice": "750",
            "rewardsPool": "649057174720907",
            "stakingPoolActivationEpoch": "0",
            "stakingPoolDeactivationEpoch": None,
        },
        {
            "suiAddress": "0xcoinbase" + "b" * 57,
            "name": "Coinbase",
            "votingPower": 215,
            "stakingPoolSuiBalance": "155708205570000000",
            "nextEpochStake": "155700000000000000",
            "pendingStake": "0",
            "pendingPoolTokenWithdraw": "9480325486",
            "pendingTotalSuiWithdraw": "10331972598",
            "commissionRate": "1000",
            "nextEpochCommissionRate": "1000",
            "gasPrice": "910",
            "nextEpochGasPrice": "910",
            "rewardsPool": "500000000000000",
            "stakingPoolActivationEpoch": "10",
            "stakingPoolDeactivationEpoch": None,
        },
        # Defective row — missing suiAddress — must be skipped silently
        # without raising. Real upstream data has occasionally returned
        # half-populated validator records during testnet upgrades.
        {
            "name": "Bad-Validator",
            "votingPower": 5,
            "stakingPoolSuiBalance": "1000000000000",
        },
    ],
}

SAMPLE_APY_PAYLOAD = {
    "epoch": "1151",
    "apys": [
        {
            "address": "0xmysten01" + "a" * 56,
            "apy": 0.0156,
        },
        {
            "address": "0xcoinbase" + "b" * 57,
            "apy": 0.0143,
        },
        # Stale entry — references a validator no longer in the system
        # state. Should be silently dropped by the join.
        {"address": "0xinactive" + "c" * 57, "apy": 0.02},
    ],
}


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class ModuleConstantsTests(unittest.TestCase):
    """Pin the module-level constants the workflow + health check depend on."""

    def test_source_name(self) -> None:
        """Stable source name keyed in RECURRING_ENDPOINTS + PRIMARY_TABLES."""
        self.assertEqual(SOURCE_NAME, "sui_staking")

    def test_collect_endpoint_label_follows_convention(self) -> None:
        """'collect' matches the universal convention pinned by test_watchlist_cmd."""
        self.assertEqual(COLLECT_ENDPOINT_LABEL, "collect")

    def test_rpc_url_is_public_mainnet_fullnode(self) -> None:
        """v1 uses the public fullnode — no Blockvision dependency."""
        self.assertEqual(SUI_RPC_URL, "https://fullnode.mainnet.sui.io")

    def test_rpc_method_names_stable(self) -> None:
        """Sui standard RPC method names — pin them so a typo doesn't go silently."""
        self.assertEqual(METHOD_SYSTEM_STATE, "suix_getLatestSuiSystemState")
        self.assertEqual(METHOD_VALIDATORS_APY, "suix_getValidatorsApy")


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class CoerceIntTests(unittest.TestCase):
    """Sui RPC returns u64s as JSON strings."""

    def test_string_digits(self) -> None:
        """Plain integer strings parse."""
        self.assertEqual(_coerce_int("1151"), 1151)
        self.assertEqual(_coerce_int("  1151  "), 1151)

    def test_native_int(self) -> None:
        """Native ints pass through."""
        self.assertEqual(_coerce_int(302), 302)

    def test_none_and_blank(self) -> None:
        """Missing / blank become None."""
        self.assertIsNone(_coerce_int(None))
        self.assertIsNone(_coerce_int(""))

    def test_garbage(self) -> None:
        """Non-numeric strings yield None rather than raising."""
        self.assertIsNone(_coerce_int("not-a-number"))

    def test_bool_rejected(self) -> None:
        """Bool would silently coerce to 0/1 via int(); reject explicitly."""
        # Defensive: stakingPoolDeactivationEpoch is sometimes None, sometimes
        # an int; a bool sneaking in would corrupt the value.
        self.assertIsNone(_coerce_int(True))
        self.assertIsNone(_coerce_int(False))


class CoerceDecimalTests(unittest.TestCase):
    """MIST values are stored as Decimal to match the NUMERIC(40, 0) column."""

    def test_string_u64(self) -> None:
        """The dominant case: large u64 from the wire."""
        self.assertEqual(
            _coerce_decimal("7250402731876294287"),
            Decimal("7250402731876294287"),
        )

    def test_string_with_decimals_for_apy(self) -> None:
        """Decimal strings (e.g. APY floats) survive."""
        self.assertEqual(_coerce_decimal("0.0156"), Decimal("0.0156"))

    def test_native_int_to_decimal(self) -> None:
        """Native ints become Decimal."""
        self.assertEqual(_coerce_decimal(123), Decimal(123))

    def test_native_float_to_decimal(self) -> None:
        """Native floats become Decimal via str-round-trip to avoid float noise."""
        self.assertEqual(_coerce_decimal(0.0156), Decimal("0.0156"))

    def test_none_and_blank(self) -> None:
        """Missing / blank become None."""
        self.assertIsNone(_coerce_decimal(None))
        self.assertIsNone(_coerce_decimal(""))


class MsToUtcDatetimeTests(unittest.TestCase):
    """epochStartTimestampMs (string ms since unix epoch) → UTC datetime."""

    def test_round_trip(self) -> None:
        """A known epoch-start ms decodes to the expected UTC moment."""
        # 1780790717256 ms = 2026-06-06 18:45:17.256 UTC
        out = _ms_to_utc_datetime("1780790717256")
        self.assertEqual(
            out, datetime.fromtimestamp(1780790717.256, tz=timezone.utc)
        )
        self.assertEqual(out.tzinfo, timezone.utc)

    def test_native_int_accepted(self) -> None:
        """Native ints (defensive — the wire uses strings) also work."""
        out = _ms_to_utc_datetime(1780790717256)
        self.assertIsNotNone(out)
        self.assertEqual(out.tzinfo, timezone.utc)

    def test_none_returns_none(self) -> None:
        """Missing field yields None."""
        self.assertIsNone(_ms_to_utc_datetime(None))

    def test_garbage_returns_none(self) -> None:
        """Unparseable input yields None instead of raising."""
        self.assertIsNone(_ms_to_utc_datetime("not-a-number"))


# ---------------------------------------------------------------------------
# parse_validator_rows — full extractor
# ---------------------------------------------------------------------------


class ParseValidatorRowsTests(unittest.TestCase):
    """End-to-end extractor: validator list × apy join × defensive skips."""

    def test_extracts_one_row_per_valid_active_validator(self) -> None:
        """Two valid active validators land; defective row is skipped."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, SAMPLE_APY_PAYLOAD)
        # 2 valid + 1 skipped (missing suiAddress)
        self.assertEqual(len(rows), 2)

    def test_epoch_and_epoch_start_carried_from_system_state(self) -> None:
        """All rows from one snapshot share the same epoch + epoch_start_ts."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, SAMPLE_APY_PAYLOAD)
        for r in rows:
            self.assertEqual(r.epoch, 1151)
            self.assertEqual(
                r.epoch_start_ts,
                datetime.fromtimestamp(1780790717.256, tz=timezone.utc),
            )

    def test_stake_amount_preserved_as_mist(self) -> None:
        """MIST is the storage unit — no unit conversion happens at write time."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, SAMPLE_APY_PAYLOAD)
        by_name = {r.name: r for r in rows}
        self.assertEqual(
            by_name["Mysten-1"].stake_amount_mist,
            Decimal("218350824320000000"),
        )
        self.assertEqual(
            by_name["Coinbase"].stake_amount_mist,
            Decimal("155708205570000000"),
        )

    def test_apy_joined_by_validator_address(self) -> None:
        """Per-validator APY pulled from the separate suix_getValidatorsApy payload."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, SAMPLE_APY_PAYLOAD)
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["Mysten-1"].apy, Decimal("0.015600"))
        self.assertEqual(by_name["Coinbase"].apy, Decimal("0.014300"))

    def test_validator_without_apy_kept_with_null(self) -> None:
        """Missing APY is a soft signal-quality issue — keep the row, null the field.

        Dropping rows would silently lose stake/flow data when only the APY
        endpoint failed; the staking signal is more load-bearing than the
        APY signal and the row should survive the join failure.
        """
        # APY payload references neither validator
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, {"epoch": "1151", "apys": []})
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIsNone(r.apy)

    def test_pending_flow_columns_default_to_zero(self) -> None:
        """pending_stake / pending_withdraw default to 0 (NOT NULL columns)."""
        # Build a system_state with pending fields explicitly missing
        ss = {
            "epoch": "100",
            "epochStartTimestampMs": "1780000000000",
            "activeValidators": [
                {
                    "suiAddress": "0x" + "d" * 64,
                    "stakingPoolSuiBalance": "1000000000",
                    # pendingStake + pendingTotalSuiWithdraw both omitted
                },
            ],
        }
        rows = parse_validator_rows(ss, {"epoch": "100", "apys": []})
        self.assertEqual(len(rows), 1)
        # NOT NULL columns: must be Decimal(0), not None — otherwise the
        # bulk_upsert would fail at the DB layer.
        self.assertEqual(rows[0].pending_stake_mist, Decimal(0))
        self.assertEqual(rows[0].pending_withdraw_mist, Decimal(0))

    def test_skips_row_with_missing_sui_address(self) -> None:
        """A validator entry missing suiAddress is skipped, not raised on."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, SAMPLE_APY_PAYLOAD)
        # The third entry ("Bad-Validator") has no suiAddress → not in output.
        addresses = {r.validator_address for r in rows}
        for addr in addresses:
            self.assertTrue(addr.startswith("0x"))
            self.assertGreater(len(addr), 10)

    def test_skips_row_with_missing_stake(self) -> None:
        """A validator missing stakingPoolSuiBalance is skipped (stake is NOT NULL)."""
        ss = {
            "epoch": "100",
            "epochStartTimestampMs": "1780000000000",
            "activeValidators": [
                {
                    "suiAddress": "0x" + "e" * 64,
                    "name": "Stakeless",
                    # stakingPoolSuiBalance omitted
                },
            ],
        }
        rows = parse_validator_rows(ss, {"epoch": "100", "apys": []})
        self.assertEqual(rows, [])

    def test_apy_payload_missing_apys_list_treated_as_empty(self) -> None:
        """A malformed APY payload doesn't break extraction — validators land sans APY."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, {"epoch": "1151"})
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIsNone(r.apy)

    def test_apy_payload_completely_missing_treated_as_empty(self) -> None:
        """A None APY payload (network failure on the second RPC) is tolerated."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, None)
        # Note: this is a soft path — caller is free to record a partial run.
        # parse_validator_rows shouldn't raise on a missing APY payload.
        self.assertEqual(len(rows), 2)

    def test_missing_epoch_raises(self) -> None:
        """A system state without epoch is unusable — raise loudly."""
        bad = dict(SAMPLE_SYSTEM_STATE)
        del bad["epoch"]
        with self.assertRaises(ValueError):
            parse_validator_rows(bad, SAMPLE_APY_PAYLOAD)

    def test_missing_epoch_start_ts_raises(self) -> None:
        """A system state without epochStartTimestampMs is unusable — raise."""
        bad = dict(SAMPLE_SYSTEM_STATE)
        del bad["epochStartTimestampMs"]
        with self.assertRaises(ValueError):
            parse_validator_rows(bad, SAMPLE_APY_PAYLOAD)

    def test_non_dict_system_state_raises(self) -> None:
        """A list payload (defensive) raises — the system state must be a JSON object."""
        with self.assertRaises(ValueError):
            parse_validator_rows([], SAMPLE_APY_PAYLOAD)

    def test_active_validators_not_a_list_raises(self) -> None:
        """A malformed activeValidators field raises — not silently empty."""
        bad = dict(SAMPLE_SYSTEM_STATE)
        bad["activeValidators"] = "string-not-list"
        with self.assertRaises(ValueError):
            parse_validator_rows(bad, SAMPLE_APY_PAYLOAD)

    def test_commission_rate_in_basis_points(self) -> None:
        """commissionRate is published as integer basis points (200 = 2%)."""
        rows = parse_validator_rows(SAMPLE_SYSTEM_STATE, SAMPLE_APY_PAYLOAD)
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["Mysten-1"].commission_rate_bps, 200)
        self.assertEqual(by_name["Coinbase"].commission_rate_bps, 1000)


class CollectTests(unittest.TestCase):
    """Collector orchestration paths that are not covered by parser-only tests."""

    def test_keeps_validator_snapshots_when_apy_rpc_fails(self) -> None:
        """APY RPC failure is partial; system-state rows still land with apy=None."""

        def fake_rpc_post(_http: object, method: str, params: object = None) -> object:
            if method == METHOD_SYSTEM_STATE:
                return SAMPLE_SYSTEM_STATE
            if method == METHOD_VALIDATORS_APY:
                raise ValueError("apy rpc down")
            raise AssertionError(f"unexpected method {method!r}")

        class FakeRun:
            id = 42

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        with (
            patch("genkei.ingest.sui_staking._rpc_post", side_effect=fake_rpc_post),
            patch("genkei.ingest.sui_staking.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.sui_staking.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.sui_staking.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.sui_staking.db.connection") as connection_cm,
            patch(
                "genkei.ingest.sui_staking.db.bulk_upsert", return_value=2
            ) as bulk_upsert,
        ):
            ingest_run_cm.return_value.__enter__.return_value = fake_run
            ingest_run_cm.return_value.__exit__.return_value = False

            self.assertEqual(collect(http=object()), 42)

        partial.assert_called_once_with(
            42,
            [
                {
                    "name": METHOD_VALIDATORS_APY,
                    "url": SUI_RPC_URL,
                    "error": "apy rpc down",
                }
            ],
        )
        store_blob.assert_called_once_with(
            42, METHOD_SYSTEM_STATE, SUI_RPC_URL, SAMPLE_SYSTEM_STATE
        )
        connection_cm.assert_called_once()
        bulk_rows = bulk_upsert.call_args.args[2]
        self.assertEqual(len(bulk_rows), 2)
        self.assertTrue(all(row["apy"] is None for row in bulk_rows))
        self.assertEqual(fake_run._added, 2)

    def test_records_parse_failures_before_reraising(self) -> None:
        """Malformed system-state shapes are recorded in partial_endpoints."""
        bad_system_state = dict(SAMPLE_SYSTEM_STATE)
        bad_system_state["activeValidators"] = "not-a-list"

        def fake_rpc_post(_http: object, method: str, params: object = None) -> object:
            if method == METHOD_SYSTEM_STATE:
                return bad_system_state
            if method == METHOD_VALIDATORS_APY:
                return SAMPLE_APY_PAYLOAD
            raise AssertionError(f"unexpected method {method!r}")

        class FakeRun:
            id = 43

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        with (
            patch("genkei.ingest.sui_staking._rpc_post", side_effect=fake_rpc_post),
            patch("genkei.ingest.sui_staking.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.sui_staking.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.sui_staking.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.sui_staking.db.connection") as connection_cm,
            patch("genkei.ingest.sui_staking.db.bulk_upsert") as bulk_upsert,
        ):
            ingest_run_cm.return_value.__enter__.return_value = fake_run
            ingest_run_cm.return_value.__exit__.return_value = False

            with self.assertRaisesRegex(RuntimeError, "Sui payload parse failed"):
                collect(http=object())

        self.assertEqual(store_blob.call_count, 2)
        partial_args = partial.call_args.args
        self.assertEqual(partial_args[0], 43)
        self.assertEqual(partial_args[1][0]["name"], COLLECT_ENDPOINT_LABEL)
        self.assertIn("activeValidators", partial_args[1][0]["error"])
        connection_cm.assert_not_called()
        bulk_upsert.assert_not_called()
        self.assertFalse(hasattr(fake_run, "_added"))


if __name__ == "__main__":
    unittest.main()
