"""
Tests for the Pattern Performance Dashboard.

Covers:
  - /v1/market/patterns  API: schema, KPI aggregation, equity curve, timeline
  - /v1/market/patterns/view  UI: HTML rendering, required components
  - Edge cases: empty data, single pattern, all wins/all losses

All eToro data sources are mocked — no file I/O or real API calls.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch, MagicMock

from connectors.etoro.pattern_memory import PatternStats
from connectors.etoro.signal_recorder import MarketSignal


# ---------------------------------------------------------------------------
# Helpers — build realistic mock data
# ---------------------------------------------------------------------------

def _make_pattern(
    symbol: str = "BTC",
    direction: str = "buy",
    session: str = "London",
    n_wins: int = 8,
    n_losses: int = 4,
    n_neutral: int = 1,
    n_expired: int = 0,
    avg_win_pct: float = 1.2,
    avg_loss_pct: float = 0.6,
) -> PatternStats:
    n_total = n_wins + n_losses + n_neutral + n_expired
    decisive = n_wins + n_losses
    wr = n_wins / max(decisive, 1)
    exp = round(wr * avg_win_pct - (1 - wr) * avg_loss_pct, 3) if decisive else 0.0
    return PatternStats(
        pattern_key=f"{symbol}:{direction}:{session}:COND_A|COND_B",
        symbol=symbol.upper(),
        direction=direction,
        session=session,
        conditions_key="COND_A|COND_B",
        n_total=n_total,
        n_wins=n_wins,
        n_losses=n_losses,
        n_neutral=n_neutral,
        n_expired=n_expired,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        expectancy=exp,
        first_seen=time.time() - 86400,
        last_updated=time.time(),
    )


def _make_signal(
    symbol: str = "BTC",
    direction: str = "buy",
    status: str = "win",
    return_pct: float = 0.5,
    ts_offset: int = 0,
) -> MarketSignal:
    return MarketSignal(
        signal_id=f"SIG-{symbol}-{direction}-{ts_offset}",
        timestamp=time.time() - ts_offset,
        symbol=symbol.upper(),
        direction=direction,
        scenario="OPERABLE",
        price=100.0,
        atr=1.5,
        rel_volume=1.2,
        session="London",
        conditions_met=4,
        conditions_names=["COND_A", "COND_B", "COND_C", "COND_D"],
        entry_price=100.0,
        invalidation_price=98.5,
        status=status,
        return_pct=return_pct,
    )


MOCK_PATTERNS = [
    _make_pattern("BTC", "buy", n_wins=10, n_losses=5, avg_win_pct=1.5, avg_loss_pct=0.8),
    _make_pattern("BTC", "sell", n_wins=3, n_losses=7, avg_win_pct=0.9, avg_loss_pct=1.1),
    _make_pattern("TSLA", "buy", n_wins=12, n_losses=3, avg_win_pct=2.0, avg_loss_pct=0.5),
    _make_pattern("ETH", "buy", n_wins=2, n_losses=1, n_neutral=1),
]

MOCK_SIGNALS = [
    _make_signal("BTC", "buy", "win", 0.8, 3600),
    _make_signal("BTC", "buy", "loss", -0.5, 3000),
    _make_signal("TSLA", "buy", "win", 1.2, 2400),
    _make_signal("BTC", "sell", "loss", -1.0, 1800),
    _make_signal("TSLA", "buy", "win", 0.9, 1200),
    _make_signal("ETH", "buy", "neutral", 0.0, 600),
    _make_signal("BTC", "buy", "pending", 0.0, 60),  # pending — filtered out of timeline
]

MOCK_CONFIG = {
    "mode": "paper",
    "paper_trades_total": 12,
    "paper_trades_wins": 8,
    "consecutive_losses": 1,
    "daily_loss_usd": 3.5,
    "halt": False,
    "max_position_usd": 50,
    "min_paper_signals": 30,
    "min_paper_win_rate": 60,
}


# ---------------------------------------------------------------------------
# Shared mock setup
# ---------------------------------------------------------------------------

def _apply_mocks():
    """Return a dict of patches to apply for all endpoint tests."""
    return {
        "patterns": patch(
            "connectors.etoro.pattern_memory.get_patterns",
            return_value=MOCK_PATTERNS,
        ),
        "signals": patch(
            "connectors.etoro.signal_recorder.load_signals",
            return_value=MOCK_SIGNALS,
        ),
        "config": patch(
            "connectors.etoro.auto_executor.get_config",
            return_value=dict(MOCK_CONFIG),
        ),
    }


def _get_client():
    """Build a fresh TestClient for the app."""
    # Clear endpoint cache before each test
    from services.core.routes.pattern_performance import _cache
    _cache["data"] = None
    _cache["ts"] = 0

    from services.core.app import create_app
    from fastapi.testclient import TestClient
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# API Tests
# ---------------------------------------------------------------------------

class TestPatternPerformanceAPI(unittest.TestCase):
    """Tests for GET /v1/market/patterns."""

    def setUp(self):
        self.mocks = _apply_mocks()
        for m in self.mocks.values():
            m.start()
        self.client = _get_client()

    def tearDown(self):
        for m in self.mocks.values():
            m.stop()

    # -- Schema --

    def test_returns_200(self):
        resp = self.client.get("/v1/market/patterns")
        self.assertEqual(resp.status_code, 200)

    def test_top_level_keys(self):
        data = self.client.get("/v1/market/patterns").json()
        for key in ("ts", "patterns", "kpi", "symbols", "timeline",
                     "equity_curve", "executor", "pending_signals"):
            self.assertIn(key, data, f"Missing top-level key: {key}")

    def test_patterns_sorted_by_expectancy_desc(self):
        pats = self.client.get("/v1/market/patterns").json()["patterns"]
        exps = [p["expectancy"] for p in pats]
        self.assertEqual(exps, sorted(exps, reverse=True))

    def test_pattern_fields(self):
        pats = self.client.get("/v1/market/patterns").json()["patterns"]
        self.assertGreater(len(pats), 0)
        p = pats[0]
        for field in ("key", "symbol", "direction", "session", "conditions",
                       "n_total", "n_wins", "n_losses", "win_rate",
                       "avg_win_pct", "avg_loss_pct", "expectancy",
                       "confidence", "usable", "progress"):
            self.assertIn(field, p, f"Missing pattern field: {field}")

    # -- KPI aggregation --

    def test_kpi_total_patterns(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        self.assertEqual(kpi["total_patterns"], len(MOCK_PATTERNS))

    def test_kpi_wins_losses_sum(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        expected_wins = sum(p.n_wins for p in MOCK_PATTERNS)
        expected_losses = sum(p.n_losses for p in MOCK_PATTERNS)
        self.assertEqual(kpi["total_wins"], expected_wins)
        self.assertEqual(kpi["total_losses"], expected_losses)

    def test_kpi_global_win_rate(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        total_wins = sum(p.n_wins for p in MOCK_PATTERNS)
        total_losses = sum(p.n_losses for p in MOCK_PATTERNS)
        expected_wr = round(total_wins / max(total_wins + total_losses, 1) * 100, 1)
        self.assertEqual(kpi["global_win_rate"], expected_wr)

    def test_kpi_best_worst_patterns(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        self.assertIsNotNone(kpi["best_pattern"])
        self.assertIsNotNone(kpi["worst_pattern"])
        self.assertGreater(
            kpi["best_pattern"]["expectancy"],
            kpi["worst_pattern"]["expectancy"],
        )

    # -- Per-symbol breakdown --

    def test_symbols_breakdown_count(self):
        syms = self.client.get("/v1/market/patterns").json()["symbols"]
        unique_symbols = {p.symbol for p in MOCK_PATTERNS}
        self.assertEqual(len(syms), len(unique_symbols))

    def test_symbols_have_required_fields(self):
        syms = self.client.get("/v1/market/patterns").json()["symbols"]
        for s in syms:
            for field in ("symbol", "patterns", "usable", "signals",
                          "wins", "losses", "win_rate", "best_exp"):
                self.assertIn(field, s, f"Missing symbol field: {field}")

    # -- Signal timeline --

    def test_timeline_excludes_pending(self):
        tl = self.client.get("/v1/market/patterns").json()["timeline"]
        statuses = {s["status"] for s in tl}
        self.assertNotIn("pending", statuses)

    def test_timeline_entries_have_required_fields(self):
        tl = self.client.get("/v1/market/patterns").json()["timeline"]
        self.assertGreater(len(tl), 0)
        for entry in tl:
            for field in ("id", "ts", "symbol", "direction", "status",
                          "conditions", "price", "return_pct"):
                self.assertIn(field, entry, f"Missing timeline field: {field}")

    # -- Equity curve --

    def test_equity_curve_matches_timeline_length(self):
        data = self.client.get("/v1/market/patterns").json()
        self.assertEqual(len(data["equity_curve"]), len(data["timeline"]))

    def test_equity_curve_is_cumulative(self):
        eq = self.client.get("/v1/market/patterns").json()["equity_curve"]
        if len(eq) < 2:
            return
        # Each point should equal previous + that signal's return
        tl = self.client.get("/v1/market/patterns").json()["timeline"]
        running = 0.0
        for i, pt in enumerate(eq):
            running += tl[i]["return_pct"]
            self.assertAlmostEqual(pt["cumulative_pct"], round(running, 3), places=2)

    # -- Executor --

    def test_executor_mode(self):
        ex = self.client.get("/v1/market/patterns").json()["executor"]
        self.assertEqual(ex["mode"], "paper")

    def test_executor_paper_wr(self):
        ex = self.client.get("/v1/market/patterns").json()["executor"]
        expected_wr = round(8 / 12 * 100, 1)
        self.assertEqual(ex["paper_wr"], expected_wr)

    # -- Pending signals --

    def test_pending_signals_count(self):
        data = self.client.get("/v1/market/patterns").json()
        pending_in_mock = sum(1 for s in MOCK_SIGNALS if s.status == "pending")
        self.assertEqual(data["pending_signals"], pending_in_mock)

    # -- Caching --

    def test_cache_returns_same_ts(self):
        r1 = self.client.get("/v1/market/patterns").json()
        r2 = self.client.get("/v1/market/patterns").json()
        self.assertEqual(r1["ts"], r2["ts"])


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestPatternPerformanceEmpty(unittest.TestCase):
    """Tests with no patterns or signals."""

    def setUp(self):
        self.mocks = {
            "patterns": patch(
                "connectors.etoro.pattern_memory.get_patterns",
                return_value=[],
            ),
            "signals": patch(
                "connectors.etoro.signal_recorder.load_signals",
                return_value=[],
            ),
            "config": patch(
                "connectors.etoro.auto_executor.get_config",
                return_value={"mode": "off", "paper_trades_total": 0,
                              "paper_trades_wins": 0, "consecutive_losses": 0,
                              "daily_loss_usd": 0, "halt": False,
                              "max_position_usd": 50, "min_paper_signals": 30,
                              "min_paper_win_rate": 60},
            ),
        }
        for m in self.mocks.values():
            m.start()
        self.client = _get_client()

    def tearDown(self):
        for m in self.mocks.values():
            m.stop()

    def test_empty_returns_200(self):
        resp = self.client.get("/v1/market/patterns")
        self.assertEqual(resp.status_code, 200)

    def test_empty_kpi_zeroes(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        self.assertEqual(kpi["total_patterns"], 0)
        self.assertEqual(kpi["usable_patterns"], 0)
        self.assertEqual(kpi["total_wins"], 0)
        self.assertEqual(kpi["total_losses"], 0)

    def test_empty_patterns_list(self):
        data = self.client.get("/v1/market/patterns").json()
        self.assertEqual(len(data["patterns"]), 0)
        self.assertEqual(len(data["timeline"]), 0)
        self.assertEqual(len(data["equity_curve"]), 0)
        self.assertEqual(len(data["symbols"]), 0)

    def test_empty_best_worst_is_none(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        self.assertIsNone(kpi["best_pattern"])
        self.assertIsNone(kpi["worst_pattern"])


class TestPatternPerformanceAllWins(unittest.TestCase):
    """Tests with 100% win rate patterns."""

    def setUp(self):
        self.mocks = {
            "patterns": patch(
                "connectors.etoro.pattern_memory.get_patterns",
                return_value=[
                    _make_pattern("NVDA", "buy", n_wins=20, n_losses=0,
                                  avg_win_pct=2.5, avg_loss_pct=0),
                ],
            ),
            "signals": patch(
                "connectors.etoro.signal_recorder.load_signals",
                return_value=[
                    _make_signal("NVDA", "buy", "win", 2.5, i * 100)
                    for i in range(20)
                ],
            ),
            "config": patch(
                "connectors.etoro.auto_executor.get_config",
                return_value=dict(MOCK_CONFIG),
            ),
        }
        for m in self.mocks.values():
            m.start()
        self.client = _get_client()

    def tearDown(self):
        for m in self.mocks.values():
            m.stop()

    def test_100_percent_win_rate(self):
        kpi = self.client.get("/v1/market/patterns").json()["kpi"]
        self.assertEqual(kpi["global_win_rate"], 100.0)
        self.assertEqual(kpi["total_losses"], 0)

    def test_equity_curve_monotonic_up(self):
        eq = self.client.get("/v1/market/patterns").json()["equity_curve"]
        for i in range(1, len(eq)):
            self.assertGreaterEqual(eq[i]["cumulative_pct"], eq[i - 1]["cumulative_pct"])

    def test_usable_pattern(self):
        pats = self.client.get("/v1/market/patterns").json()["patterns"]
        self.assertEqual(len(pats), 1)
        self.assertTrue(pats[0]["usable"])


# ---------------------------------------------------------------------------
# UI Tests
# ---------------------------------------------------------------------------

class TestPatternPerformanceUI(unittest.TestCase):
    """Tests for GET /v1/market/patterns/view HTML page."""

    def setUp(self):
        self.client = _get_client()

    def test_view_returns_200_html(self):
        resp = self.client.get("/v1/market/patterns/view")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

    def test_html_contains_title(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("PATTERN PERFORMANCE", html)

    def test_html_contains_kpi_row(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn('id="kpi-row"', html)

    def test_html_contains_equity_canvas(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("eq-canvas", html)

    def test_html_contains_api_fetch(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("/v1/market/patterns", html)

    def test_html_contains_mode_badge(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn('id="mode-badge"', html)

    def test_html_contains_navigation_links(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("Market Cycle", html)
        self.assertIn("Observatory", html)
        self.assertIn("Universe", html)

    def test_html_contains_leaderboard_section(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("Pattern Leaderboard", html)

    def test_html_contains_signal_timeline_section(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("Signal Timeline", html)

    def test_html_contains_executor_section(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("Auto-Executor", html)

    def test_html_contains_symbol_breakdown(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("Breakdown por", html)

    def test_html_has_auto_refresh(self):
        html = self.client.get("/v1/market/patterns/view").text
        self.assertIn("setInterval", html)


if __name__ == "__main__":
    unittest.main()
