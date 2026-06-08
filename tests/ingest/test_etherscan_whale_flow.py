"""Unit tests for the ETH whale-flow snapshot collector (B-106)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal

from genkei.common.watchlist import EthWhaleAddressEntry
from genkei.ingest.etherscan_whale_flow import (
    COLLECT_ENDPOINT_LABEL,
    ETH_DECIMALS,
    ETHERSCAN_API_KEY_ENV,
    SOURCE_NAME,
    _iter_snapshot_dates,
    _utc_midnight,
    _wei_to_eth,
    build_snapshot,
    compute_net_flow_and_count,
)

# Address constants used across cases. Lowercased — the parser stores
# them this way and the compute_net_flow_and_count helper compares
# case-insensitively.
WHALE_ADDR = "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"  # EF
OTHER_ADDR = "0xabcdef1234567890abcdef1234567890abcdef12"

# 1 ETH and 0.5 ETH expressed in wei (Etherscan returns these as strings).
ONE_ETH_WEI = str(10**18)
HALF_ETH_WEI = str(5 * 10**17)
TWO_ETH_WEI = str(2 * 10**18)


def _tx(
    *,
    to: str,
    from_: str,
    value_wei: str,
    is_error: str = "0",
) -> dict[str, object]:
    """Build a minimal Etherscan txlist entry for tests."""
    return {
        "to": to,
        "from": from_,
        "value": value_wei,
        "isError": is_error,
    }


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class ModuleConstantsTests(unittest.TestCase):
    """Pin constants the workflow + health checks depend on."""

    def test_source_name(self) -> None:
        """Source name is what PRIMARY_TABLES + RECURRING_ENDPOINTS key on."""
        self.assertEqual(SOURCE_NAME, "eth_whale_flow")

    def test_collect_endpoint_label_follows_convention(self) -> None:
        """'collect' matches the universal convention pinned by test_watchlist_cmd."""
        self.assertEqual(COLLECT_ENDPOINT_LABEL, "collect")

    def test_api_key_env_name(self) -> None:
        """Shares the Etherscan key env var with onchain_staking (B-082/B-086)."""
        self.assertEqual(ETHERSCAN_API_KEY_ENV, "ETHERSCAN_API_KEY")

    def test_eth_decimals(self) -> None:
        """Native ETH precision pinned — wei→ETH math depends on this."""
        self.assertEqual(ETH_DECIMALS, 18)


# ---------------------------------------------------------------------------
# Wei → ETH conversion
# ---------------------------------------------------------------------------


class WeiToEthTests(unittest.TestCase):
    """Lossless wei→ETH conversion to 18 decimal places."""

    def test_one_eth(self) -> None:
        """10^18 wei == 1.0 ETH exactly."""
        self.assertEqual(_wei_to_eth(10**18), Decimal("1"))

    def test_fractional_eth(self) -> None:
        """0.5 ETH = 5×10^17 wei."""
        self.assertEqual(_wei_to_eth(5 * 10**17), Decimal("0.5"))

    def test_large_balance(self) -> None:
        """Binance-cold-1 scale balance: ~556K ETH."""
        wei = 556_224 * 10**18
        self.assertEqual(_wei_to_eth(wei), Decimal("556224"))

    def test_zero(self) -> None:
        """Zero wei == zero ETH (no division-by-zero or sign artifacts)."""
        self.assertEqual(_wei_to_eth(0), Decimal("0"))


# ---------------------------------------------------------------------------
# 24h-window net-flow computation
# ---------------------------------------------------------------------------


class ComputeNetFlowTests(unittest.TestCase):
    """The load-bearing arithmetic: Σ(incoming) − Σ(outgoing), error-filtered."""

    def test_pure_inflow(self) -> None:
        """Two incoming txs sum to +3 ETH net + 2 tx_count."""
        txs = [
            _tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei=ONE_ETH_WEI),
            _tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei=TWO_ETH_WEI),
        ]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        self.assertEqual(net, 3 * 10**18)
        self.assertEqual(count, 2)

    def test_pure_outflow(self) -> None:
        """One outgoing tx = -1 ETH net + 1 tx_count."""
        txs = [
            _tx(to=OTHER_ADDR, from_=WHALE_ADDR, value_wei=ONE_ETH_WEI),
        ]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        self.assertEqual(net, -(10**18))
        self.assertEqual(count, 1)

    def test_mixed_inflow_outflow(self) -> None:
        """Inflow + outflow nets correctly + counts both."""
        txs = [
            _tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei=TWO_ETH_WEI),
            _tx(to=OTHER_ADDR, from_=WHALE_ADDR, value_wei=HALF_ETH_WEI),
        ]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        self.assertEqual(net, 2 * 10**18 - 5 * 10**17)  # +1.5 ETH
        self.assertEqual(count, 2)

    def test_skips_reverted_tx(self) -> None:
        """isError='1' txs move no value and would inflate flow if counted."""
        txs = [
            _tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei=ONE_ETH_WEI),
            _tx(
                to=WHALE_ADDR,
                from_=OTHER_ADDR,
                value_wei=TWO_ETH_WEI,
                is_error="1",
            ),
        ]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        # Only the first tx contributed
        self.assertEqual(net, 10**18)
        self.assertEqual(count, 1)

    def test_address_match_is_case_insensitive(self) -> None:
        """Etherscan returns lowercase; the watchlist might be checksum-cased.

        Without case-insensitive matching the flow calculation would
        silently report zero for any checksum-cased input.
        """
        # Checksum-cased query address; lowercased data in the tx
        checksum = "0xDE0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"
        txs = [_tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei=ONE_ETH_WEI)]
        net, count = compute_net_flow_and_count(txs, address=checksum)
        self.assertEqual(net, 10**18)
        self.assertEqual(count, 1)

    def test_self_to_self(self) -> None:
        """An address sending to itself nets to zero but still counts the tx.

        Wash-style same-address moves are rare on real cold wallets but a
        v1 ingester should handle them deterministically rather than
        double-count.
        """
        txs = [_tx(to=WHALE_ADDR, from_=WHALE_ADDR, value_wei=ONE_ETH_WEI)]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        # +1 ETH (incoming) − 1 ETH (outgoing) = 0
        self.assertEqual(net, 0)
        self.assertEqual(count, 1)

    def test_skips_non_touching_txs(self) -> None:
        """Txs that don't touch the address are ignored."""
        txs = [
            _tx(to=OTHER_ADDR, from_=OTHER_ADDR, value_wei=ONE_ETH_WEI),
        ]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        self.assertEqual(net, 0)
        self.assertEqual(count, 0)

    def test_skips_malformed_value(self) -> None:
        """A non-numeric value field is dropped, not raised on."""
        txs = [
            _tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei="not-a-number"),
            _tx(to=WHALE_ADDR, from_=OTHER_ADDR, value_wei=ONE_ETH_WEI),
        ]
        net, count = compute_net_flow_and_count(txs, address=WHALE_ADDR)
        # Only the good tx counted
        self.assertEqual(net, 10**18)
        self.assertEqual(count, 1)

    def test_empty_list(self) -> None:
        """Zero txs in window == 0 flow + 0 count, not an error."""
        net, count = compute_net_flow_and_count([], address=WHALE_ADDR)
        self.assertEqual(net, 0)
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# build_snapshot — joins ETH math with USD pricing
# ---------------------------------------------------------------------------


