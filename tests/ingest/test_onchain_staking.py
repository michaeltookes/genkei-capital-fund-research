"""Unit tests for the on-chain staking event ingester (B-082)."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.onchain_staking import (
    BLOCK_CHUNK_SIZE,
    CHAINLINK_V01_POOL,
    CHAINLINK_V01_POOL_ADDRESS,
    CHAINLINK_V02_OPERATOR_POOL,
    CHAINLINK_V02_OPERATOR_POOL_ADDRESS,
    CHAINLINK_V02_POOL,
    CHAINLINK_V02_POOL_ADDRESS,
    DEFAULT_POOLS,
    ETHERSCAN_API_KEY_ENV,
    EVENT_TOPIC_OPERATOR_REMOVED,
    EVENT_TOPIC_SLASHED,
    EVENT_TOPIC_STAKED,
    EVENT_TOPIC_UNBONDING_STARTED,
    EVENT_TOPIC_UNSTAKED,
    LINK_DECIMALS,
    V01_TOPIC_MIGRATED,
    V01_TOPIC_STAKED,
    _insert_rows,
    collect,
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

    def test_unbonding_started_topic_returns_unbonding_started(self) -> None:
        self.assertEqual(
            event_type_for_topic(EVENT_TOPIC_UNBONDING_STARTED), "unbonding_started"
        )

    def test_operator_removed_topic_returns_operator_removed(self) -> None:
        self.assertEqual(
            event_type_for_topic(EVENT_TOPIC_OPERATOR_REMOVED), "operator_removed"
        )

    def test_slashed_topic_returns_slashed(self) -> None:
        self.assertEqual(event_type_for_topic(EVENT_TOPIC_SLASHED), "slashed")

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
        self.assertEqual(row["source_endpoint"], "https://example.com")
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

    def test_parses_unbonding_started_event_with_zero_amount(self) -> None:
        # UnbondingPeriodStarted has no data payload (no amount) — captured
        # as an intent signal with amount_token=0 so the row still records
        # the event for future analysis without polluting flow aggregates.
        row = parse_log(
            # data="0x" simulates an empty data field (no amount word)
            {
                "topics": [
                    EVENT_TOPIC_UNBONDING_STARTED,
                    "0x000000000000000000000000aabbccddeeff00112233445566778899aabbccdd",
                ],
                "data": "0x",
                "blockNumber": "0x1",
                "timeStamp": "0x67500000",
                "transactionHash": "0xabc",
                "logIndex": "0x0",
            },
            pool=CHAINLINK_V02_POOL,
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )
        assert row is not None
        self.assertEqual(row["event_type"], "unbonding_started")
        self.assertEqual(row["amount_token"], Decimal(0))

    def test_parses_operator_removed_event_as_principal_reduction(self) -> None:
        # Deployed OperatorStakingPool ABI:
        # OperatorRemoved(address indexed operator, uint256 principal, uint256 newTotalPrincipal)
        principal = format(31 * 10**18, "064x")
        new_total = format(1_700_000 * 10**18, "064x")
        log = _build_log(topic0=EVENT_TOPIC_OPERATOR_REMOVED)
        log["data"] = "0x" + principal + new_total

        row = parse_log(
            log,
            pool=CHAINLINK_V02_OPERATOR_POOL,
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )

        assert row is not None
        self.assertEqual(row["event_type"], "operator_removed")
        self.assertEqual(row["protocol_slug"], "chainlink-v02-operator")
        self.assertEqual(row["amount_token"], Decimal(31))

    def test_parses_slashed_event_as_principal_reduction(self) -> None:
        # Deployed OperatorStakingPool ABI:
        # Slashed(address indexed operator, uint256 slashedAmount,
        #         uint256 updatedStakerPrincipal, uint256 newTotalPrincipal)
        slashed = format(2 * 10**18, "064x")
        updated_principal = format(28 * 10**18, "064x")
        new_total = format(1_699_998 * 10**18, "064x")
        log = _build_log(topic0=EVENT_TOPIC_SLASHED)
        log["data"] = "0x" + slashed + updated_principal + new_total

        row = parse_log(
            log,
            pool=CHAINLINK_V02_OPERATOR_POOL,
            source_endpoint="x",
            ingest_run_id=1,
            fetched_at=NOW,
        )

        assert row is not None
        self.assertEqual(row["event_type"], "slashed")
        self.assertEqual(row["protocol_slug"], "chainlink-v02-operator")
        self.assertEqual(row["amount_token"], Decimal(2))

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

    def test_fetches_all_pages_until_short_page(self) -> None:
        first_page = [{"x": i} for i in range(1000)]
        second_page = [{"x": 1000}]

        class FakeHttp:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def get_json(self, url: str) -> object:
                self.urls.append(url)
                if "page=1" in url:
                    return {"status": "1", "message": "OK", "result": first_page}
                if "page=2" in url:
                    return {"status": "1", "message": "OK", "result": second_page}
                raise AssertionError(f"unexpected URL: {url}")

        fake = FakeHttp()
        result = fetch_logs(
            fake,  # type: ignore[arg-type]
            api_key="k",
            pool=CHAINLINK_V02_POOL,
            from_block=1,
            to_block=10,
        )

        self.assertEqual(result, first_page + second_page)
        self.assertIn("offset=1000", fake.urls[0])
        self.assertIn("page=1", fake.urls[0])
        self.assertIn("page=2", fake.urls[1])

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

    def test_malformed_payload_raises(self) -> None:
        class FakeHttp:
            def get_json(self, url: str) -> object:  # noqa: ARG002
                return ["not", "a", "dict"]

        with self.assertRaisesRegex(RuntimeError, "malformed response"):
            fetch_logs(
                FakeHttp(),  # type: ignore[arg-type]
                api_key="k",
                pool=CHAINLINK_V02_POOL,
                from_block=1,
                to_block=10,
            )

    def test_malformed_second_page_raises_after_full_first_page(self) -> None:
        first_page = [{"x": i} for i in range(1000)]

        class FakeHttp:
            def get_json(self, url: str) -> object:
                if "page=1" in url:
                    return {"status": "1", "message": "OK", "result": first_page}
                return {"status": "1", "message": "OK", "result": "unexpected"}

        with self.assertRaisesRegex(RuntimeError, "malformed response"):
            fetch_logs(
                FakeHttp(),  # type: ignore[arg-type]
                api_key="k",
                pool=CHAINLINK_V02_POOL,
                from_block=1,
                to_block=10,
            )


# ---------------------------------------------------------------------------
# Resume state
# ---------------------------------------------------------------------------


class LatestBlockTests(unittest.TestCase):
    def test_latest_block_is_filtered_by_protocol_and_contract(self) -> None:
        from genkei.ingest.onchain_staking import latest_block_for_pool

        class FakeCursor:
            params: list[object] | None = None

            def __enter__(self) -> FakeCursor:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def execute(self, sql: str, params: list[object]) -> None:
                self.sql = sql
                self.params = params

            def fetchone(self) -> tuple[int]:
                return (123,)

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_obj = FakeCursor()

            def __enter__(self) -> FakeConnection:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def cursor(self) -> FakeCursor:
                return self.cursor_obj

        fake_conn = FakeConnection()

        with patch(
            "genkei.ingest.onchain_staking.db.connection", return_value=fake_conn
        ):
            self.assertEqual(latest_block_for_pool(CHAINLINK_V02_POOL), 123)

        self.assertIn("contract_address = %s", fake_conn.cursor_obj.sql)
        self.assertEqual(
            fake_conn.cursor_obj.params,
            [CHAINLINK_V02_POOL.protocol_slug, CHAINLINK_V02_POOL.contract_address.lower()],
        )


# ---------------------------------------------------------------------------
# Insert accounting
# ---------------------------------------------------------------------------


class InsertRowsTests(unittest.TestCase):
    def test_insert_rows_returns_actual_rowcount(self) -> None:
        class FakeCursor:
            rowcount = 0

            def __enter__(self) -> FakeCursor:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def executemany(self, sql: str, values: list[tuple[object, ...]]) -> None:
                self.sql = sql
                self.values = values
                self.rowcount = len(values)

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_obj = FakeCursor()

            def __enter__(self) -> FakeConnection:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def cursor(self) -> FakeCursor:
                return self.cursor_obj

        row = {
            "tx_hash": "0xabc",
            "log_index": 1,
            "chain": "ethereum",
            "protocol_slug": "chainlink-v02",
            "contract_address": CHAINLINK_V02_POOL.contract_address.lower(),
            "block_number": 123,
            "block_timestamp": NOW,
            "event_type": "staked",
            "staker_address": "0xaabbccddeeff00112233445566778899aabbccdd",
            "amount_token": Decimal("1.5"),
            "amount_usd": None,
            "source_endpoint": "https://example.com",
            "fetched_at": NOW,
            "ingest_run_id": 42,
        }
        fake_conn = FakeConnection()

        with patch(
            "genkei.ingest.onchain_staking.db.connection", return_value=fake_conn
        ):
            self.assertEqual(_insert_rows([row]), 1)


# ---------------------------------------------------------------------------
# collect()
# ---------------------------------------------------------------------------


class CollectTests(unittest.TestCase):
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

    def test_chunk_fetch_failure_fails_run_without_row_count(self) -> None:
        with (
            patch("genkei.ingest.onchain_staking.db.ingest_run") as ingest_run_cm,
            patch(
                "genkei.ingest.onchain_staking.fetch_current_head_block",
                return_value=CHAINLINK_V02_POOL.deployment_block + 1,
            ),
            patch("genkei.ingest.onchain_staking.latest_block_for_pool", return_value=None),
            patch(
                "genkei.ingest.onchain_staking.fetch_logs",
                side_effect=RuntimeError("rate limit"),
            ) as fetch_mock,
        ):
            class FakeRun:
                id = 42

                def add_rows(self, n: int) -> None:
                    self._added = n

            fake = FakeRun()
            ingest_run_cm.return_value.__enter__.return_value = fake
            ingest_run_cm.return_value.__exit__.return_value = False

            with self.assertRaisesRegex(RuntimeError, "rate limit"):
                collect(http=_MockHttp(), api_key="k", pools=[CHAINLINK_V02_POOL])

            fetch_mock.assert_called_once()
            self.assertFalse(hasattr(fake, "_added"))

    def test_fetched_at_is_captured_per_successful_chunk(self) -> None:
        first_fetched_at = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
        second_fetched_at = datetime(2026, 5, 17, 12, 5, tzinfo=timezone.utc)
        timestamps = [first_fetched_at, second_fetched_at]
        inserted_batches: list[list[dict[str, object]]] = []

        class FakeDateTime:
            @classmethod
            def now(cls, tz: timezone) -> datetime:  # noqa: ARG003
                return timestamps.pop(0)

            @classmethod
            def fromtimestamp(cls, value: int, tz: timezone) -> datetime:
                return datetime.fromtimestamp(value, tz=tz)

        def fake_fetch_logs(
            *args: object, from_block: int, **kwargs: object
        ) -> list[dict[str, object]]:
            tx_hash = (
                "0xfirst"
                if from_block == CHAINLINK_V02_POOL.deployment_block
                else "0xsecond"
            )
            return [_build_log(topic0=EVENT_TOPIC_STAKED, tx_hash=tx_hash)]

        def fake_insert_rows(rows: list[dict[str, object]]) -> int:
            inserted_batches.append(rows)
            return len(rows)

        with (
            patch("genkei.ingest.onchain_staking.db.ingest_run") as ingest_run_cm,
            patch(
                "genkei.ingest.onchain_staking.fetch_current_head_block",
                return_value=CHAINLINK_V02_POOL.deployment_block + BLOCK_CHUNK_SIZE,
            ),
            patch("genkei.ingest.onchain_staking.latest_block_for_pool", return_value=None),
            patch("genkei.ingest.onchain_staking.fetch_logs", side_effect=fake_fetch_logs),
            patch("genkei.ingest.onchain_staking._insert_rows", side_effect=fake_insert_rows),
            patch("genkei.ingest.onchain_staking.datetime", FakeDateTime),
        ):
            class FakeRun:
                id = 42

                def add_rows(self, n: int) -> None:
                    self._added = n

            fake = FakeRun()
            ingest_run_cm.return_value.__enter__.return_value = fake
            ingest_run_cm.return_value.__exit__.return_value = False

            self.assertEqual(
                collect(http=_MockHttp(), api_key="k", pools=[CHAINLINK_V02_POOL]),
                42,
            )

        self.assertEqual(len(inserted_batches), 2)
        self.assertEqual(inserted_batches[0][0]["fetched_at"], first_fetched_at)
        self.assertEqual(inserted_batches[1][0]["fetched_at"], second_fetched_at)
        self.assertEqual(fake._added, 2)


class _MockHttp:
    """Stand-in HttpClient for tests that shouldn't actually touch the network."""

    def close(self) -> None:
        pass


