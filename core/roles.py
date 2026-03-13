"""
Vectrax Roles & Permissions
==============================
Three roles with deny-by-default permission model.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Set


class Role(str, Enum):
    OWNER = "owner"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Proposals
    PROPOSE = "propose"
    APPLY_PROPOSAL = "apply_proposal"
    # Agent
    AGENT_START = "agent.start"
    AGENT_STATUS = "agent.status"
    # Core
    CORE_ADMIN = "core.admin"
    CORE_READ = "core.read"
    # Connectors
    CONNECTOR_MANAGE = "connector.manage"
    CONNECTOR_READ = "connector.read"
    # Config
    CONFIG_WRITE = "config.write"
    CONFIG_READ = "config.read"
    # Audit
    AUDIT_READ = "audit.read"
    # Events
    EVENT_WRITE = "event.write"
    EVENT_READ = "event.read"


# Permission grants per role
ROLE_PERMISSIONS: Dict[Role, FrozenSet[Permission]] = {
    Role.OWNER: frozenset(Permission),  # all permissions
    Role.OPERATOR: frozenset([
        Permission.PROPOSE,
        Permission.AGENT_START,
        Permission.AGENT_STATUS,
        Permission.CORE_READ,
        Permission.CONNECTOR_READ,
        Permission.CONFIG_READ,
        Permission.AUDIT_READ,
        Permission.EVENT_WRITE,
        Permission.EVENT_READ,
    ]),
    Role.VIEWER: frozenset([
        Permission.AGENT_STATUS,
        Permission.CORE_READ,
        Permission.CONNECTOR_READ,
        Permission.CONFIG_READ,
        Permission.AUDIT_READ,
        Permission.EVENT_READ,
    ]),
}


def has_permission(role: Role | str, perm: Permission | str) -> bool:
    """Check if a role has a specific permission."""
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return False
    if isinstance(perm, str):
        try:
            perm = Permission(perm)
        except ValueError:
            return False
    return perm in ROLE_PERMISSIONS.get(role, frozenset())


def get_permissions(role: Role | str) -> Set[str]:
    """Get all permissions for a role as strings."""
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return set()
    return {p.value for p in ROLE_PERMISSIONS.get(role, frozenset())}
