"""Unit tests for the equity rel-strength → signal_events emitter (B-111)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from genkei.common.watchlist import load_watchlist
from genkei.experiments.emitters.equity_relative_strength_emitter import (
    EMITTER_ENDPOINT,
    EMITTER_SOURCE,
    LAGGARD_THRESHOLD_PCT,
    LEADER_THRESHOLD_PCT,
    PEER_SYMBOL,
    PEER_TICKER,
    QQQ_SECTOR_KEYWORDS,
    QQQ_SYMBOL,
    QQQ_TICKER,
    SPY_SYMBOL,
    SPY_TICKER,
    STRENGTH_SATURATION_PP,
    WINDOW_DAYS,
    Crossing,
    _build_event,
    _date_ts,
    _detect_crossings,
    _equity_assets,
    _load_price_series,
    _peer_for_sector,
    _state_for,
    _strength_from_rel_strength,
    compute_daily_relative_strength,
)
from genkei.experiments.relative_strength import PricePoint

WATCHLIST_YAML = (
    "version: 1\n"
    "equities:\n"
    "  primary:\n"
    "    - symbol: CRM\n"
    "      cik: '0001108524'\n"
    "      name: Salesforce, Inc.\n"
    "      sector: Enterprise software\n"
    "      sleeve: core\n"
    "    - symbol: NOW\n"
    "      cik: '0001373715'\n"
    "      name: ServiceNow, Inc.\n"
    "      sector: Enterprise software\n"
    "      sleeve: core\n"
    "    - symbol: AAPL\n"
    "      cik: '0000320193'\n"
    "      name: Apple Inc.\n"
    "      sector: Consumer technology\n"
    "      sleeve: core\n"
    "benchmarks:\n"
    "  - symbol: SPY\n"
    "    name: SPDR S&P 500 ETF Trust\n"
    "    role: Equity-core baseline.\n"
)


def _load_watchlist() -> object:
    """Load the fixture watchlist through the production parser."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "watchlists.yml"
        path.write_text(WATCHLIST_YAML, encoding="utf-8")
        return load_watchlist(path)


# ---------------------------------------------------------------------------
# Pin the equity-tuned constants — the most likely future regression is a
# re-tune that forgets to also update one of these (threshold edge vs
# saturation point) and breaks the strength curve.
# ---------------------------------------------------------------------------


class ConstantsTests(unittest.TestCase):
    """Pin module constants that downstream signal rules depend on."""

    def test_equity_thresholds_are_two_thirds_of_crypto(self) -> None:
        """Equity thresholds stay calibrated to the B-098 crypto values."""
        # B-098 crypto values: LAGGARD=-15, LEADER=15, SATURATION=20.
        # B-111 equity values: LAGGARD=-10, LEADER=10, SATURATION=15.
        # The 2/3 ratio reflects equity volatility being ~2/3 of crypto
        # over comparable windows. A future contributor re-tuning should
        # keep the relationship coherent across both emitters.
        self.assertEqual(LAGGARD_THRESHOLD_PCT, Decimal("-10"))
        self.assertEqual(LEADER_THRESHOLD_PCT, Decimal("10"))
        self.assertEqual(STRENGTH_SATURATION_PP, Decimal("15"))

    def test_peer_is_spy(self) -> None:
        """Back-compat aliases default to SPY (B-111 contract preserved)."""
        # PEER_TICKER / PEER_SYMBOL stayed as back-compat aliases pointing
        # at SPY so any caller imported them under the B-111 single-peer
        # design continues to behave identically.
        self.assertEqual(PEER_TICKER, "SPY")
        self.assertEqual(PEER_SYMBOL, "SPY")

    def test_explicit_peer_constants_are_defined(self) -> None:
        """B-112 introduces explicit SPY_*/QQQ_* constants."""
        # The dual-peer design needs both tickers named explicitly so
        # the source_ref + payload code can reference them by name.
        self.assertEqual(SPY_TICKER, "SPY")
        self.assertEqual(SPY_SYMBOL, "SPY")
        self.assertEqual(QQQ_TICKER, "QQQ")
        self.assertEqual(QQQ_SYMBOL, "QQQ")

    def test_emitter_source_is_distinct_from_crypto(self) -> None:
        """Equity and crypto relative-strength sources do not collide."""
        # Critical for signal_rules.yml — the equity-side rules
        # reference 'equity_relative_strength' explicitly. If this
        # constant collided with crypto's 'relative_strength' the
        # cross-asset stack-forming would mix events across classes.
        self.assertEqual(EMITTER_SOURCE, "equity_relative_strength")
        self.assertNotEqual(EMITTER_SOURCE, "relative_strength")

    def test_endpoint_matches_recurring_endpoints_registration(self) -> None:
        """Health monitoring uses the emitter endpoint constant."""
        # Matches the value wired into RECURRING_ENDPOINTS in
        # genkei.cli.watchlist for `genkei watchlist health` monitoring.
        self.assertEqual(EMITTER_ENDPOINT, "equity_relative_strength")

    def test_window_days_is_30(self) -> None:
        """The trailing-return window matches the B-098 horizon."""
        self.assertEqual(WINDOW_DAYS, 30)


