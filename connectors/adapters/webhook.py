"""
Vectrax UCE — Webhook Connector.

Inbound and outbound webhook support.  Migrated from the legacy
generic_webhook_stub to a full UCE connector.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional

from connectors.base import ConnectorSpec
from connectors.contracts.families import ConnectorFamily
from connectors.contracts.schema import ConnectorContract
from connectors.core.interface import UniversalConnector
from connectors.core.types import AuthMethod, ConnectorCapability, ConnectorStatus

WEBHOOK_CONTRACT = ConnectorContract(
    name="webhook",
    family=ConnectorFamily.WEB,
    version="0.1.0",
    capabilities=[
        ConnectorCapability.AUTHENTICATE,
        ConnectorCapability.READ,
        ConnectorCapability.WRITE,
    ],
    required_permissions=["read", "write"],
    auth_method=AuthMethod.TOKEN,
    input_schema={
        "read": {"webhook_id": "str"},
        "write": {"url": "str", "payload": "dict", "headers": "dict (optional)"},
    },
    output_schema={"status_code": "int", "response": "dict|str"},
    risks=["External network access", "Payload sent to external URL"],
    description="Webhook connector — send/receive webhooks with token auth.",
    author="vectrax",
)


class WebhookConnector(UniversalConnector):
    """Universal webhook connector."""

    def __init__(self) -> None:
        spec = ConnectorSpec(
            name="webhook",
            scopes=["webhook.send", "webhook.receive"],
            permissions=["read", "write"],
            version="0.1.0",
            description=WEBHOOK_CONTRACT.description,
            author="vectrax",
        )
        super().__init__(spec)
        self._registered_hooks: Dict[str, str] = {}  # id -> url
        self._token: Optional[str] = None

    def authenticate(self, credentials: Dict[str, Any] | None = None) -> bool:
        if credentials:
            self._token = credentials.get("token")
            hooks = credentials.get("webhooks", {})
            self._registered_hooks.update(hooks)
        self._set_status(ConnectorStatus.CONNECTED)
        return True

    def read(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """List registered webhooks or get a specific one."""
        hook_id = query.get("webhook_id")
        if hook_id:
            url = self._registered_hooks.get(hook_id)
            return {"webhook_id": hook_id, "url": url, "exists": url is not None, "success": True}
        return {"webhooks": self._registered_hooks, "count": len(self._registered_hooks), "success": True}

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send a webhook payload to a URL."""
        url = data.get("url", "")
        payload = data.get("payload", {})
        headers = {"Content-Type": "application/json", **data.get("headers", {})}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        if not url:
            return {"error": "No URL specified", "success": False}

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {"status_code": resp.status, "response": resp.read().decode(), "success": True}
        except Exception as exc:
            return {"error": str(exc), "success": False}

    def healthcheck(self) -> bool:
        return True  # Webhooks are stateless

    def test(self) -> Dict[str, Any]:
        return {"passed": True, "details": f"Webhook connector: {len(self._registered_hooks)} hooks registered"}

    def list_resources(self) -> List[Dict[str, Any]]:
        return [
            {"id": hid, "name": url, "type": "webhook"}
            for hid, url in self._registered_hooks.items()
        ]