class BuildSnapshotTests(unittest.TestCase):
    """Snapshot assembly: USD columns are derived only when price is known."""

    def _entry(self) -> EthWhaleAddressEntry:
        return EthWhaleAddressEntry(
            address=WHALE_ADDR,
            label="Ethereum Foundation",
            category="foundation",
            notes=None,
        )

    def test_balance_and_flow_eth_columns(self) -> None:
        """wei inputs become Decimal ETH on the snapshot."""
        snap = build_snapshot(
            address_entry=self._entry(),
            ts=datetime(2026, 6, 8, tzinfo=timezone.utc),
            balance_wei=9774 * 10**18,
            net_wei=5 * 10**17,  # +0.5 ETH
            tx_count=3,
            eth_price_usd=Decimal("2500"),
        )
        self.assertEqual(snap.balance_eth, Decimal("9774"))
        self.assertEqual(snap.net_flow_eth_24h, Decimal("0.5"))
        self.assertEqual(snap.tx_count_24h, 3)

    def test_usd_columns_when_price_known(self) -> None:
        """USD columns quantize to 2 decimals (cents)."""
        snap = build_snapshot(
            address_entry=self._entry(),
            ts=datetime(2026, 6, 8, tzinfo=timezone.utc),
            balance_wei=10**18,  # 1 ETH
            net_wei=5 * 10**17,  # +0.5 ETH
            tx_count=1,
            eth_price_usd=Decimal("2500"),
        )
        self.assertEqual(snap.balance_usd_at_snapshot, Decimal("2500.00"))
        self.assertEqual(snap.net_flow_usd_24h, Decimal("1250.00"))

    def test_usd_columns_null_when_price_missing(self) -> None:
        """USD columns are None when coingecko hasn't backfilled yet.

        The ETH-denominated columns are still load-bearing — never raise
        on missing price; let the row land with NULL USD and let a later
        re-run fill it in via the (address, ts) upsert.
        """
        snap = build_snapshot(
            address_entry=self._entry(),
            ts=datetime(2026, 6, 8, tzinfo=timezone.utc),
            balance_wei=10**18,
            net_wei=10**17,
            tx_count=1,
            eth_price_usd=None,
        )
        self.assertEqual(snap.balance_eth, Decimal("1"))
        self.assertIsNone(snap.balance_usd_at_snapshot)
        self.assertIsNone(snap.net_flow_usd_24h)

    def test_carries_watchlist_metadata(self) -> None:
        """label + category come from the watchlist entry, not the upstream tx data."""
        snap = build_snapshot(
            address_entry=self._entry(),
            ts=datetime(2026, 6, 8, tzinfo=timezone.utc),
            balance_wei=0,
            net_wei=0,
            tx_count=0,
            eth_price_usd=None,
        )
        self.assertEqual(snap.label, "Ethereum Foundation")
        self.assertEqual(snap.category, "foundation")
        self.assertEqual(snap.address, WHALE_ADDR)


