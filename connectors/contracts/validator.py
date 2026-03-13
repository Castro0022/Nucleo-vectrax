"""
Vectrax UCE — Contract Validator.

Validates that a ConnectorContract is complete and coherent before the
connector is allowed to register in the UCE.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from connectors.contracts.schema import ConnectorContract
from connectors.core.errors import ContractViolation
from connectors.core.types import ConnectorCapability


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def validate_contract(contract: ConnectorContract) -> Tuple[bool, List[str]]:
    """
    Validate a connector contract.

    Returns (True, []) on success, or (False, [list of violations]).
    Raises ContractViolation if ``raise_on_error`` semantics are needed
    (caller can catch).
    """
    violations: List[str] = []

    # --- Identity ---
    if not contract.name or not contract.name.strip():
        violations.append("Contract 'name' must not be empty.")

    if not _SEMVER_RE.match(contract.version):
        violations.append(
            f"Contract 'version' must be semver (got '{contract.version}')."
        )

    # --- Capabilities ---
    if not contract.capabilities:
        violations.append("Contract must declare at least one capability.")

    if ConnectorCapability.AUTHENTICATE not in contract.capabilities:
        violations.append(
            "Every connector must include AUTHENTICATE capability."
        )

    # --- Permissions ---
    if not contract.required_permissions:
        violations.append("Contract must declare at least one required permission.")

    # WRITE capability requires 'write' permission
    if ConnectorCapability.WRITE in contract.capabilities:
        if "write" not in contract.required_permissions:
            violations.append(
                "WRITE capability declared but 'write' not in required_permissions."
            )

    # EXECUTE capability requires 'execute' permission
    if ConnectorCapability.EXECUTE in contract.capabilities:
        if "execute" not in contract.required_permissions:
            violations.append(
                "EXECUTE capability declared but 'execute' not in required_permissions."
            )

    # --- Description ---
    if not contract.description:
        violations.append("Contract must include a description.")

    is_valid = len(violations) == 0
    return is_valid, violations


def validate_or_raise(contract: ConnectorContract) -> None:
    """Validate and raise ContractViolation on first failure."""
    is_valid, violations = validate_contract(contract)
    if not is_valid:
        raise ContractViolation(
            f"Contract '{contract.name}' has {len(violations)} violation(s): "
            + "; ".join(violations)
        )
