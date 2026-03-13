"""
Vectrax UCE — Connection Engine.

The single entry point for ALL connector operations.  Every read,
write, observe, and authenticate call goes through this engine, which
orchestrates:

  1. Contract validation
  2. Permission enforcement
  3. Approval gate check
  4. Connector dispatch
  5. Response normalisation
  6. Operation tracking (learning)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from connectors.contracts.schema import ConnectorContract
from connectors.contracts.validator import validate_or_raise
from connectors.core.errors import (
    ApprovalRequired,
    ConnectorConnectionError,
    PermissionDenied,
    UCEError,
)
from connectors.core.interface import UniversalConnector
from connectors.core.types import ConnectorStatus, OperationType
from connectors.learning.tracker import ConnectionTracker
from connectors.learning.patterns import PatternAnalyzer
from connectors.normalization.engine import NormalizationEngine
from connectors.normalization.models import NormalizedResult
from connectors.security.approval import ApprovalGate
from connectors.security.policy import ConnectionPolicy
from connectors.security.vault import CredentialVault

logger = logging.getLogger("vectrax.uce.engine")


class ConnectionEngine:
    """
    Unified orchestrator for the Universal Connection Engine.

    Usage::

        engine = ConnectionEngine()
        engine.register(my_connector, my_contract)
        engine.connect("my_connector")
        result = engine.read("my_connector", {"query": "..."})
    """

    def __init__(self) -> None:
        self._connectors: Dict[str, UniversalConnector] = {}
        self._contracts: Dict[str, ConnectorContract] = {}
        self._permissions: Dict[str, List[str]] = {}  # granted per connector

        # Sub-systems
        self._normalizer = NormalizationEngine()
        self._tracker = ConnectionTracker()
        self._analyzer = PatternAnalyzer(self._tracker)
        self._gate = ApprovalGate()
        self._vault = CredentialVault()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        connector: UniversalConnector,
        contract: ConnectorContract,
        granted_permissions: List[str] | None = None,
    ) -> None:
        """
        Register a connector with its contract.

        Validates the contract, but does NOT activate the connector.
        Activation requires a separate ``connect()`` call (which checks
        the approval gate).
        """
        validate_or_raise(contract)
        name = contract.name
        self._connectors[name] = connector
        self._contracts[name] = contract
        self._permissions[name] = granted_permissions or list(contract.required_permissions)
        logger.info("Connector '%s' v%s registered", name, contract.version)

    def unregister(self, name: str) -> bool:
        """Remove a connector from the engine."""
        if name in self._connectors:
            connector = self._connectors[name]
            try:
                connector.disconnect()
            except Exception:
                pass
            del self._connectors[name]
            self._contracts.pop(name, None)
            self._permissions.pop(name, None)
            logger.info("Connector '%s' unregistered", name)
            return True
        return False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(
        self,
        name: str,
        credentials: Dict[str, Any] | None = None,
    ) -> bool:
        """
        Authenticate and activate a registered connector.

        Checks:
          1. Connector exists
          2. Contract is valid
          3. Permissions are sufficient
          4. Approval gate is passed
          5. Authentication succeeds
        """
        connector, contract = self._resolve(name)
        ConnectionPolicy.enforce_permissions(contract, self._permissions.get(name, []))
        self._gate.require_approval(name)

        # Retrieve credentials from vault if not provided
        if credentials is None:
            credentials = self._vault.retrieve(name)

        t0 = time.time()
        try:
            ok = connector.authenticate(credentials)
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.AUTH.value, ok, latency,
                input_hint="authenticate",
            )
            if not ok:
                raise ConnectorConnectionError(
                    f"Connector '{name}' authentication returned False."
                )
            return True
        except UCEError:
            raise
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.AUTH.value, False, latency,
                error=str(exc)[:200],
            )
            raise ConnectorConnectionError(
                f"Connector '{name}' auth failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Operations — read / write / observe
    # ------------------------------------------------------------------

    def read(self, name: str, query: Dict[str, Any]) -> NormalizedResult:
        """Read from a connector and return normalised result."""
        connector, contract = self._resolve(name)
        self._ensure_permission(name, "read")

        t0 = time.time()
        try:
            raw = connector.read(query)
            raw = connector.normalize_response(raw)
            result = self._normalizer.normalize(raw, name, OperationType.READ)
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.READ.value, True, latency,
                input_hint=f"read query keys: {list(query.keys())}",
                output_hint=f"result success={result.success}",
            )
            return result
        except UCEError:
            raise
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.READ.value, False, latency,
                error=str(exc)[:200],
            )
            raise

    def write(self, name: str, data: Dict[str, Any]) -> NormalizedResult:
        """Write to a connector and return normalised result."""
        connector, contract = self._resolve(name)
        self._ensure_permission(name, "write")

        t0 = time.time()
        try:
            raw = connector.write(data)
            raw = connector.normalize_response(raw)
            result = self._normalizer.normalize(raw, name, OperationType.WRITE)
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.WRITE.value, True, latency,
                input_hint=f"write data keys: {list(data.keys())}",
                output_hint=f"result success={result.success}",
            )
            return result
        except UCEError:
            raise
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.WRITE.value, False, latency,
                error=str(exc)[:200],
            )
            raise

    def observe(
        self,
        name: str,
        event_filter: Dict[str, Any] | None = None,
        callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> NormalizedResult:
        """Subscribe to events from a connector."""
        connector, contract = self._resolve(name)
        self._ensure_permission(name, "read")

        t0 = time.time()
        try:
            raw = connector.observe(event_filter, callback)
            raw = connector.normalize_response(raw)
            result = self._normalizer.normalize(raw, name, OperationType.OBSERVE)
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.OBSERVE.value, True, latency,
                input_hint="observe subscription",
            )
            return result
        except UCEError:
            raise
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            self._tracker.track_operation(
                name, OperationType.OBSERVE.value, False, latency,
                error=str(exc)[:200],
            )
            raise

    # ------------------------------------------------------------------
    # Proposals
    # ------------------------------------------------------------------

    def propose_connection(self, name: str) -> Dict[str, Any]:
        """Generate an approval proposal for a registered connector."""
        _, contract = self._resolve(name)
        proposal = self._gate.propose(contract)
        return proposal.to_dict()

    def approve(self, proposal_id: str) -> Dict[str, Any] | None:
        """Approve a connection proposal (creator-only)."""
        p = self._gate.approve(proposal_id)
        return p.to_dict() if p else None

    def reject(self, proposal_id: str) -> Dict[str, Any] | None:
        """Reject a connection proposal (creator-only)."""
        p = self._gate.reject(proposal_id)
        return p.to_dict() if p else None

    # ------------------------------------------------------------------
    # Status & introspection
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return status summary of all registered connectors."""
        connectors = {}
        for name, connector in self._connectors.items():
            contract = self._contracts.get(name)
            stats = self._tracker.get_stats(name)
            connectors[name] = {
                "status": connector.status.value,
                "version": contract.version if contract else "?",
                "family": contract.family.value if contract else "?",
                "healthy": connector.healthcheck(),
                "approved": self._gate.is_approved(name),
                "stats": stats,
            }
        return {
            "total_connectors": len(self._connectors),
            "connectors": connectors,
        }

    def get_connector(self, name: str) -> UniversalConnector | None:
        return self._connectors.get(name)

    def get_contract(self, name: str) -> ConnectorContract | None:
        return self._contracts.get(name)

    def list_connectors(self) -> List[str]:
        return list(self._connectors.keys())

    def patterns(self, connector_name: str | None = None) -> List[Dict[str, Any]]:
        """Return detected connection patterns (learning layer)."""
        return self._analyzer.analyze(connector_name)

    def logs(self, name: str | None = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent connection logs."""
        entries = self._tracker.get_logs(connector_name=name, limit=limit)
        return [e.to_dict() for e in entries]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve(self, name: str) -> tuple[UniversalConnector, ConnectorContract]:
        connector = self._connectors.get(name)
        contract = self._contracts.get(name)
        if connector is None or contract is None:
            raise ConnectorConnectionError(
                f"Connector '{name}' is not registered."
            )
        return connector, contract

    def _ensure_permission(self, name: str, permission: str) -> None:
        granted = self._permissions.get(name, [])
        if permission not in granted:
            raise PermissionDenied(
                f"Operation '{permission}' not granted for connector '{name}'. "
                f"Granted: {granted}"
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: ConnectionEngine | None = None


def get_connection_engine() -> ConnectionEngine:
    """Return the global ConnectionEngine singleton."""
    global _engine
    if _engine is None:
        _engine = ConnectionEngine()
    return _engine