# ---------------------------------------------------------------------------
# Backfill date iteration
# ---------------------------------------------------------------------------


class IterSnapshotDatesTests(unittest.TestCase):
    """Calendar walking for incremental + backfill modes."""

    def test_incremental_returns_today_only(self) -> None:
        """No --since means a single snapshot for today."""
        days = _iter_snapshot_dates(since=None, today=date(2026, 6, 8))
        self.assertEqual(days, [date(2026, 6, 8)])

    def test_backfill_inclusive_range(self) -> None:
        """--since includes both endpoints."""
        days = _iter_snapshot_dates(
            since=date(2026, 6, 5), today=date(2026, 6, 8)
        )
        self.assertEqual(
            days,
            [
                date(2026, 6, 5),
                date(2026, 6, 6),
                date(2026, 6, 7),
                date(2026, 6, 8),
            ],
        )

    def test_since_equals_today_returns_one_day(self) -> None:
        """--since today == only today (not zero days)."""
        days = _iter_snapshot_dates(
            since=date(2026, 6, 8), today=date(2026, 6, 8)
        )
        self.assertEqual(days, [date(2026, 6, 8)])


class UtcMidnightTests(unittest.TestCase):
    """The PK ts uses UTC midnight of the snapshot date — pin this contract."""

    def test_utc_midnight_returns_aware_datetime(self) -> None:
        """Returned datetime is tz-aware UTC."""
        m = _utc_midnight(date(2026, 6, 8))
        self.assertEqual(m, datetime(2026, 6, 8, 0, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(m.tzinfo, timezone.utc)
        self.assertEqual(m.time(), time.min)


if __name__ == "__main__":
    unittest.main()
