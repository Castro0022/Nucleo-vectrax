"""
Tests for Vectrax Market Router.

Tests source selection, fallback behavior, and cache integration.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock


class TestMarketRouterFallback(unittest.TestCase):
    """Test fallback chain in market router."""

    def setUp(self):
        """Clear cache between tests."""
        from services.market_cache import get_market_cache
        get_market_cache().clear()

    @patch("services.market_router.binance_rest.get_spot_price")
    def test_crypto_spot_uses_binance_first(self, mock_binance):
        mock_binance.return_value = {
            "success": True,
            "symbol": "BTCUSDT",
            "price": 67000.0,
            "source": "binance",
            "latency_ms": 50,
        }

        from services.market_router import get_crypto_spot
        result = get_crypto_spot("BTCUSDT")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "binance")
        mock_binance.assert_called_once()

    @patch("services.market_router.coingecko_client.get_spot_price")
    @patch("services.market_router.binance_rest.get_spot_price")
    def test_crypto_spot_falls_back_to_coingecko(self, mock_binance, mock_coingecko):
        mock_binance.return_value = {"success": False, "error": "timeout"}
        mock_coingecko.return_value = {
            "success": True,
            "symbol": "BTCUSDT",
            "price": 67000.0,
            "source": "coingecko",
            "latency_ms": 200,
        }

        from services.market_router import get_crypto_spot
        result = get_crypto_spot("BTCUSDT")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "coingecko")

    @patch("services.market_router.coingecko_client.get_spot_price")
    @patch("services.market_router.binance_rest.get_spot_price")
    def test_all_sources_fail(self, mock_binance, mock_coingecko):
        mock_binance.return_value = {"success": False, "error": "binance down"}
        mock_coingecko.return_value = {"success": False, "error": "coingecko down"}

        from services.market_router import get_crypto_spot
        result = get_crypto_spot("BTCUSDT")

        self.assertFalse(result["success"])
        self.assertIn("All sources failed", result["error"])

    @patch("services.market_router.binance_rest.get_spot_price")
    def test_cache_is_used_on_second_call(self, mock_binance):
        mock_binance.return_value = {
            "success": True,
            "symbol": "BTCUSDT",
            "price": 67000.0,
            "source": "binance",
            "latency_ms": 50,
        }

        from services.market_router import get_crypto_spot
        result1 = get_crypto_spot("BTCUSDT")
        result2 = get_crypto_spot("BTCUSDT")

        # Second call should come from cache
        self.assertTrue(result2.get("from_cache", False))
        # Binance should only be called once
        self.assertEqual(mock_binance.call_count, 1)


class TestMarketRouterStockQuote(unittest.TestCase):
    """Test stock quote routing."""

    def setUp(self):
        from services.market_cache import get_market_cache
        get_market_cache().clear()

    @patch("services.market_router.alphavantage_client.get_stock_quote")
    def test_stock_quote_routes_to_alphavantage(self, mock_av):
        mock_av.return_value = {
            "success": True,
            "symbol": "SPY",
            "price": 453.50,
            "source": "alphavantage",
            "latency_ms": 100,
        }

        from services.market_router import get_stock_quote
        result = get_stock_quote("SPY")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "alphavantage")


class TestMarketRouterOHLCV(unittest.TestCase):
    """Test OHLCV routing."""

    def setUp(self):
        from services.market_cache import get_market_cache
        get_market_cache().clear()

    @patch("services.market_router.binance_rest.get_ohlcv")
    def test_crypto_ohlcv_routes_to_binance(self, mock_binance):
        mock_binance.return_value = {
            "success": True,
            "symbol": "BTCUSDT",
            "candles": [],
            "source": "binance",
            "latency_ms": 80,
        }

        from services.market_router import get_ohlcv
        result = get_ohlcv("BTCUSDT", "1h")

        self.assertTrue(result["success"])
        mock_binance.assert_called_once()

    @patch("services.market_router.alphavantage_client.get_daily_ohlcv")
    def test_stock_daily_ohlcv_routes_to_alphavantage(self, mock_av):
        mock_av.return_value = {
            "success": True,
            "symbol": "SPY",
            "candles": [],
            "source": "alphavantage",
            "latency_ms": 100,
        }

        from services.market_router import get_ohlcv
        result = get_ohlcv("SPY", "1d")

        self.assertTrue(result["success"])
        mock_av.assert_called_once()


class TestDoesNotBreakRouter(unittest.TestCase):
    """Ensure market module doesn't interfere with existing router."""

    def test_smart_router_still_works(self):
        """The existing SmartRouter should still function normally."""
        from core.routing.smart_router import SmartRouter, TaskType

        router = SmartRouter({
            "default_provider": "ollama",
            "default_model": "fast",
            "rules": [
                {"task_type": "code", "provider": "ollama", "model": "code"},
            ],
        })

        provider, model = router.route("write a python function")
        self.assertEqual(provider, "ollama")
        self.assertEqual(model, "code")

    def test_connector_family_enum_valid(self):
        """MARKET_DATA family should be in the enum."""
        from connectors.contracts.families import ConnectorFamily
        self.assertEqual(ConnectorFamily.MARKET_DATA.value, "market_data")


if __name__ == "__main__":
    unittest.main()
