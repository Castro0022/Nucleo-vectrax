"""
Vectrax UCE — Local Filesystem Connector.

Read, write, and list local files.  All operations are sandboxed
to configured root directories.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from connectors.base import ConnectorSpec
from connectors.contracts.families import ConnectorFamily
from connectors.contracts.schema import ConnectorContract
from connectors.core.interface import UniversalConnector
from connectors.core.types import AuthMethod, ConnectorCapability, ConnectorStatus

FILESYSTEM_CONTRACT = ConnectorContract(
    name="local_filesystem",
    family=ConnectorFamily.LOCAL_FILE,
    version="0.1.0",
    capabilities=[
        ConnectorCapability.AUTHENTICATE,
        ConnectorCapability.READ,
        ConnectorCapability.WRITE,
        ConnectorCapability.OBSERVE,
    ],
    required_permissions=["read", "write"],
    auth_method=AuthMethod.NONE,
    input_schema={
        "read": {"path": "str (relative to root)", "encoding": "str (default utf-8)"},
        "write": {"path": "str", "content": "str", "encoding": "str (default utf-8)"},
    },
    output_schema={"path": "str", "content": "str|None", "exists": "bool", "size": "int"},
    risks=["Local filesystem access", "Can overwrite files if write permission granted"],
    description="Local filesystem connector — read, write and list files in sandboxed directories.",
    author="vectrax",
)


class LocalFilesystemConnector(UniversalConnector):
    """Connector for local file operations."""

    def __init__(self, root: str | None = None) -> None:
        spec = ConnectorSpec(
            name="local_filesystem",
            scopes=["fs.read", "fs.write", "fs.list"],
            permissions=["read", "write"],
            version="0.1.0",
            description=FILESYSTEM_CONTRACT.description,
            author="vectrax",
        )
        super().__init__(spec)
        self._root = Path(root) if root else Path.home()

    def authenticate(self, credentials: Dict[str, Any] | None = None) -> bool:
        if credentials and "root" in credentials:
            self._root = Path(credentials["root"])
        if not self._root.exists():
            self._set_status(ConnectorStatus.ERROR, f"Root not found: {self._root}")
            return False
        self._set_status(ConnectorStatus.CONNECTED)
        return True

    def read(self, query: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = query.get("path", ".")
        encoding = query.get("encoding", "utf-8")
        target = self._safe_path(rel_path)

        if target.is_dir():
            entries = []
            for entry in sorted(target.iterdir()):
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
            return {"path": str(target), "entries": entries, "success": True}

        if target.is_file():
            try:
                content = target.read_text(encoding=encoding)
                return {
                    "path": str(target),
                    "content": content,
                    "size": target.stat().st_size,
                    "success": True,
                }
            except Exception as exc:
                return {"path": str(target), "error": str(exc), "success": False}

        return {"path": str(target), "error": "Not found", "success": False}

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = data.get("path", "")
        content = data.get("content", "")
        encoding = data.get("encoding", "utf-8")

        if not rel_path:
            return {"error": "No path specified", "success": False}

        target = self._safe_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return {
            "path": str(target),
            "size": target.stat().st_size,
            "success": True,
        }

    def observe(self, event_filter=None, callback=None) -> Dict[str, Any]:
        """List recently modified files as a simple observe mechanism."""
        root = self._root
        pattern = (event_filter or {}).get("pattern", "*")
        limit = (event_filter or {}).get("limit", 20)
        files = sorted(root.rglob(pattern), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)
        recent = []
        for f in files[:limit]:
            if f.is_file():
                recent.append({"path": str(f), "mtime": f.stat().st_mtime, "size": f.stat().st_size})
        return {"recent_files": recent, "success": True}

    def healthcheck(self) -> bool:
        return self._root.exists() and self._root.is_dir()

    def test(self) -> Dict[str, Any]:
        ok = self.healthcheck()
        return {"passed": ok, "details": f"Root '{self._root}' {'exists' if ok else 'missing'}"}

    def list_resources(self) -> List[Dict[str, Any]]:
        return [{"id": "fs-root", "name": str(self._root), "type": "directory"}]

    def _safe_path(self, rel: str) -> Path:
        """Resolve path and ensure it stays within the root."""
        target = (self._root / rel).resolve()
        root_resolved = self._root.resolve()
        if not str(target).startswith(str(root_resolved)):
            raise PermissionError(f"Path escapes sandbox: {rel}")
        return target