# ---------------------------------------------------------------------------
# State classification at the equity-tuned threshold edges.
# ---------------------------------------------------------------------------


class StateForTests(unittest.TestCase):
    """Cover state classification at threshold edges."""

    def test_none_yields_none(self) -> None:
        """Missing relative strength stays unclassified."""
        self.assertIsNone(_state_for(None))

    def test_at_or_below_laggard_threshold_is_laggard(self) -> None:
        """Values at or below -10pp are laggard."""
        self.assertEqual(_state_for(Decimal("-10")), "laggard")
        self.assertEqual(_state_for(Decimal("-15")), "laggard")
        self.assertEqual(_state_for(Decimal("-50")), "laggard")

    def test_at_or_above_leader_threshold_is_leader(self) -> None:
        """Values at or above +10pp are leader."""
        self.assertEqual(_state_for(Decimal("10")), "leader")
        self.assertEqual(_state_for(Decimal("15")), "leader")
        self.assertEqual(_state_for(Decimal("50")), "leader")

    def test_strictly_between_thresholds_is_neutral(self) -> None:
        """Values strictly between threshold edges are neutral."""
        self.assertEqual(_state_for(Decimal("0")), "neutral")
        self.assertEqual(_state_for(Decimal("9.99")), "neutral")
        self.assertEqual(_state_for(Decimal("-9.99")), "neutral")


# ---------------------------------------------------------------------------
# Saturating-ramp strength helper at equity-tuned saturation.
# ---------------------------------------------------------------------------


class StrengthFromRelStrengthTests(unittest.TestCase):
    """Cover the saturating-ramp strength helper."""

    def test_at_threshold_yields_two_thirds(self) -> None:
        """Threshold-edge crossings get meaningful nonzero strength."""
        # ±10pp / 15pp saturation = 0.667 — the threshold-edge
        # crossing has meaningful strength rather than being near-zero.
        self.assertEqual(
            _strength_from_rel_strength(Decimal("-10")),
            Decimal("10") / Decimal("15"),
        )

    def test_at_saturation_yields_one(self) -> None:
        """Values at either saturation edge clamp to one."""
        self.assertEqual(_strength_from_rel_strength(Decimal("-15")), Decimal("1"))
        self.assertEqual(_strength_from_rel_strength(Decimal("15")), Decimal("1"))

    def test_above_saturation_clamps_to_one(self) -> None:
        """Extreme values do not exceed the normalized strength cap."""
        # Real SaaS-cohort May 2026 fires reached -12 to -15pp; far
        # extremes (-50pp+) should still cap at 1.0, not blow up the
        # strength scale.
        self.assertEqual(_strength_from_rel_strength(Decimal("-100")), Decimal("1"))
        self.assertEqual(_strength_from_rel_strength(Decimal("100")), Decimal("1"))


