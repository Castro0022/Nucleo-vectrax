"""
Vectrax UCE — Connector Families.

Each connector belongs to exactly one family.  Families group connectors
by the kind of external resource they access.
"""
from __future__ import annotations

from enum import Enum, unique


@unique
class ConnectorFamily(str, Enum):
    WEB = "web"
    REST_API = "rest_api"
    LOCAL_FILE = "local_file"
    DATABASE = "database"
    EMAIL = "email"
    MESSAGING = "messaging"
    CALENDAR = "calendar"
    CLOUD_STORAGE = "cloud_storage"
    DOCUMENT = "document"
    TERMINAL = "terminal"
    CUSTOM = "custom"