def _build_v01_log(
    *,
    topic0: str,
    staker: str = "0xaabbccddeeff00112233445566778899aabbccdd",
    principal_wei: int = 7000 * 10**18,
    extra_words: int = 2,
    block_number: int = 0x10_00_00,
    timestamp: int = 0x63_88_00_00,
    tx_hash: str = "0xv01tx",
    log_index: int = 0x2,
) -> dict[str, object]:
    """A v0.1-shaped log: staker NOT indexed (data word 0), principal word 1.

    ``extra_words`` pads the tail (v0.1 Unstaked/Migrated carry reward words +
    a dynamic-bytes tail after the principal) to prove word-1 decoding is
    unaffected by later data.
    """
    staker_word = format(int(staker, 16), "064x")
    principal_word = format(principal_wei, "064x")
    data = "0x" + staker_word + principal_word + ("00" * 32) * extra_words
    return {
        "topics": [topic0],  # no staker topic — the v0.1 distinction
        "data": data,
        "blockNumber": format(block_number, "#x"),
        "timeStamp": format(timestamp, "#x"),
        "transactionHash": tx_hash,
        "logIndex": format(log_index, "#x"),
    }


class ParseLogV01Tests(unittest.TestCase):
    """v0.1 legacy pool decoding — non-indexed staker, principal in word 1."""

    def _parse(self, log: dict) -> dict | None:
        return parse_log(
            log,
            pool=CHAINLINK_V01_POOL,
            source_endpoint="https://api.etherscan.io/v2/api",
            ingest_run_id=1,
            fetched_at=NOW,
        )

    def test_parses_v01_staked_with_data0_staker_and_word1_amount(self) -> None:
        row = self._parse(
            _build_v01_log(topic0=V01_TOPIC_STAKED, principal_wei=7000 * 10**18)
        )
        assert row is not None
        self.assertEqual(row["event_type"], "staked")
        self.assertEqual(row["protocol_slug"], "chainlink-v01")
        self.assertEqual(row["staker_address"], "0xaabbccddeeff00112233445566778899aabbccdd")
        self.assertEqual(row["amount_token"], Decimal(7000))

    def test_parses_v01_migrated_as_its_own_event_type(self) -> None:
        row = self._parse(
            _build_v01_log(topic0=V01_TOPIC_MIGRATED, principal_wei=1500 * 10**18)
        )
        assert row is not None
        self.assertEqual(row["event_type"], "migrated")
        self.assertEqual(row["amount_token"], Decimal(1500))

    def test_v01_unstaked_shares_topic_but_decodes_word1(self) -> None:
        # v0.1 Unstaked shares v0.2's topic0, but here the staker is in data
        # word 0 and the principal in word 1 — the per-pool spec resolves it.
        row = self._parse(
            _build_v01_log(topic0=EVENT_TOPIC_UNSTAKED, principal_wei=42 * 10**18)
        )
        assert row is not None
        self.assertEqual(row["event_type"], "unstaked")
        self.assertEqual(row["amount_token"], Decimal(42))

    def test_v01_ignores_v02_indexed_staked_topic(self) -> None:
        # The v0.2 Staked topic is not in the v0.1 events map → skipped.
        self.assertIsNone(
            self._parse(_build_v01_log(topic0=EVENT_TOPIC_STAKED))
        )