# ---------------------------------------------------------------------------
# Daily relative-strength row computation.
# ---------------------------------------------------------------------------


class ComputeDailyRelativeStrengthTests(unittest.TestCase):
    """Cover trailing-return and peer-date alignment behavior."""

    def test_computes_relative_strength_from_unsorted_inputs(self) -> None:
        """Unsorted series still produce date-ordered daily rows."""
        asset_series = [
            PricePoint(ts=date(2026, 2, 1), price_usd=Decimal("130")),
            PricePoint(ts=date(2026, 1, 1), price_usd=Decimal("100")),
        ]
        peer_series = [
            PricePoint(ts=date(2026, 2, 1), price_usd=Decimal("110")),
            PricePoint(ts=date(2026, 1, 1), price_usd=Decimal("100")),
        ]
        rows = compute_daily_relative_strength(
            asset_series, peer_series, window_days=30
        )
        self.assertEqual(
            rows,
            [(date(2026, 2, 1), Decimal("30"), Decimal("10"), Decimal("20"))],
        )

    def test_skips_asset_day_without_exact_peer_observation(self) -> None:
        """Asset dates without a peer row are skipped defensively."""
        asset_series = [
            PricePoint(ts=date(2026, 1, 1), price_usd=Decimal("100")),
            PricePoint(ts=date(2026, 2, 1), price_usd=Decimal("130")),
        ]
        peer_series = [
            PricePoint(ts=date(2026, 1, 1), price_usd=Decimal("100")),
            PricePoint(ts=date(2026, 2, 2), price_usd=Decimal("110")),
        ]
        rows = compute_daily_relative_strength(
            asset_series, peer_series, window_days=30
        )
        self.assertEqual(rows, [])

    def test_skips_rows_without_trailing_lookback(self) -> None:
        """Rows before both series have lookback history are omitted."""
        series = [PricePoint(ts=date(2026, 2, 1), price_usd=Decimal("100"))]
        rows = compute_daily_relative_strength(series, series, window_days=30)
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# Yahoo candle loading.
# ---------------------------------------------------------------------------


