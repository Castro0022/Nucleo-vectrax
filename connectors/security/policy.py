"""
Vectrax UCE — Connection Policy.

Enforces:
  - Channel separation (creator / user)
  - Permission validation (required vs granted)
  - Sensitive data classification
  - Approval gate (no connection activates without creator OK)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from connectors.contracts.schema import ConnectorContract
from connectors.core.errors import PermissionDenied
from connectors.core.types import ConnectorCapability, Sensitivity

logger = logging.getLogger("vectrax.uce.security.policy")


class ConnectionPolicy:
    """
    Stateless policy engine that validates whether an operation is
    allowed given the active contract and the caller's granted permissions.
    """

    # Capabilities that always require explicit creator approval
    HIGH_RISK_CAPABILITIES: Set[ConnectorCapability] = {
        ConnectorCapability.WRITE,
        ConnectorCapability.EXECUTE,
    }

    # ------------------------------------------------------------------
    # Permission checks
    # ------------------------------------------------------------------

    @staticmethod
    def check_permissions(
        contract: ConnectorContract,
        granted_permissions: List[str],
    ) -> bool:
        """
        Verify that *granted_permissions* satisfy what the contract requires.
        Returns True if all requirements are met.
        """
        granted = set(granted_permissions)
        required = set(contract.required_permissions)
        return required.issubset(granted)

    @staticmethod
    def enforce_permissions(
        contract: ConnectorContract,
        granted_permissions: List[str],
    ) -> None:
        """Like check_permissions, but raises PermissionDenied on failure."""
        granted = set(granted_permissions)
        required = set(contract.required_permissions)
        missing = required - granted
        if missing:
            raise PermissionDenied(
                f"Connector '{contract.name}' requires permissions "
                f"{sorted(missing)} which are not granted."
            )

    # ------------------------------------------------------------------
    # Risk assessment
    # ------------------------------------------------------------------

    @staticmethod
    def assess_risk(contract: ConnectorContract) -> Dict[str, Any]:
        """
        Return a risk summary for the given contract.
        Used by the approval gate to present risks to the creator.
        """
        risk_level = "low"
        reasons: List[str] = list(contract.risks)

        has_write = ConnectorCapability.WRITE in contract.capabilities
        has_execute = ConnectorCapability.EXECUTE in contract.capabilities

        if has_execute:
            risk_level = "critical"
            reasons.append("EXECUTE capability: can run arbitrary commands.")
        elif has_write:
            risk_level = "high"
            reasons.append("WRITE capability: can modify external data.")

        if contract.auth_method.value == "none":
            reasons.append("No authentication required — open access.")

        return {
            "connector": contract.name,
            "risk_level": risk_level,
            "reasons": reasons,
            "capabilities": [c.value for c in contract.capabilities],
            "auth_method": contract.auth_method.value,
        }

    # ------------------------------------------------------------------
    # Sensitivity filter
    # ------------------------------------------------------------------

    @staticmethod
    def max_allowed_sensitivity(
        granted_permissions: List[str],
    ) -> Sensitivity:
        """
        Determine the maximum data sensitivity the caller may access.
        """
        perms = set(granted_permissions)
        if "admin" in perms or "creator" in perms:
            return Sensitivity.CRITICAL
        if "write" in perms:
            return Sensitivity.HIGH
        if "read" in perms:
            return Sensitivity.MEDIUM
        return Sensitivity.LOW

    @staticmethod
    def check_sensitivity(
        data_sensitivity: Sensitivity,
        max_allowed: Sensitivity,
    ) -> bool:
        """Return True if *data_sensitivity* ≤ *max_allowed*."""
        order = [Sensitivity.LOW, Sensitivity.MEDIUM, Sensitivity.HIGH, Sensitivity.CRITICAL]
        return order.index(data_sensitivity) <= order.index(max_allowed)