# ---------------------------------------------------------------------------
# DEFAULT_POOLS surface (B-086)
# ---------------------------------------------------------------------------


class DefaultPoolsTests(unittest.TestCase):
    """Pin the multi-pool surface so accidentally dropping a pool surfaces in CI.

    DefiLlama's chainlink-staking adapter lists exactly 3 LINK-balance owners
    (verified by fetching projects/chainlink/index.js from the DefiLlama-
    Adapters repo on 2026-06-07). DEFAULT_POOLS now covers all three: the two
    v0.2 contracts (shared event signatures) plus the v0.1 legacy contract
    (B-116 — different, per-pool event layout via PoolConfig.events).
    """

    def test_default_pools_carry_all_three_contracts(self) -> None:
        """v0.2 community + v0.2 operator + v0.1 legacy all ship by default."""
        slugs = sorted(p.protocol_slug for p in DEFAULT_POOLS)
        self.assertEqual(
            slugs, ["chainlink-v01", "chainlink-v02", "chainlink-v02-operator"]
        )

    def test_v02_pool_addresses_are_distinct(self) -> None:
        """A typo making both PoolConfig entries point at the same address would
        silently re-ingest the same events under two slugs."""
        addresses = {p.contract_address.lower() for p in DEFAULT_POOLS}
        self.assertEqual(len(addresses), len(DEFAULT_POOLS))

    def test_operator_pool_uses_v02_base_event_topics(self) -> None:
        """Operator pool reuses the v0.2 base topics — verified live 2026-06-07.

        Pinned to prevent a future contributor from accidentally setting
        different topic constants per-pool without first extending PoolConfig
        with per-pool topic overrides.
        """
        self.assertEqual(
            CHAINLINK_V02_OPERATOR_POOL.contract_address,
            CHAINLINK_V02_OPERATOR_POOL_ADDRESS,
        )
        self.assertEqual(
            CHAINLINK_V02_OPERATOR_POOL.token_decimals, LINK_DECIMALS
        )
        # Same deployment_block as community pool — both v0.2 contracts
        # were deployed at block 18572190 on 2023-11-14.
        self.assertEqual(
            CHAINLINK_V02_OPERATOR_POOL.deployment_block,
            CHAINLINK_V02_POOL.deployment_block,
        )

    def test_operator_pool_principal_reduction_topics_are_pinned(self) -> None:
        """Operator-only topics come from the deployed Etherscan ABI.

        These are not emitted by the community pool but must stay parsed while
        the operator pool remains in DEFAULT_POOLS.
        """
        self.assertEqual(
            EVENT_TOPIC_OPERATOR_REMOVED,
            "0xd8572c381824ffffebc7dcf1cc25a094eedc7498e31f3ddfd0a82d4ffa026e9d",
        )
        self.assertEqual(
            EVENT_TOPIC_SLASHED,
            "0x23ee33e2cc85d581547d857dc227450a3e2ef8666fa2faa5b13f0a0893e4d4ad",
        )

    def test_v02_pool_addresses_match_module_constants(self) -> None:
        """Module-level address constants are the canonical source of truth."""
        self.assertEqual(
            CHAINLINK_V02_POOL.contract_address, CHAINLINK_V02_POOL_ADDRESS
        )
        self.assertEqual(
            CHAINLINK_V02_OPERATOR_POOL.contract_address,
            CHAINLINK_V02_OPERATOR_POOL_ADDRESS,
        )

    def test_v01_pool_is_enabled_in_default_pools(self) -> None:
        """B-116: v0.1 legacy contract is now wired into DEFAULT_POOLS."""
        self.assertEqual(
            CHAINLINK_V01_POOL_ADDRESS,
            "0x3feB1e09b4bb0E7f0387CeE092a52e85797ab889",
        )
        addresses_in_defaults = {p.contract_address.lower() for p in DEFAULT_POOLS}
        self.assertIn(CHAINLINK_V01_POOL_ADDRESS.lower(), addresses_in_defaults)
        v01 = next(p for p in DEFAULT_POOLS if p.protocol_slug == "chainlink-v01")
        self.assertEqual(v01.deployment_block, 16083969)
        # v0.1 uses the non-indexed-staker layout: staker in data word 0.
        staked = v01.events[V01_TOPIC_STAKED.lower()]
        self.assertEqual(staked.event_type, "staked")
        self.assertEqual(staked.staker_source, "data0")
        self.assertEqual(staked.amount_word, 1)


if __name__ == "__main__":
    unittest.main()
