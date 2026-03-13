"""
Vectrax UCE — Credential Vault.

Stores connector credentials in ~/.vectrax/vault/ as JSON files.
Credentials are base64-encoded (not plain text).  For production
use, this should be replaced with OS keychain or a proper secrets
manager.

IMPORTANT: This vault never logs or prints credential values.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("vectrax.uce.security.vault")

_VAULT_DIR = Path.home() / ".vectrax" / "vault"


class CredentialVault:
    """
    Simple file-based credential store.

    Each connector gets its own file: ``~/.vectrax/vault/<name>.json``
    Values are base64-encoded before writing to disk.
    """

    def __init__(self, vault_dir: Path | None = None) -> None:
        self._dir = vault_dir or _VAULT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, connector_name: str, credentials: Dict[str, Any]) -> None:
        """Store credentials for a connector (overwrites existing)."""
        encoded = {
            k: base64.b64encode(str(v).encode()).decode()
            for k, v in credentials.items()
        }
        path = self._path(connector_name)
        path.write_text(json.dumps(encoded, indent=2), encoding="utf-8")
        path.chmod(0o600)  # owner-only read/write
        logger.info("Credentials stored for connector '%s'", connector_name)

    def retrieve(self, connector_name: str) -> Optional[Dict[str, str]]:
        """Retrieve and decode credentials for a connector."""
        path = self._path(connector_name)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            k: base64.b64decode(v).decode()
            for k, v in raw.items()
        }

    def delete(self, connector_name: str) -> bool:
        """Delete stored credentials for a connector."""
        path = self._path(connector_name)
        if path.exists():
            path.unlink()
            logger.info("Credentials deleted for connector '%s'", connector_name)
            return True
        return False

    def has_credentials(self, connector_name: str) -> bool:
        """Check whether credentials exist for a connector."""
        return self._path(connector_name).exists()

    def list_connectors(self) -> list[str]:
        """List connectors that have stored credentials."""
        return [
            p.stem for p in self._dir.glob("*.json")
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _path(self, connector_name: str) -> Path:
        # Sanitise name to prevent path traversal
        safe = connector_name.replace("/", "_").replace("..", "_")
        return self._dir / f"{safe}.json"
