"""
Tests for Vectrax Market Data Module.

Tests connectors, cache, signals, alerts, and intents using mocks.
No real API calls are made during testing.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch, MagicMock

# ── Governance Tests ────────────────────────────────────────────────

class TestGovernance(unittest.TestCase):
    """Test allowlist and domain governance."""

    def test_allowlist_contains_required_hosts(self):
        from connectors.market import ALLOWLIST
        self.assertIn("api.binance.com", ALLOWLIST)
        self.assertIn("stream.binance.com", ALLOWLIST)
        self.assertIn("api.coingecko.com", ALLOWLIST)
        self.assertIn("www.alphavantage.co", ALLOWLIST)

    def test_validate_host_allowed(self):
        from connectors.market import validate_host
        self.assertTrue(validate_host("api.binance.com"))
        self.assertTrue(validate_host("api.coingecko.com"))

    def test_validate_host_blocked(self):
        from connectors.market import validate_host
        self.assertFalse(validate_host("evil.example.com"))
        self.assertFalse(validate_host("api.unknown.com"))

    def test_domain_is_read_only(self):
        from connectors.market import MODE, DOMAIN
        self.assertEqual(MODE, "read_only")
        self.assertEqual(DOMAIN, "market_data_authorized")

    def test_watchlists_defined(self):
        from connectors.market import CRYPTO_WATCHLIST, STOCK_WATCHLIST
        self.assertIn("BTCUSDT", CRYPTO_WATCHLIST)
        self.assertIn("ETHUSDT", CRYPTO_WATCHLIST)
        self.assertIn("SPY", STOCK_WATCHLIST)
        self.assertIn("NVDA", STOCK_WATCHLIST)


# ── Binance REST Tests ──────────────────────────────────────────────

class TestBinanceRest(unittest.TestCase):
    """Test Binance REST connector with mocked HTTP."""

    @patch("connectors.market.binance_rest.urllib.request.urlopen")
    def test_get_spot_price_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "symbol": "BTCUSDT",
            "price": "67500.50"
        }).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from connectors.market.binance_rest import get_spot_price
        result = get_spot_price("BTCUSDT")

        self.assertTrue(result["success"])
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["price"], 67500.50)
        self.assertEqual(result["source"], "binance")

    @patch("connectors.market.binance_rest.urllib.request.urlopen")
    def test_get_ticker_24h_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "symbol": "BTCUSDT",
            "lastPrice": "67500.50",
            "openPrice": "66000.00",
            "highPrice": "68000.00",
            "lowPrice": "65500.00",
            "volume": "12345.678",
            "quoteVolume": "834567890.12",
            "priceChangePercent": "2.27",
            "count": 500000,
        }).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from connectors.market.binance_rest import get_ticker_24h
        result = get_ticker_24h("BTCUSDT")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 67500.50)
        self.assertAlmostEqual(result["change_pct"], 2.27)
        self.assertGreater(result["volume"], 0)

    @patch("connectors.market.binance_rest.urllib.request.urlopen")
    def test_get_ohlcv_parsing(self, mock_urlopen):
        candle = [
            1234567890000, "67000.0", "68000.0", "66000.0", "67500.0",
            "100.5", 1234567899999, "6789012.3", 500, "50.2", "3456789.1", "0"
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([candle, candle]).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from connectors.market.binance_rest import get_ohlcv
        result = get_ohlcv("BTCUSDT", "1h", 2)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["candles"][0]["open"], 67000.0)
        self.assertEqual(result["candles"][0]["high"], 68000.0)

    @patch("connectors.market.binance_rest.urllib.request.urlopen")
    def test_network_error_handled(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network timeout")

        from connectors.market.binance_rest import get_spot_price
        result = get_spot_price("BTCUSDT")

        self.assertFalse(result["success"])
        self.assertIn("timeout", result["error"].lower())


# ── CoinGecko Tests ─────────────────────────────────────────────────

class TestCoinGecko(unittest.TestCase):
    """Test CoinGecko client with mocked HTTP."""

    @patch("connectors.market.coingecko_client.urllib.request.urlopen")
    def test_get_spot_price_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "bitcoin": {
                "usd": 67500.50,
                "usd_24h_change": 2.5,
                "usd_24h_vol": 28000000000,
                "usd_market_cap": 1300000000000,
            }
        }).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from connectors.market.coingecko_client import get_spot_price
        result = get_spot_price("BTCUSDT")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 67500.50)
        self.assertEqual(result["source"], "coingecko")
        self.assertGreater(result["market_cap"], 0)

    def test_symbol_to_id_resolution(self):
        from connectors.market.coingecko_client import _resolve_id
        self.assertEqual(_resolve_id("BTCUSDT"), "bitcoin")
        self.assertEqual(_resolve_id("ETH"), "ethereum")
        self.assertEqual(_resolve_id("BTC"), "bitcoin")


# ── Alpha Vantage Tests ────────────────────────────────────────────

class TestAlphaVantage(unittest.TestCase):
    """Test Alpha Vantage client with mocked HTTP."""

    @patch.dict("os.environ", {"ALPHAVANTAGE_API_KEY": "test_key"})
    @patch("connectors.market.alphavantage_client.urllib.request.urlopen")
    def test_get_stock_quote_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "Global Quote": {
                "01. symbol": "SPY",
                "02. open": "450.00",
                "03. high": "455.00",
                "04. low": "448.00",
                "05. price": "453.50",
                "06. volume": "75000000",
                "07. latest trading day": "2025-03-25",
                "08. previous close": "450.00",
                "09. change": "3.50",
                "10. change percent": "0.78%",
            }
        }).encode()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from connectors.market.alphavantage_client import get_stock_quote
        result = get_stock_quote("SPY")

        self.assertTrue(result["success"])
        self.assertEqual(result["symbol"], "SPY")
        self.assertEqual(result["price"], 453.50)
        self.assertEqual(result["source"], "alphavantage")

    def test_missing_api_key_returns_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            # Remove the key if set
            import os
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
            from connectors.market.alphavantage_client import get_stock_quote
            result = get_stock_quote("SPY")
            self.assertFalse(result["success"])
            self.assertTrue(result.get("fallback", False))


# ── Cache Tests ─────────────────────────────────────────────────────

class TestMarketCache(unittest.TestCase):
    """Test market cache service."""

    def test_set_and_get(self):
        from services.market_cache import MarketCache
        cache = MarketCache()
        cache.set("test_key", {"price": 100}, ttl=10)
        result = cache.get("test_key")
        self.assertIsNotNone(result)
        self.assertEqual(result["price"], 100)

    def test_ttl_expiry(self):
        from services.market_cache import MarketCache
        cache = MarketCache()
        cache.set("expire_key", {"price": 100}, ttl=0.01)
        time.sleep(0.02)
        result = cache.get("expire_key")
        self.assertIsNone(result)

    def test_invalidate(self):
        from services.market_cache import MarketCache
        cache = MarketCache()
        cache.set("del_key", {"price": 100}, ttl=60)
        self.assertTrue(cache.invalidate("del_key"))
        self.assertIsNone(cache.get("del_key"))

    def test_stats(self):
        from services.market_cache import MarketCache
        cache = MarketCache()
        cache.set("k1", 1, ttl=60)
        cache.get("k1")  # hit
        cache.get("k2")  # miss
        stats = cache.stats
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)

    def test_convenience_keys(self):
        from services.market_cache import MarketCache
        self.assertEqual(MarketCache.spot_key("btcusdt"), "spot:BTCUSDT")
        self.assertEqual(MarketCache.ohlcv_key("btc", "1h"), "ohlcv:BTC:1h")


# ── Alerts Tests ────────────────────────────────────────────────────

class TestMarketAlerts(unittest.TestCase):
    """Test market alerts service."""

    def test_add_and_trigger_alert(self):
        from services.market_alerts import MarketAlerts
        alerts = MarketAlerts()
        alerts.add_alert("BTCUSDT", 70000, "above")
        triggered = alerts.check_price("BTCUSDT", 71000)
        self.assertEqual(len(triggered), 1)
        self.assertTrue(triggered[0]["triggered"])

    def test_alert_not_triggered_below_level(self):
        from services.market_alerts import MarketAlerts
        alerts = MarketAlerts()
        alerts.add_alert("BTCUSDT", 70000, "above")
        triggered = alerts.check_price("BTCUSDT", 69000)
        self.assertEqual(len(triggered), 0)

    def test_below_direction(self):
        from services.market_alerts import MarketAlerts
        alerts = MarketAlerts()
        alerts.add_alert("BTCUSDT", 60000, "below")
        triggered = alerts.check_price("BTCUSDT", 59000)
        self.assertEqual(len(triggered), 1)

    def test_alert_triggers_once(self):
        from services.market_alerts import MarketAlerts
        alerts = MarketAlerts()
        alerts.add_alert("BTCUSDT", 70000, "above")
        alerts.check_price("BTCUSDT", 71000)
        triggered = alerts.check_price("BTCUSDT", 72000)
        self.assertEqual(len(triggered), 0)  # Already triggered

    def test_list_active_and_triggered(self):
        from services.market_alerts import MarketAlerts
        alerts = MarketAlerts()
        alerts.add_alert("BTCUSDT", 70000, "above")
        alerts.add_alert("ETHUSDT", 4000, "above")
        alerts.check_price("BTCUSDT", 71000)

        active = alerts.list_active()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["symbol"], "ETHUSDT")

        triggered = alerts.list_triggered()
        self.assertEqual(len(triggered), 1)


# ── Signals Tests ───────────────────────────────────────────────────

class TestMarketSignals(unittest.TestCase):
    """Test signal detection functions."""

    def test_sma_calculation(self):
        from services.market_signals import _sma
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(_sma(values, 3), 40.0)
        self.assertIsNone(_sma(values, 10))

    def test_roc_calculation(self):
        from services.market_signals import _roc
        values = list(range(1, 20))  # 1 to 19
        values = [float(v) for v in values]
        result = _roc(values, 5)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_ema_calculation(self):
        from services.market_signals import _ema
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        result = _ema(values, 3)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)


# ── Intent Detection Tests ──────────────────────────────────────────

class TestMarketIntents(unittest.TestCase):
    """Test intent detection patterns."""

    def test_detect_price_intent_spanish(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("precio de bitcoin")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "market_price")
        self.assertEqual(result[1]["symbol"], "BTCUSDT")

    def test_detect_price_intent_english(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("price of ethereum")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "market_price")
        self.assertEqual(result[1]["symbol"], "ETHUSDT")

    def test_detect_bitcoin_status(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("cómo está BTC")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "bitcoin_status")

    def test_detect_stock_status(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("cómo está NVDA hoy")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "stock_status")

    def test_detect_trend_with_timeframe(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("tendencia de BTC 4h")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "market_trend")
        self.assertEqual(result[1]["timeframe"], "4h")

    def test_detect_snapshot(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("resumen del mercado")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "market_snapshot")

    def test_vx_market_command(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("vx market status")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "vx_market_status")

    def test_non_market_query_returns_none(self):
        from intents.market_intents import detect_market_intent
        result = detect_market_intent("hola cómo estás")
        self.assertIsNone(result)


# ── Symbol Classification Tests ─────────────────────────────────────

class TestSymbolClassification(unittest.TestCase):
    """Test crypto/stock symbol classification."""

    def test_crypto_detection(self):
        from services.market_router import is_crypto
        self.assertTrue(is_crypto("BTCUSDT"))
        self.assertTrue(is_crypto("ETHUSDT"))
        self.assertTrue(is_crypto("BTC"))
        self.assertTrue(is_crypto("ETH"))

    def test_stock_detection(self):
        from services.market_router import is_crypto
        self.assertFalse(is_crypto("SPY"))
        self.assertFalse(is_crypto("AAPL"))
        self.assertFalse(is_crypto("NVDA"))

    def test_normalize_crypto(self):
        from services.market_router import normalize_crypto_symbol
        self.assertEqual(normalize_crypto_symbol("BTC"), "BTCUSDT")
        self.assertEqual(normalize_crypto_symbol("BTCUSDT"), "BTCUSDT")
        self.assertEqual(normalize_crypto_symbol("eth"), "ETHUSDT")


if __name__ == "__main__":
    unittest.main()
