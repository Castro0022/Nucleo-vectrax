"""
tests/test_etoro_client_retry_modes.py — cobertura de
core.trading-alineada RetryMode en connectors/etoro/etoro_client.py.

El punto central: ante timeout/excepción ambigua durante el ENVÍO,
SAFE_READ conserva el reintento automático de siempre; NON_IDEMPOTENT_WRITE
NO reintenta nunca y marca el resultado `indeterminate=True` — porque si
la primera petición sí llegó al broker, un segundo POST con un
x-request-id nuevo puede duplicar la acción.
"""
from __future__ import annotations

import httpx
import pytest

from connectors.etoro import etoro_client


class _TimeoutClient:
    """Doble de httpx.Client cuyo .request siempre lanza timeout."""

    def __init__(self):
        self.call_count = 0

    def request(self, *args, **kwargs):
        self.call_count += 1
        raise httpx.TimeoutException("simulated timeout")


class _ExceptionClient:
    def __init__(self):
        self.call_count = 0

    def request(self, *args, **kwargs):
        self.call_count += 1
        raise ConnectionError("simulated connection drop")


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch):
    monkeypatch.setenv("ETORO_API_KEY", "test-key")
    monkeypatch.setenv("ETORO_USER_KEY", "test-user-key")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """No perder tiempo real en los backoffs de SAFE_READ."""
    monkeypatch.setattr(etoro_client.time, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """El circuit breaker de core.external_call_guard es estado global de
    proceso — sin resetearlo, los fallos simulados de un test abren el
    circuito 'etoro' y bloquean (circuit_open) las llamadas de los
    tests siguientes antes de que lleguen a client.request()."""
    from core.external_call_guard import reset_all
    reset_all()
    yield
    reset_all()


class TestSafeReadRetriesOnTimeout:
    def test_safe_read_retries_up_to_max_retries(self, monkeypatch):
        client = _TimeoutClient()
        monkeypatch.setattr(etoro_client, "_get_client", lambda: client)

        result = etoro_client._request(
            "GET", "/trading/info/pnl", retry_mode=etoro_client.RetryMode.SAFE_READ,
        )
        assert client.call_count == etoro_client.MAX_RETRIES
        assert result["success"] is False
        assert "indeterminate" not in result

    def test_safe_read_is_the_default(self, monkeypatch):
        client = _TimeoutClient()
        monkeypatch.setattr(etoro_client, "_get_client", lambda: client)
        etoro_client._request("GET", "/trading/info/pnl")
        assert client.call_count == etoro_client.MAX_RETRIES


class TestNonIdempotentWriteNeverRetriesOnAmbiguousFailure:
    def test_timeout_sends_exactly_one_request(self, monkeypatch):
        client = _TimeoutClient()
        monkeypatch.setattr(etoro_client, "_get_client", lambda: client)

        result = etoro_client._request(
            "POST", "/trading/execution/market-open-orders/by-amount",
            body={"x": 1}, retry_mode=etoro_client.RetryMode.NON_IDEMPOTENT_WRITE,
        )
        assert client.call_count == 1, "un timeout en escritura no idempotente NUNCA reintenta"
        assert result["success"] is False
        assert result["indeterminate"] is True

    def test_generic_exception_sends_exactly_one_request(self, monkeypatch):
        client = _ExceptionClient()
        monkeypatch.setattr(etoro_client, "_get_client", lambda: client)

        result = etoro_client._request(
            "POST", "/trading/execution/market-close-orders/positions/p1",
            body={"x": 1}, retry_mode=etoro_client.RetryMode.NON_IDEMPOTENT_WRITE,
        )
        assert client.call_count == 1
        assert result["indeterminate"] is True

    def test_success_is_never_marked_indeterminate(self, monkeypatch):
        class _OkResponse:
            status_code = 200
            content = b'{"orderForOpen": {"orderID": "o1", "positionID": "p1"}}'

            def json(self):
                return {"orderForOpen": {"orderID": "o1", "positionID": "p1"}}

        class _OkClient:
            def request(self, *args, **kwargs):
                return _OkResponse()

        monkeypatch.setattr(etoro_client, "_get_client", lambda: _OkClient())
        result = etoro_client._request(
            "POST", "/trading/execution/market-open-orders/by-amount",
            body={"x": 1}, retry_mode=etoro_client.RetryMode.NON_IDEMPOTENT_WRITE,
        )
        assert result["success"] is True
        assert "indeterminate" not in result

    def test_explicit_http_rejection_is_not_indeterminate(self, monkeypatch):
        """Un HTTP 400 explícito es un desenlace CONOCIDO — no ambiguo."""
        class _RejectedResponse:
            status_code = 400
            content = b'{"error": "bad request"}'
            text = '{"error": "bad request"}'
            headers = {}

            def json(self):
                return {"error": "bad request"}

        class _RejectingClient:
            def request(self, *args, **kwargs):
                return _RejectedResponse()

        monkeypatch.setattr(etoro_client, "_get_client", lambda: _RejectingClient())
        result = etoro_client._request(
            "POST", "/trading/execution/market-open-orders/by-amount",
            body={"x": 1}, retry_mode=etoro_client.RetryMode.NON_IDEMPOTENT_WRITE,
        )
        assert result["success"] is False
        assert result.get("indeterminate", False) is False


class TestOpenAndClosePositionUseNonIdempotentWrite:
    def test_open_position_never_retries_on_timeout(self, monkeypatch):
        client = _TimeoutClient()
        monkeypatch.setattr(etoro_client, "_get_client", lambda: client)
        result = etoro_client.open_position(instrument_id=1, amount=100.0, is_buy=True)
        assert client.call_count == 1
        assert result["indeterminate"] is True

    def test_close_position_never_retries_on_timeout(self, monkeypatch):
        client = _TimeoutClient()
        monkeypatch.setattr(etoro_client, "_get_client", lambda: client)
        result = etoro_client.close_position(position_id="p1", instrument_id=1)
        assert client.call_count == 1
        assert result["indeterminate"] is True