class LoadPriceSeriesTests(unittest.TestCase):
    """Cover Yahoo adjusted-close loading semantics."""

    def test_prefers_adjusted_close_with_close_fallback(self) -> None:
        """The SQL uses adjusted close for return signals when available."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (date(2026, 1, 1), Decimal("100")),
            (date(2026, 2, 1), Decimal("110")),
        ]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        connection_cm = MagicMock()
        connection_cm.__enter__.return_value = conn

        with patch(
            "genkei.experiments.emitters.equity_relative_strength_emitter.db.connection",
            return_value=connection_cm,
        ):
            rows = _load_price_series("CRM", until=date(2026, 2, 1))

        sql, params = cursor.execute.call_args.args
        self.assertIn("COALESCE(adj_close, close)::numeric", sql)
        self.assertIn("COALESCE(adj_close, close) IS NOT NULL", sql)
        self.assertEqual(params, ["CRM", date(2026, 2, 1)])
        self.assertEqual(
            rows,
            [
                PricePoint(ts=date(2026, 1, 1), price_usd=Decimal("100")),
                PricePoint(ts=date(2026, 2, 1), price_usd=Decimal("110")),
            ],
        )


# ---------------------------------------------------------------------------
# Crossing-detector state machine (mirrors B-098's tests).
# ---------------------------------------------------------------------------


class DetectCrossingsTests(unittest.TestCase):
    """Cover the episode-onset crossing detector."""

    def _row(
        self, day: date, rel: Decimal
    ) -> tuple[date, Decimal, Decimal, Decimal]:
        """Build a daily rel-strength row with synthetic returns."""
        return (day, Decimal("0"), Decimal("0"), rel)

    def test_transition_neutral_to_laggard_emits_one(self) -> None:
        """Entering laggard state emits exactly one crossing."""
        d0 = date(2026, 5, 1)
        rows = [
            self._row(d0, Decimal("-5")),  # neutral
            self._row(date(2026, 5, 2), Decimal("-12")),  # laggard onset
            self._row(date(2026, 5, 3), Decimal("-14")),  # still laggard — silent
        ]
        crossings = _detect_crossings(rows, asset="CRM")
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].kind, "laggard_crossing")
        self.assertEqual(crossings[0].ts, date(2026, 5, 2))
        self.assertEqual(crossings[0].rel_strength_pct, Decimal("-12"))

    def test_transition_neutral_to_leader_emits_one(self) -> None:
        """Entering leader state emits exactly one crossing."""
        rows = [
            self._row(date(2026, 5, 1), Decimal("3")),  # neutral
            self._row(date(2026, 5, 2), Decimal("11")),  # leader onset
            self._row(date(2026, 5, 3), Decimal("14")),  # still leader — silent
        ]
        crossings = _detect_crossings(rows, asset="NOW")
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].kind, "leader_crossing")
        self.assertEqual(crossings[0].rel_strength_pct, Decimal("11"))

    def test_transition_back_to_neutral_silent(self) -> None:
        """Returning to neutral does not emit a recovery event."""
        rows = [
            self._row(date(2026, 5, 1), Decimal("-12")),  # laggard onset
            self._row(date(2026, 5, 2), Decimal("-5")),  # back to neutral — silent
        ]
        crossings = _detect_crossings(rows, asset="ADBE")
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].kind, "laggard_crossing")

    def test_laggard_to_leader_emits_each(self) -> None:
        """Direct laggard-to-leader transitions emit both onsets."""
        rows = [
            self._row(date(2026, 5, 1), Decimal("-12")),  # laggard onset
            self._row(date(2026, 5, 2), Decimal("11")),  # leader onset
        ]
        crossings = _detect_crossings(rows, asset="WDAY")
        self.assertEqual(len(crossings), 2)
        self.assertEqual(crossings[0].kind, "laggard_crossing")
        self.assertEqual(crossings[1].kind, "leader_crossing")


# ---------------------------------------------------------------------------
# Event-construction shape contract.
# ---------------------------------------------------------------------------


class BuildEventTests(unittest.TestCase):
    """Cover the signal event payload contract."""

    def test_laggard_event_has_correct_asset_class_and_direction(self) -> None:
        """Laggard events are equity-scoped bearish events."""
        crossing = Crossing(
            asset="CRM",
            peer="SPY",
            ts=date(2026, 5, 20),
            kind="laggard_crossing",
            rel_strength_pct=Decimal("-11.26"),
            asset_return_pct=Decimal("-2.0"),
            peer_return_pct=Decimal("9.26"),
        )
        event = _build_event(crossing, horizon="equity:core")
        self.assertEqual(event["asset"], "CRM")
        # Critical: events must carry asset_class="equity" so the
        # cross-source correlator's per-class filtering scopes the
        # rel-strength events to equity stacks only.
        self.assertEqual(event["asset_class"], "equity")
        self.assertEqual(event["direction"], "bearish")
        self.assertEqual(event["signal_kind"], "laggard_crossing")
        self.assertEqual(event["source"], EMITTER_SOURCE)
        self.assertEqual(event["horizon"], "equity:core")
        # ts is at UTC midnight of the crossing day
        self.assertEqual(
            event["ts"],
            datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc),
        )
        # source_ref is the idempotent natural key.
        self.assertEqual(event["source_ref"], "CRM:SPY:30d:2026-05-20")

    def test_leader_event_direction_is_bullish(self) -> None:
        """Leader events are bullish and strength-capped."""
        crossing = Crossing(
            asset="SNOW",
            peer="SPY",
            ts=date(2026, 5, 25),
            kind="leader_crossing",
            rel_strength_pct=Decimal("21.43"),
            asset_return_pct=Decimal("28"),
            peer_return_pct=Decimal("6.57"),
        )
        event = _build_event(crossing, horizon="equity:core")
        self.assertEqual(event["direction"], "bullish")
        self.assertEqual(event["signal_kind"], "leader_crossing")
        # Strength above saturation clamps to 1.0
        self.assertEqual(event["strength"], Decimal("1"))


# ---------------------------------------------------------------------------
# Watchlist routing — excludes SPY (the peer itself) and skips entries
# without a usable symbol.
# ---------------------------------------------------------------------------


class EquityAssetsTests(unittest.TestCase):
    """Cover watchlist routing into equity ticker and sleeve pairs."""

    def test_emits_watchlist_equities_excluding_spy(self) -> None:
        """Watchlist equities are returned without benchmark symbols."""
        watchlist = _load_watchlist()
        assets = _equity_assets(watchlist)
        tickers = sorted({t for t, _, _ in assets})
        # The 3 equities under primary; SPY is in benchmarks not equities,
        # so it isn't iterated. (Defense-in-depth: if someone moves SPY
        # to equities it still gets filtered out by ticker.)
        self.assertEqual(tickers, ["AAPL", "CRM", "NOW"])

    def test_filters_spy_and_qqq_when_they_collide_with_equity(self) -> None:
        """SPY / QQQ are filtered even if accidentally listed as equities."""
        # Defensive pin: if a future contributor adds SPY or QQQ to
        # equities by accident, the emitter filters them out (otherwise
        # the emitter would try to compute peer-vs-peer rel-strength).
        yaml = (
            "version: 1\n"
            "equities:\n"
            "  primary:\n"
            "    - symbol: SPY\n"
            "      name: oops shouldn't be here\n"
            "      cik: '0000884394'\n"
            "      sector: Broad market ETF\n"
            "      sleeve: core\n"
            "    - symbol: QQQ\n"
            "      name: also shouldn't be here\n"
            "      cik: '0000000000'\n"
            "      sector: Tech ETF\n"
            "      sleeve: core\n"
            "    - symbol: CRM\n"
            "      cik: '0001108524'\n"
            "      name: Salesforce\n"
            "      sector: Enterprise software\n"
            "      sleeve: core\n"
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml, encoding="utf-8")
            watchlist = load_watchlist(path)
        assets = _equity_assets(watchlist)
        self.assertEqual({t for t, _, _ in assets}, {"CRM"})

    def test_passes_through_sleeve_to_horizon_routing(self) -> None:
        """Equity sleeve values pass through for horizon construction."""
        # Today every equity is sleeve=core; the sleeve field is read so
        # a future tactical-sleeve equity is routed to "equity:tactical"
        # automatically.
        yaml = (
            "version: 1\n"
            "equities:\n"
            "  primary:\n"
            "    - symbol: CRM\n"
            "      cik: '0001108524'\n"
            "      name: Salesforce\n"
            "      sector: Enterprise software\n"
            "      sleeve: core\n"
            "    - symbol: TACTICAL_EQUITY\n"
            "      cik: '0000000000'\n"
            "      name: Hypothetical tactical equity\n"
            "      sector: Banking\n"
            "      sleeve: tactical\n"
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml, encoding="utf-8")
            watchlist = load_watchlist(path)
        sleeves = {ticker: sleeve for ticker, sleeve, _ in _equity_assets(watchlist)}
        self.assertEqual(sleeves["CRM"], "core")
        self.assertEqual(sleeves["TACTICAL_EQUITY"], "tactical")

    def test_emits_peer_per_asset_based_on_sector(self) -> None:
        """Sector-routed peer assignment (B-112) — CRM/AAPL → QQQ; JPM → SPY."""
        yaml = (
            "version: 1\n"
            "equities:\n"
            "  primary:\n"
            "    - symbol: CRM\n"
            "      cik: '0001108524'\n"
            "      name: Salesforce\n"
            "      sector: Enterprise software\n"
            "      sleeve: core\n"
            "    - symbol: AAPL\n"
            "      cik: '0000320193'\n"
            "      name: Apple\n"
            "      sector: Consumer technology\n"
            "      sleeve: core\n"
            "    - symbol: JPM\n"
            "      cik: '0000019617'\n"
            "      name: JPMorgan\n"
            "      sector: Banking\n"
            "      sleeve: core\n"
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlists.yml"
            path.write_text(yaml, encoding="utf-8")
            watchlist = load_watchlist(path)
        peers = {ticker: peer for ticker, _, peer in _equity_assets(watchlist)}
        self.assertEqual(peers["CRM"], "QQQ")
        self.assertEqual(peers["AAPL"], "QQQ")
        self.assertEqual(peers["JPM"], "SPY")


# ---------------------------------------------------------------------------
# Small _date_ts pin.
# ---------------------------------------------------------------------------


class PeerForSectorTests(unittest.TestCase):
    """B-112: sector-routed peer assignment.

    Each watchlist equity goes to QQQ if its lowercased sector string
    contains any of QQQ_SECTOR_KEYWORDS; otherwise to SPY. The keyword
    list was calibrated against the live watchlist's sector strings;
    these tests cover every distinct sector in the current watchlist so
    a future contributor reordering or shortening the keyword list
    sees the regression immediately rather than at the next emitter run.
    """

    def test_missing_sector_defaults_to_spy(self) -> None:
        # No sector → broad-market default (safe fallback when
        # classification is unknown).
        self.assertEqual(_peer_for_sector(None), "SPY")
        self.assertEqual(_peer_for_sector(""), "SPY")
        self.assertEqual(_peer_for_sector("   "), "SPY")

    def test_technology_sectors_route_to_qqq(self) -> None:
        # AAPL ("Consumer technology") through to all the
        # software / cloud / internet / semis / data / server names.
        for sector in (
            "Consumer technology",
            "Software / cloud",
            "Internet / cloud / AI",
            "E-commerce / cloud",
            "Internet / advertising / AI",
            "Semiconductors",
            "Semiconductors / software",
            "Semiconductors / memory",
            "Semiconductors / foundry",
            "Server systems",
            "Enterprise software",
            "Data platform",
            "Data / defense software",
            "Cloud infrastructure",
        ):
            with self.subTest(sector=sector):
                self.assertEqual(_peer_for_sector(sector), "QQQ")

    def test_fintech_crypto_bitcoin_ev_route_to_qqq(self) -> None:
        # The "non-obviously-tech" sectors that the user wanted in the
        # QQQ-comp bucket because they're Nasdaq-correlated.
        for sector in (
            "Consumer fintech",
            "Brokerage / fintech",
            "Crypto exchange",
            "Bitcoin treasury",
            "Bitcoin mining",
            "EV / energy",
        ):
            with self.subTest(sector=sector):
                self.assertEqual(_peer_for_sector(sector), "QQQ")

    def test_broad_market_sectors_route_to_spy(self) -> None:
        # The non-tech-correlated names in today's watchlist.
        for sector in (
            "Banking",
            "Payments",
            "Mobility",
            "Mining / uranium",
            "Oil & gas",
            "Sui treasury",  # treasury without bitcoin/crypto keyword → SPY
        ):
            with self.subTest(sector=sector):
                self.assertEqual(_peer_for_sector(sector), "SPY")

    def test_case_insensitive(self) -> None:
        self.assertEqual(_peer_for_sector("enterprise software"), "QQQ")
        self.assertEqual(_peer_for_sector("ENTERPRISE SOFTWARE"), "QQQ")
        self.assertEqual(_peer_for_sector("BANKING"), "SPY")

    def test_ev_keyword_avoids_substring_false_positives(self) -> None:
        # The "ev /" keyword (with trailing space-slash) deliberately
        # avoids matching common words containing "ev" (revenue, level,
        # eleven, …) that aren't tech-correlated.
        self.assertEqual(_peer_for_sector("Revenue services"), "SPY")
        self.assertEqual(_peer_for_sector("Eleven banking"), "SPY")

    def test_keyword_list_is_lowercase(self) -> None:
        # ``_peer_for_sector`` lowercases the input before checking, so
        # any keyword with mixed case would silently never match. Pin
        # the invariant.
        for kw in QQQ_SECTOR_KEYWORDS:
            with self.subTest(keyword=kw):
                self.assertEqual(kw, kw.lower())


class QqqEventConstructionTests(unittest.TestCase):
    """B-112: QQQ-routed crossings produce events with peer=QQQ.

    The source_ref encodes the peer code (``<ticker>:<peer>:30d:<date>``)
    so SPY and QQQ events for the same asset and date are natural-key-
    distinct. The payload's ``peer`` field also carries the code so
    downstream queries can filter cleanly.
    """

    def test_qqq_laggard_event_carries_qqq_in_source_ref_and_payload(self) -> None:
        crossing = Crossing(
            asset="NOW",
            peer="QQQ",
            ts=date(2026, 5, 12),
            kind="laggard_crossing",
            rel_strength_pct=Decimal("-18.0"),
            asset_return_pct=Decimal("-5.0"),
            peer_return_pct=Decimal("13.0"),
        )
        event = _build_event(crossing, horizon="equity:core")
        self.assertEqual(event["asset"], "NOW")
        self.assertEqual(event["payload"]["peer"], "QQQ")
        # Source ref naturally includes the peer code — SPY events for
        # the same asset / date would have a DIFFERENT source_ref.
        self.assertEqual(event["source_ref"], "NOW:QQQ:30d:2026-05-12")
        # Strength clamps at saturation for -18pp (15pp saturation cap).
        self.assertEqual(event["strength"], Decimal("1"))

    def test_spy_and_qqq_events_have_distinct_source_refs(self) -> None:
        # Same asset, same date, same kind — only peer differs. The
        # natural-key UNIQUE constraint on
        # (asset, ts, source, signal_kind, source_ref, horizon) keys on
        # source_ref so the two peers' events coexist in
        # meta.signal_events without collision.
        spy_event = _build_event(
            Crossing(
                asset="CRM",
                peer="SPY",
                ts=date(2026, 5, 12),
                kind="laggard_crossing",
                rel_strength_pct=Decimal("-12"),
                asset_return_pct=Decimal("-2"),
                peer_return_pct=Decimal("10"),
            ),
            horizon="equity:core",
        )
        qqq_event = _build_event(
            Crossing(
                asset="CRM",
                peer="QQQ",
                ts=date(2026, 5, 12),
                kind="laggard_crossing",
                rel_strength_pct=Decimal("-13.6"),
                asset_return_pct=Decimal("-2"),
                peer_return_pct=Decimal("11.6"),
            ),
            horizon="equity:core",
        )
        self.assertNotEqual(spy_event["source_ref"], qqq_event["source_ref"])
        self.assertEqual(spy_event["source_ref"], "CRM:SPY:30d:2026-05-12")
        self.assertEqual(qqq_event["source_ref"], "CRM:QQQ:30d:2026-05-12")


class DateTsTests(unittest.TestCase):
    """Cover timestamp conversion for crossing dates."""

    def test_converts_to_utc_midnight(self) -> None:
        """Crossing dates become timezone-aware UTC midnights."""
        self.assertEqual(
            _date_ts(date(2026, 5, 20)),
            datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(_date_ts(date(2026, 5, 20)).time(), time(0, 0))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
