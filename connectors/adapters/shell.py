"""
Vectrax UCE — Shell / Terminal Connector.

Execute shell commands and read environment variables.
CRITICAL risk level — requires explicit creator approval.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List

from connectors.base import ConnectorSpec
from connectors.contracts.families import ConnectorFamily
from connectors.contracts.schema import ConnectorContract
from connectors.core.interface import UniversalConnector
from connectors.core.types import AuthMethod, ConnectorCapability, ConnectorStatus

SHELL_CONTRACT = ConnectorContract(
    name="shell",
    family=ConnectorFamily.TERMINAL,
    version="0.1.0",
    capabilities=[
        ConnectorCapability.AUTHENTICATE,
        ConnectorCapability.READ,
        ConnectorCapability.EXECUTE,
    ],
    required_permissions=["read", "execute"],
    auth_method=AuthMethod.NONE,
    input_schema={
        "read": {"var": "str (env variable name)"},
        "execute": {"command": "str", "timeout": "int (seconds, default 30)"},
    },
    output_schema={"stdout": "str", "stderr": "str", "returncode": "int"},
    risks=[
        "CRITICAL: Can execute arbitrary shell commands",
        "Full access to local environment",
        "Can modify system state",
    ],
    description="Shell connector — execute commands and read environment variables. CRITICAL risk.",
    author="vectrax",
)


class ShellConnector(UniversalConnector):
    """Connector for shell/terminal operations."""

    def __init__(self) -> None:
        spec = ConnectorSpec(
            name="shell",
            scopes=["shell.exec", "shell.env"],
            permissions=["read", "execute"],
            version="0.1.0",
            description=SHELL_CONTRACT.description,
            author="vectrax",
        )
        super().__init__(spec)
        self._allowed_commands: List[str] | None = None  # None = all allowed

    def authenticate(self, credentials: Dict[str, Any] | None = None) -> bool:
        if credentials and "allowed_commands" in credentials:
            self._allowed_commands = credentials["allowed_commands"]
        self._set_status(ConnectorStatus.CONNECTED)
        return True

    def read(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """Read an environment variable."""
        var = query.get("var", "")
        if var:
            value = os.environ.get(var)
            return {
                "var": var,
                "value": value,
                "exists": value is not None,
                "success": True,
            }
        # List env var names (not values) for safety
        return {
            "env_vars": sorted(os.environ.keys()),
            "count": len(os.environ),
            "success": True,
        }

    def write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a command (mapped to write for compatibility)."""
        return self._execute(data)

    def _execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        command = data.get("command", "")
        timeout = data.get("timeout", 30)

        if not command:
            return {"error": "No command specified", "success": False}

        if self._allowed_commands is not None:
            cmd_base = command.split()[0] if command else ""
            if cmd_base not in self._allowed_commands:
                return {
                    "error": f"Command '{cmd_base}' not in allowed list",
                    "success": False,
                }

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after {timeout}s", "success": False}
        except Exception as exc:
            return {"error": str(exc), "success": False}

    def healthcheck(self) -> bool:
        try:
            result = subprocess.run(
                "echo ok", shell=True, capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def test(self) -> Dict[str, Any]:
        ok = self.healthcheck()
        return {"passed": ok, "details": f"Shell {'operational' if ok else 'failed'}"}

    def list_resources(self) -> List[Dict[str, Any]]:
        return [
            {"id": "shell-env", "name": "Environment Variables", "type": "env"},
            {"id": "shell-exec", "name": "Command Execution", "type": "exec"},
        ]
