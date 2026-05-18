"""Unit tests for the on-chain staking event ingester (B-082)."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.onchain_staking import (
    CHAINLINK_V02_POOL,
    ETHERSCAN_API_KEY_ENV,
    EVENT_TOPIC_STAKED,
    EVENT_TOPIC_UNSTAKED,
    LINK_DECIMALS,
    decode_amount_token,
    event_type_for_topic,
    fetch_logs,
    hex_to_address,
    hex_to_int,
    iter_block_chunks,
    parse_log,
    resolve_api_key,
)

NOW = datetime(2026, 5, 17, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Pure hex / encoding helpers
# ---------------------------------------------------------------------------


class HexHelpersTests(unittest.TestCase):
    def test_hex_to_int_handles_canonical_forms(self) -> None:
        self.assertEqual(hex_to_int("0x10"), 16)
        self.assertEqual(hex_to_int("0xdeadbeef"), 0xDEADBEEF)
        # Empty / "0x" / non-hex strings round-trip to 0 rather than raise
        self.assertEqual(hex_to_int("0x"), 0)
        self.assertEqual(hex_to_int(""), 0)

    def test_hex_to_address_extracts_last_20_bytes(self) -> None:
        # 32-byte topic, address is the last 20 bytes (40 hex chars)
        topic = "0x000000000000000000000000bc10f2e862ed4502144c7d632a3459f49dfcdb5e"
        self.assertEqual(
            hex_to_address(topic), "0xbc10f2e862ed4502144c7d632a3459f49dfcdb5e"
        )

    def test_hex_to_address_lowercases(self) -> None:
        # Mixed-case input is normalized down to lowercase
        topic = "0x000000000000000000000000BC10f2E862ED4502144c7d632a3459F49DFCDB5E"
        self.assertEqual(
            hex_to_address(topic), "0xbc10f2e862ed4502144c7d632a3459f49dfcdb5e"
        )

    def test_hex_to_address_rejects_short_payload(self) -> None:
        self.assertEqual(hex_to_address("0xabcd"), "")
        self.assertEqual(hex_to_address(""), "")


class DecodeAmountTests(unittest.TestCase):
    def test_decodes_18_decimal_token_amount(self) -> None:
        # 1 LINK = 10**18 wei = 0xde0b6b3a7640000
        one_link_hex = "0x" + format(10**18, "064x")
        self.assertEqual(decode_amount_token(one_link_hex, LINK_DECIMALS), Decimal(1))

    def test_decodes_fractional_link(self) -> None:
        # 1.5 LINK = 1.5 * 10**18 wei
        half_and_one_hex = "0x" + format(int(1.5 * 10**18), "064x")
        self.assertEqual(
            decode_amount_token(half_and_one_hex, LINK_DECIMALS),
            Decimal("1.5"),
        )

    def test_decodes_only_first_32_byte_word(self) -> None:
        # If `data` carries multiple words (e.g. a non-indexed second
        # arg), only the first word is the amount.
        first = format(123 * 10**18, "064x")
        second = format(999, "064x")  # ignored by the decoder
        self.assertEqual(
            decode_amount_token("0x" + first + second, LINK_DECIMALS),
            Decimal(123),
        )


# ---------------------------------------------------------------------------
# Event-topic classification
# ---------------------------------------------------------------------------


class EventTypeForTopicTests(unittest.TestCase):
    def test_staked_topic_returns_staked(self) -> None:
        self.assertEqual(event_type_for_topic(EVENT_TOPIC_STAKED), "staked")

    def test_unstaked_topic_returns_unstaked(self) -> None:
        self.assertEqual(event_type_for_topic(EVENT_TOPIC_UNSTAKED), "unstaked")

    def test_unknown_topic_returns_none(self) -> None:
        # Other events from the same contract (e.g. RewardsAdded) get
        # skipped at parse time rather than mis-decoded.
        self.assertIsNone(
            event_type_for_topic("0x0000000000000000000000000000000000000000000000000000000000000001")
        )


# ---------------------------------------------------------------------------
# Log → row decoding
# ---------------------------------------------------------------------------


def _build_log(
    *,
    topic0: str,
    staker_topic: str = "0x000000000000000000000000aabbccddeeff00112233445566778899aabbccdd",
    amount_wei: int = 10**18,
    block_number: int = 0x12_34_56,
    timestamp: int = 0x67_50_00_00,
    tx_hash: str = "0xtxhash",
    log_index: int = 0x5,
) -> dict[str, object]:
    return {
        "topics": [topic0, staker_topic],
        "data": "0x" + format(amount_wei, "064x"),
        "blockNumber": format(block_number, "#x"),
        "timeStamp": format(timestamp, "#x"),
        "transactionHash": tx_hash,
        "logIndex": format(log_index, "#x"),
    }


class ParseLogTests(unittest.TestCase):
    def test_parses_staked_event(self) -> None:
        row = parse_log(
            _build_log(topic0=EVENT_TOPIC_STAKED, amount_wei=42 * 10**18),
            pool=CHAINLINK_V02_POOL,
            source_endpoint="https://example.com",
            ingest_run_id=99,
            fetched_at=NOW,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["event_type"], "staked")
        self.assertEqual(row["protocol_slug"], "chainlink-v02")
        self.assertEqual(row["chain"], "ethereum")
        self.assertEqual(row["amount_token"], Decimal(42))
        self.assertEqual(row["staker_address"], "0xaabbccddeeff00112233445566778899aabbccdd")
        self.assertEqual(row["ingest_run_id"], 99)
        # USD value is None at ingest; backfilled later via price join.
        self.assertIsNone(row["amount_usd"])

    def test_parses_unstaked_event(self) -> None:
        row = parse_log(
            _build_log(topic0=EVENT_TOPIC_UNSTAKED, amount_wei=7 * 10**18),
            pool=CHAINLINK_V02_POOL,
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        assert row is not None
        self.assertEqual(row["event_type"], "unstaked")
        self.assertEqual(row["amount_token"], Decimal(7))

    def test_skips_unknown_event_type(self) -> None:
        # Other contract events (RewardsAdded, etc.) return None — the
        # collector silently drops them rather than logging noise.
        unknown_topic = (
            "0x0000000000000000000000000000000000000000000000000000000000000abc"
        )
        row = parse_log(
            _build_log(topic0=unknown_topic),
            pool=CHAINLINK_V02_POOL,
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        self.assertIsNone(row)

    def test_skips_log_without_topics(self) -> None:
        bad = {
            "data": "0x",
            "blockNumber": "0x1",
            "timeStamp": "0x1",
            "transactionHash": "0xa",
            "logIndex": "0x0",
        }
        self.assertIsNone(
            parse_log(
                bad,
                pool=CHAINLINK_V02_POOL,
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            )
        )

    def test_skips_log_with_missing_staker_topic(self) -> None:
        # Only topic0 present — no indexed staker arg.
        bad = {
            "topics": [EVENT_TOPIC_STAKED],
            "data": "0x" + format(10**18, "064x"),
            "blockNumber": "0x1",
            "timeStamp": "0x1",
            "transactionHash": "0xa",
            "logIndex": "0x0",
        }
        self.assertIsNone(
            parse_log(
                bad,
                pool=CHAINLINK_V02_POOL,
                source_endpoint="x",
                ingest_run_id=1,
                fetched_at=NOW,
            )
        )


# ---------------------------------------------------------------------------
# Block-range chunking
# ---------------------------------------------------------------------------


class IterBlockChunksTests(unittest.TestCase):
    def test_single_chunk_for_small_range(self) -> None:
        chunks = iter_block_chunks(from_block=100, to_block=200, chunk_size=1000)
        self.assertEqual(chunks, [(100, 200)])

    def test_splits_long_range_into_chunks(self) -> None:
        chunks = iter_block_chunks(from_block=0, to_block=4, chunk_size=2)
        # Windows are inclusive on both ends → [0-1], [2-3], [4-4]
        self.assertEqual(chunks, [(0, 1), (2, 3), (4, 4)])

    def test_handles_exact_multiple(self) -> None:
        chunks = iter_block_chunks(from_block=10, to_block=29, chunk_size=10)
        self.assertEqual(chunks, [(10, 19), (20, 29)])


# ---------------------------------------------------------------------------
# Graceful-skip-when-no-key path (mirrors CoinGecko keyless from D-020)
# ---------------------------------------------------------------------------


class ResolveApiKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop(ETHERSCAN_API_KEY_ENV, None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ[ETHERSCAN_API_KEY_ENV] = self._saved
        else:
            os.environ.pop(ETHERSCAN_API_KEY_ENV, None)

    def test_returns_none_when_unset(self) -> None:
        self.assertIsNone(resolve_api_key())

    def test_returns_none_when_empty(self) -> None:
        os.environ[ETHERSCAN_API_KEY_ENV] = ""
        self.assertIsNone(resolve_api_key())

    def test_returns_none_when_whitespace(self) -> None:
        os.environ[ETHERSCAN_API_KEY_ENV] = "   "
        self.assertIsNone(resolve_api_key())

    def test_returns_trimmed_value_when_set(self) -> None:
        os.environ[ETHERSCAN_API_KEY_ENV] = "  ABC123  "
        self.assertEqual(resolve_api_key(), "ABC123")


# ---------------------------------------------------------------------------
# Etherscan API-response handling
# ---------------------------------------------------------------------------


class FetchLogsTests(unittest.TestCase):
    def test_success_returns_result_list(self) -> None:
        sentinel = [{"x": 1}, {"x": 2}]

        class FakeHttp:
            def get_json(self, url: str) -> object:  # noqa: ARG002
                return {"status": "1", "message": "OK", "result": sentinel}

        result = fetch_logs(
            FakeHttp(),  # type: ignore[arg-type]
            api_key="k",
            pool=CHAINLINK_V02_POOL,
            from_block=1,
            to_block=10,
        )
        self.assertEqual(result, sentinel)

    def test_no_records_found_returns_empty_not_raise(self) -> None:
        # Etherscan signals "no events in this range" with status=0 +
        # result="No records found" — that's a benign empty, not an error.
        class FakeHttp:
            def get_json(self, url: str) -> object:  # noqa: ARG002
                return {"status": "0", "message": "No records found", "result": "No records found"}

        self.assertEqual(
            fetch_logs(
                FakeHttp(),  # type: ignore[arg-type]
                api_key="k",
                pool=CHAINLINK_V02_POOL,
                from_block=1,
                to_block=10,
            ),
            [],
        )

    def test_real_api_error_raises(self) -> None:
        class FakeHttp:
            def get_json(self, url: str) -> object:  # noqa: ARG002
                return {"status": "0", "message": "NOTOK", "result": "Missing/Invalid API Key"}

        with self.assertRaisesRegex(RuntimeError, "Missing/Invalid API Key"):
            fetch_logs(
                FakeHttp(),  # type: ignore[arg-type]
                api_key="bogus",
                pool=CHAINLINK_V02_POOL,
                from_block=1,
                to_block=10,
            )


# ---------------------------------------------------------------------------
# collect() — graceful skip on missing key
# ---------------------------------------------------------------------------


class CollectGracefulSkipTests(unittest.TestCase):
    def test_no_api_key_records_zero_row_run_and_returns_cleanly(self) -> None:
        # No DB call should happen for fetch_logs / latest_block_for_pool
        # in this code path — the function bails after recording the run.
        with (
            patch("genkei.ingest.onchain_staking.resolve_api_key", return_value=None),
            patch("genkei.ingest.onchain_staking.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.onchain_staking.fetch_current_head_block") as head_mock,
            patch("genkei.ingest.onchain_staking.fetch_logs") as fetch_mock,
        ):
            # Mock the context-manager shape of db.ingest_run
            class FakeRun:
                id = 42

                def add_rows(self, n: int) -> None:
                    self._added = n

            fake = FakeRun()
            ingest_run_cm.return_value.__enter__.return_value = fake
            ingest_run_cm.return_value.__exit__.return_value = False

            from genkei.ingest.onchain_staking import collect

            result = collect(http=_MockHttp())
            self.assertEqual(result, 42)
            # Bailed before any Etherscan call:
            head_mock.assert_not_called()
            fetch_mock.assert_not_called()
            # Run was recorded with 0 rows added:
            self.assertEqual(fake._added, 0)
            # And metadata reflects the missing-key situation:
            metadata = ingest_run_cm.call_args.kwargs.get("metadata", {})
            self.assertFalse(metadata.get("has_api_key"))


class _MockHttp:
    """Stand-in HttpClient for tests that shouldn't actually touch the network."""

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
