"""
Vectrax Connectors — Registry
================================
Discover, register, and list connectors.
Auto-loads stub connectors on first access.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from connectors.base import ConnectorAdapter

logger = logging.getLogger("vectrax.connectors.registry")


class ConnectorRegistry:
    """Central registry for all connectors."""

    def __init__(self) -> None:
        self._adapters: Dict[str, ConnectorAdapter] = {}

    def register(self, adapter: ConnectorAdapter) -> None:
        name = adapter.spec.name
        self._adapters[name] = adapter
        logger.info("Connector registered: %s (v%s)", name, adapter.spec.version)

    def get(self, name: str) -> Optional[ConnectorAdapter]:
        return self._adapters.get(name)

    def list_all(self) -> Dict[str, ConnectorAdapter]:
        return dict(self._adapters)

    def names(self) -> list:
        return list(self._adapters.keys())

    def unregister(self, name: str) -> bool:
        if name in self._adapters:
            del self._adapters[name]
            return True
        return False


# ---------------------------------------------------------------------------
# Global singleton with auto-discovery
# ---------------------------------------------------------------------------

_registry: Optional[ConnectorRegistry] = None


def _load_stubs(reg: ConnectorRegistry) -> None:
    """Auto-register built-in stub connectors (legacy compat)."""
    try:
        from connectors.stubs.google_stub import GoogleStubConnector
        reg.register(GoogleStubConnector())
    except Exception as exc:
        logger.warning("Failed to load google_stub: %s", exc)

    try:
        from connectors.stubs.icloud_stub import ICloudStubConnector
        reg.register(ICloudStubConnector())
    except Exception as exc:
        logger.warning("Failed to load icloud_stub: %s", exc)

    try:
        from connectors.stubs.generic_webhook_stub import GenericWebhookStubConnector
        reg.register(GenericWebhookStubConnector())
    except Exception as exc:
        logger.warning("Failed to load generic_webhook_stub: %s", exc)


def _load_uce_adapters(reg: ConnectorRegistry) -> None:
    """Auto-register UCE adapters into the legacy registry for backward-compat."""
    _adapters = [
        ("connectors.adapters.rest_api", "RestApiConnector"),
        ("connectors.adapters.local_filesystem", "LocalFilesystemConnector"),
        ("connectors.adapters.shell", "ShellConnector"),
        ("connectors.adapters.sqlite_db", "SqliteConnector"),
        ("connectors.adapters.webhook", "WebhookConnector"),
        ("connectors.adapters.google_workspace", "GoogleWorkspaceConnector"),
        ("connectors.adapters.icloud", "ICloudConnector"),
    ]
    for module_path, class_name in _adapters:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            reg.register(cls())
        except Exception as exc:
            logger.warning("Failed to load UCE adapter %s: %s", class_name, exc)


def get_connector_registry() -> ConnectorRegistry:
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
        _load_stubs(_registry)
    return _registry
