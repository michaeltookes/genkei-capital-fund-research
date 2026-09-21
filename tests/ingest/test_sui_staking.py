"""Unit tests for the Sui staking collector (B-088; GraphQL port B-145)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from genkei.ingest.sui_staking import (
    ACTIVE_VALIDATORS_QUERY,
    COLLECT_ENDPOINT_LABEL,
    SOURCE_NAME,
    SUI_GRAPHQL_URL,
    VALIDATORS_PAGE_SIZE,
    _coerce_decimal,
    _coerce_int,
    _graphql_post,
    _iso_to_utc_datetime,
    collect,
    fetch_active_validators,
    parse_validator_rows,
)

# Minimal-but-realistic ValidatorV1 Move-struct payloads as served by the
# GraphQL API's activeValidators.nodes[].contents.json (live shape probed
# 2026-09-18, epoch 1255). u64s arrive as JSON strings; field names are the
# on-chain snake_case, unlike the retired JSON-RPC camelCase.
SAMPLE_EPOCH_ID = 1255
SAMPLE_START_TIMESTAMP = "2026-09-19T00:05:39.687Z"

SAMPLE_VALIDATOR_MYSTEN = {
    "metadata": {
        "sui_address": "0xmysten01" + "a" * 56,
        "name": "Mysten-1",
        "description": "First-party validator",
        "net_address": "/dns/mysten-1.example/tcp/8080/http",
    },
    "voting_power": "302",
    "gas_price": "750",
    "commission_rate": "200",
    "next_epoch_stake": "218400000000000000",
    "next_epoch_gas_price": "750",
    "next_epoch_commission_rate": "200",
    "staking_pool": {
        "id": "0x" + "1" * 64,
        "activation_epoch": "0",
        "deactivation_epoch": None,
        "sui_balance": "218350824320000000",
        "rewards_pool": "649057174720907",
        "pool_token_balance": "218000000000000000",
        "pending_stake": "5000000000000",
        "pending_total_sui_withdraw": "1100000000000",
        "pending_pool_token_withdraw": "1000000000000",
    },
}

SAMPLE_VALIDATOR_COINBASE = {
    "metadata": {
        "sui_address": "0xcoinbase" + "b" * 57,
        "name": "Coinbase",
    },
    "voting_power": "215",
    "gas_price": "910",
    "commission_rate": "1000",
    "next_epoch_stake": "155700000000000000",
    "staking_pool": {
        "id": "0x" + "2" * 64,
        "activation_epoch": "10",
        "deactivation_epoch": None,
        "sui_balance": "155708205570000000",
        "rewards_pool": "500000000000000",
        "pending_stake": "0",
        "pending_total_sui_withdraw": "10331972598",
        "pending_pool_token_withdraw": "9480325486",
    },
}

# Defective payload — missing metadata.sui_address — must be skipped
# silently without raising. Real upstream data has occasionally returned
# half-populated validator records during upgrades.
SAMPLE_VALIDATOR_DEFECTIVE = {
    "metadata": {"name": "Bad-Validator"},
    "voting_power": "5",
    "staking_pool": {"sui_balance": "1000000000000"},
}

SAMPLE_VALIDATORS = [
    SAMPLE_VALIDATOR_MYSTEN,
    SAMPLE_VALIDATOR_COINBASE,
    SAMPLE_VALIDATOR_DEFECTIVE,
]


def _page(
    nodes: list[dict],
    *,
    epoch_id: int = SAMPLE_EPOCH_ID,
    has_next: bool = False,
    end_cursor: str | None = None,
) -> dict:
    """Build one GraphQL ``data`` payload in the live response shape."""
    return {
        "epoch": {
            "epochId": epoch_id,
            "startTimestamp": SAMPLE_START_TIMESTAMP,
            "validatorSet": {
                "activeValidators": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                    "nodes": [{"contents": {"json": n}} for n in nodes],
                }
            },
        }
    }


class _FakeGraphqlHttp:
    """Feeds a scripted sequence of GraphQL response bodies."""

    def __init__(self, bodies: list[dict]) -> None:
        self._bodies = list(bodies)
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, **kwargs})
        body = self._bodies.pop(0)

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return body

        return _Resp()


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

    def test_graphql_url_is_public_mainnet(self) -> None:
        """B-145 port: JSON-RPC on public fullnodes is deprecated upstream."""
        self.assertEqual(SUI_GRAPHQL_URL, "https://graphql.mainnet.sui.io/graphql")

    def test_query_covers_epoch_and_validators(self) -> None:
        """The query must carry the epoch guard fields and the Move contents."""
        for needle in (
            "epochId",
            "startTimestamp",
            "activeValidators",
            "pageInfo",
            "contents",
        ):
            self.assertIn(needle, ACTIVE_VALIDATORS_QUERY)

    def test_page_size_positive(self) -> None:
        """Server serves 50/page; a non-positive size would loop forever."""
        self.assertGreater(VALIDATORS_PAGE_SIZE, 0)


# ---------------------------------------------------------------------------
# GraphQL helper
# ---------------------------------------------------------------------------


class GraphqlPostTests(unittest.TestCase):
    """GraphQL POST helper behavior."""

    def test_opts_into_retries_for_read_only_post(self) -> None:
        """GraphQL reads are read-only despite using POST."""
        http = _FakeGraphqlHttp([{"data": {"ok": True}}])

        self.assertEqual(_graphql_post(http, "query { ok }"), {"ok": True})

        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], SUI_GRAPHQL_URL)
        self.assertIs(call["retryable"], True)
        self.assertEqual(call["json"], {"query": "query { ok }", "variables": {}})

    def test_graphql_errors_raise(self) -> None:
        """A 200 body carrying GraphQL errors is an application failure."""
        http = _FakeGraphqlHttp(
            [{"data": None, "errors": [{"message": "Unknown field"}]}]
        )
        with self.assertRaisesRegex(ValueError, "Unknown field"):
            _graphql_post(http, "query { nope }")

    def test_missing_data_raises(self) -> None:
        """A body with neither data nor errors is malformed."""
        http = _FakeGraphqlHttp([{}])
        with self.assertRaisesRegex(ValueError, "missing 'data'"):
            _graphql_post(http, "query { ok }")


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class CoerceIntTests(unittest.TestCase):
    """The GraphQL MoveValue JSON encodes u64s as strings."""

    def test_string_digits(self) -> None:
        """Plain integer strings parse."""
        self.assertEqual(_coerce_int("1255"), 1255)
        self.assertEqual(_coerce_int("  1255  "), 1255)

    def test_native_int(self) -> None:
        """Native ints pass through (epochId arrives as a JSON number)."""
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


class IsoToUtcDatetimeTests(unittest.TestCase):
    """GraphQL startTimestamp (ISO-8601 with Z suffix) → UTC datetime."""

    def test_round_trip(self) -> None:
        """The live wire format decodes to the expected UTC moment."""
        out = _iso_to_utc_datetime("2026-09-19T00:05:39.687Z")
        self.assertEqual(
            out,
            datetime(2026, 9, 19, 0, 5, 39, 687000, tzinfo=timezone.utc),
        )
        self.assertEqual(out.tzinfo, timezone.utc)

    def test_explicit_offset_normalized_to_utc(self) -> None:
        """A non-UTC offset (defensive) is converted, not trusted verbatim."""
        out = _iso_to_utc_datetime("2026-09-19T02:05:39+02:00")
        self.assertEqual(out, datetime(2026, 9, 19, 0, 5, 39, tzinfo=timezone.utc))

    def test_none_returns_none(self) -> None:
        """Missing field yields None."""
        self.assertIsNone(_iso_to_utc_datetime(None))

    def test_garbage_returns_none(self) -> None:
        """Unparseable input yields None instead of raising."""
        self.assertIsNone(_iso_to_utc_datetime("not-a-timestamp"))


# ---------------------------------------------------------------------------
# fetch_active_validators — pagination
# ---------------------------------------------------------------------------


class FetchActiveValidatorsTests(unittest.TestCase):
    """Cursor pagination with the epoch-boundary guard."""

    def test_single_page(self) -> None:
        """A hasNextPage=false first page ends the loop after one request."""
        http = _FakeGraphqlHttp([{"data": _page(SAMPLE_VALIDATORS)}])

        epoch_id, start_ts, validators, pages = fetch_active_validators(http)

        self.assertEqual(epoch_id, SAMPLE_EPOCH_ID)
        self.assertEqual(start_ts, SAMPLE_START_TIMESTAMP)
        self.assertEqual(len(validators), 3)
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(http.calls), 1)
        self.assertIsNone(http.calls[0]["json"]["variables"]["after"])

    def test_two_pages_follow_cursor(self) -> None:
        """The endCursor from page 1 is passed as `after` on page 2."""
        http = _FakeGraphqlHttp(
            [
                {
                    "data": _page(
                        [SAMPLE_VALIDATOR_MYSTEN], has_next=True, end_cursor="c1"
                    )
                },
                {"data": _page([SAMPLE_VALIDATOR_COINBASE])},
            ]
        )

        epoch_id, _start_ts, validators, pages = fetch_active_validators(http)

        self.assertEqual(epoch_id, SAMPLE_EPOCH_ID)
        self.assertEqual(len(validators), 2)
        self.assertEqual(len(pages), 2)
        self.assertEqual(http.calls[1]["json"]["variables"]["after"], "c1")

    def test_epoch_change_mid_pagination_raises(self) -> None:
        """Never stitch two epochs into one snapshot — fail loudly instead."""
        http = _FakeGraphqlHttp(
            [
                {
                    "data": _page(
                        [SAMPLE_VALIDATOR_MYSTEN], has_next=True, end_cursor="c1"
                    )
                },
                {
                    "data": _page(
                        [SAMPLE_VALIDATOR_COINBASE],
                        epoch_id=SAMPLE_EPOCH_ID + 1,
                    )
                },
            ]
        )
        with self.assertRaisesRegex(ValueError, "epoch changed mid-pagination"):
            fetch_active_validators(http)

    def test_has_next_without_cursor_raises(self) -> None:
        """A missing endCursor with hasNextPage=true would loop forever."""
        http = _FakeGraphqlHttp(
            [{"data": _page([SAMPLE_VALIDATOR_MYSTEN], has_next=True)}]
        )
        with self.assertRaisesRegex(ValueError, "without endCursor"):
            fetch_active_validators(http)

    def test_missing_epoch_object_raises(self) -> None:
        """A page without the epoch object is malformed."""
        http = _FakeGraphqlHttp([{"data": {"epoch": None}}])
        with self.assertRaisesRegex(ValueError, "missing 'epoch'"):
            fetch_active_validators(http)


# ---------------------------------------------------------------------------
# parse_validator_rows — full extractor
# ---------------------------------------------------------------------------


class ParseValidatorRowsTests(unittest.TestCase):
    """End-to-end extractor: Move-struct payloads × defensive skips."""

    def test_extracts_one_row_per_valid_validator(self) -> None:
        """Two valid validators land; the defective payload is skipped."""
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS
        )
        self.assertEqual(len(rows), 2)

    def test_epoch_and_epoch_start_carried(self) -> None:
        """All rows from one snapshot share the same epoch + epoch_start_ts."""
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS
        )
        for r in rows:
            self.assertEqual(r.epoch, 1255)
            self.assertEqual(
                r.epoch_start_ts,
                datetime(2026, 9, 19, 0, 5, 39, 687000, tzinfo=timezone.utc),
            )

    def test_stake_amount_preserved_as_mist(self) -> None:
        """MIST is the storage unit — no unit conversion happens at write time."""
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS
        )
        by_name = {r.name: r for r in rows}
        self.assertEqual(
            by_name["Mysten-1"].stake_amount_mist,
            Decimal("218350824320000000"),
        )
        self.assertEqual(
            by_name["Coinbase"].stake_amount_mist,
            Decimal("155708205570000000"),
        )

    def test_apy_always_none_since_graphql_port(self) -> None:
        """suix_getValidatorsApy has no GraphQL equivalent — apy is NULL.

        The upsert path preserves pre-port APYs; this pins that the parser
        never fabricates one.
        """
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS
        )
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIsNone(r.apy)

    def test_flow_columns_from_staking_pool(self) -> None:
        """pending stake/withdraw + rewards come from the nested staking_pool."""
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, [SAMPLE_VALIDATOR_MYSTEN]
        )
        (row,) = rows
        self.assertEqual(row.pending_stake_mist, Decimal("5000000000000"))
        self.assertEqual(row.pending_withdraw_mist, Decimal("1100000000000"))
        self.assertEqual(row.rewards_pool_mist, Decimal("649057174720907"))
        self.assertEqual(row.staking_pool_activation_epoch, 0)
        self.assertIsNone(row.staking_pool_deactivation_epoch)

    def test_pending_flow_columns_default_to_zero(self) -> None:
        """pending_stake / pending_withdraw default to 0 (NOT NULL columns)."""
        payload = {
            "metadata": {"sui_address": "0x" + "d" * 64},
            "staking_pool": {
                "sui_balance": "1000000000",
                # pending_stake + pending_total_sui_withdraw both omitted
            },
        }
        rows = parse_validator_rows(100, SAMPLE_START_TIMESTAMP, [payload])
        self.assertEqual(len(rows), 1)
        # NOT NULL columns: must be Decimal(0), not None — otherwise the
        # bulk_upsert would fail at the DB layer.
        self.assertEqual(rows[0].pending_stake_mist, Decimal(0))
        self.assertEqual(rows[0].pending_withdraw_mist, Decimal(0))

    def test_skips_payload_with_missing_sui_address(self) -> None:
        """A payload missing metadata.sui_address is skipped, not raised on."""
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS
        )
        addresses = {r.validator_address for r in rows}
        self.assertEqual(len(addresses), 2)
        for addr in addresses:
            self.assertTrue(addr.startswith("0x"))

    def test_skips_payload_with_missing_stake(self) -> None:
        """A validator missing staking_pool.sui_balance is skipped (stake is NOT NULL)."""
        payload = {
            "metadata": {"sui_address": "0x" + "e" * 64, "name": "Stakeless"},
            "staking_pool": {},
        }
        rows = parse_validator_rows(100, SAMPLE_START_TIMESTAMP, [payload])
        self.assertEqual(rows, [])

    def test_skips_payload_with_missing_staking_pool(self) -> None:
        """A validator without the staking_pool struct is skipped."""
        payload = {"metadata": {"sui_address": "0x" + "f" * 64}}
        rows = parse_validator_rows(100, SAMPLE_START_TIMESTAMP, [payload])
        self.assertEqual(rows, [])

    def test_missing_epoch_raises(self) -> None:
        """A snapshot without a usable epochId is unusable — raise loudly."""
        with self.assertRaises(ValueError):
            parse_validator_rows(None, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS)

    def test_missing_start_timestamp_raises(self) -> None:
        """A snapshot without startTimestamp is unusable — raise."""
        with self.assertRaises(ValueError):
            parse_validator_rows(SAMPLE_EPOCH_ID, None, SAMPLE_VALIDATORS)

    def test_commission_rate_in_basis_points(self) -> None:
        """commission_rate is published as integer basis points (200 = 2%)."""
        rows = parse_validator_rows(
            SAMPLE_EPOCH_ID, SAMPLE_START_TIMESTAMP, SAMPLE_VALIDATORS
        )
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["Mysten-1"].commission_rate_bps, 200)
        self.assertEqual(by_name["Coinbase"].commission_rate_bps, 1000)


# ---------------------------------------------------------------------------
# collect — orchestration
# ---------------------------------------------------------------------------


class CollectTests(unittest.TestCase):
    """Collector orchestration paths not covered by parser-only tests."""

    def test_happy_path_stores_pages_and_preserves_apy(self) -> None:
        """Rows land, each page blob is stored, and apy is never overwritten."""
        pages = [
            {"page": 1},
            {"page": 2},
        ]

        class FakeRun:
            id = 42

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        with (
            patch(
                "genkei.ingest.sui_staking.fetch_active_validators",
                return_value=(
                    SAMPLE_EPOCH_ID,
                    SAMPLE_START_TIMESTAMP,
                    SAMPLE_VALIDATORS,
                    pages,
                ),
            ),
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

        partial.assert_not_called()
        self.assertEqual(store_blob.call_count, 2)
        store_blob.assert_any_call(
            42, "active_validators_page_1", SUI_GRAPHQL_URL, {"page": 1}
        )
        store_blob.assert_any_call(
            42, "active_validators_page_2", SUI_GRAPHQL_URL, {"page": 2}
        )
        connection_cm.assert_called_once()
        # All parsed rows carry apy=None since the GraphQL port, so the
        # upsert must run down the preserve-apy path (apy not in update_cols)
        # to keep pre-port APYs intact on same-epoch reruns.
        bulk_upsert.assert_called_once()
        bulk_rows = bulk_upsert.call_args.args[2]
        self.assertEqual(len(bulk_rows), 2)
        self.assertTrue(all(row["apy"] is None for row in bulk_rows))
        self.assertNotIn("apy", bulk_upsert.call_args.kwargs["update_cols"])
        self.assertEqual(fake_run._added, 2)

    def test_records_fetch_failures_before_reraising(self) -> None:
        """A GraphQL fetch failure is recorded in partial_endpoints and raised."""

        class FakeRun:
            id = 43

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        with (
            patch(
                "genkei.ingest.sui_staking.fetch_active_validators",
                side_effect=ValueError("Sui GraphQL error: 'boom'"),
            ),
            patch("genkei.ingest.sui_staking.db.ingest_run") as ingest_run_cm,
            patch("genkei.ingest.sui_staking.db.record_partial_endpoints") as partial,
            patch("genkei.ingest.sui_staking.db.store_raw_blob") as store_blob,
            patch("genkei.ingest.sui_staking.db.connection") as connection_cm,
            patch("genkei.ingest.sui_staking.db.bulk_upsert") as bulk_upsert,
        ):
            ingest_run_cm.return_value.__enter__.return_value = fake_run
            ingest_run_cm.return_value.__exit__.return_value = False

            with self.assertRaisesRegex(RuntimeError, "Sui GraphQL fetch failed"):
                collect(http=object())

        partial.assert_called_once_with(
            43,
            [
                {
                    "name": COLLECT_ENDPOINT_LABEL,
                    "url": SUI_GRAPHQL_URL,
                    "error": "Sui GraphQL error: 'boom'",
                }
            ],
        )
        store_blob.assert_not_called()
        connection_cm.assert_not_called()
        bulk_upsert.assert_not_called()
        self.assertFalse(hasattr(fake_run, "_added"))

    def test_records_parse_failures_before_reraising(self) -> None:
        """Malformed epoch metadata is recorded in partial_endpoints."""

        class FakeRun:
            id = 44

            def add_rows(self, n: int) -> None:
                self._added = n

        fake_run = FakeRun()
        with (
            patch(
                "genkei.ingest.sui_staking.fetch_active_validators",
                return_value=(None, None, SAMPLE_VALIDATORS, [{"page": 1}]),
            ),
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

        self.assertEqual(store_blob.call_count, 1)
        partial_args = partial.call_args.args
        self.assertEqual(partial_args[0], 44)
        self.assertEqual(partial_args[1][0]["name"], COLLECT_ENDPOINT_LABEL)
        self.assertIn("epochId", partial_args[1][0]["error"])
        connection_cm.assert_not_called()
        bulk_upsert.assert_not_called()
        self.assertFalse(hasattr(fake_run, "_added"))


if __name__ == "__main__":
    unittest.main()
