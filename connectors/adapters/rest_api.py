"""
Vectrax UCE — Generic REST API Connector.

Connects to any REST API given a base URL and optional auth.
Supports GET (read), POST/PUT (write), and webhooks (observe).
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from connectors.base import ConnectorSpec
from connectors.contracts.families import ConnectorFamily
from connectors.contracts.schema import ConnectorContract
from connectors.core.interface import UniversalConnector
from connectors.core.types import AuthMethod, ConnectorCapability, ConnectorStatus


# ------------------------------------------------------------------
# Contract
# ------------------------------------------------------------------

REST_API_CONTRACT = ConnectorContract(
    name="rest_api",
    family=ConnectorFamily.REST_API,
    version="0.1.0",
    capabilities=[
        ConnectorCapability.AUTHENTICATE,
        ConnectorCapability.READ,
        ConnectorCapability.WRITE,
    ],
    required_permissions=["read", "write"],
    auth_method=AuthMethod.TOKEN,
    input_schema={
        "read": {"endpoint": "str", "params": "dict (optional)", "headers": "dict (optional)"},
        "write": {"endpoint": "str", "method": "POST|PUT|PATCH", "body": "dict", "headers": "dict (optional)"},
    },
    output_schema={"status_code": "int", "body": "dict|str", "headers": "dict"},
    risks=["External network access", "Data may leave the local system"],
    rate_limits={"requests_per_minute": 60},
    description="Generic REST API connector — any HTTP endpoint with token auth.",
    author="vectrax",
)


# ------------------------------------------------------------------
# Connector
# ------------------------------------------------------------------

class RestApiConnector(UniversalConnector):
    """Universal connector for any REST API."""

    def __init__(
        self,
        base_url: str = "",
        default_headers: Dict[str, str] | None = None,
    ) -> None:
        spec = ConnectorSpec(
            name="rest_api",
            scopes=["http.get", "http.post", "http.put"],
            permissions=["read", "write"],
            version="0.1.0",
            description=REST_API_CONTRACT.description,
            author="vectrax",
        )
        super().__init__(spec)
        self._base_url = base_url.rstrip("/")
        self._headers: Dict[str, str] = default_headers or {}
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def authenticate(self, credentials: Dict[str, Any] | None = None) -> bool:
        if credentials:
            self._token = credentials.get("token", credentials.get("api_key"))
            if self._token:
                self._headers["Authorization"] = f"Bearer {self._token}"
            self._base_url = credentials.get("base_url", self._base_url)
        self._set_status(ConnectorStatus.CONNECTED)
        return True

    def read(self, query: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = query.get("endpoint", "/")
        params = query.get("params", {})
        headers = {**self._headers, **query.get("headers", {})}

        url = f"{self._base_url}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    data = body
                return {
                    "status_code": resp.status,
                    "body": data,
                    "headers": dict(resp.headers),
                    "success": True,
                }
        except urllib.error.HTTPError as exc:
            return {
                "status_code": exc.code,
                "body": exc.read().decode("utf-8", errors="replace"),
                "error": str(exc),
                "success": False,
            }
        except Exception as exc:
            self._set_status(ConnectorStatus.ERROR, str(exc))
            return {"error": str(exc), "success": False}

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = data.get("endpoint", "/")
        method = data.get("method", "POST").upper()
        body = data.get("body", {})
        headers = {
            **self._headers,
            "Content-Type": "application/json",
            **data.get("headers", {}),
        }

        url = f"{self._base_url}{endpoint}"
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read().decode("utf-8")
                try:
                    resp_data = json.loads(resp_body)
                except json.JSONDecodeError:
                    resp_data = resp_body
                return {
                    "status_code": resp.status,
                    "body": resp_data,
                    "headers": dict(resp.headers),
                    "success": True,
                }
        except urllib.error.HTTPError as exc:
            return {
                "status_code": exc.code,
                "body": exc.read().decode("utf-8", errors="replace"),
                "error": str(exc),
                "success": False,
            }
        except Exception as exc:
            self._set_status(ConnectorStatus.ERROR, str(exc))
            return {"error": str(exc), "success": False}

    def healthcheck(self) -> bool:
        if not self._base_url:
            return False
        try:
            req = urllib.request.Request(self._base_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except urllib.error.HTTPError:
            # Any HTTP response (including 4xx/5xx) means the host is reachable
            return True
        except Exception:
            return False

    def test(self) -> Dict[str, Any]:
        healthy = self.healthcheck()
        return {
            "passed": healthy,
            "details": f"REST API {'reachable' if healthy else 'unreachable'} at {self._base_url}",
        }

    def list_resources(self) -> List[Dict[str, Any]]:
        return [{"id": "api-root", "name": self._base_url or "(not configured)", "type": "rest_api"}]
