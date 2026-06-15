"""
Tenant Registry — Multi-business isolation
=============================================
Each business (tenant) gets:
  - Unique API key for authentication
  - Isolated domain in the gravitational universe
  - Rate limits based on plan
  - Own observation bias weights

API:
  create_tenant(name, plan) → {api_key, tenant_id}
  validate_key(api_key) → Tenant | None
  get_tenant(tenant_id) → Tenant | None

Creador: Mario Bravo Castro
Fecha: 2026-06-15
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger("vectrax.tenant")

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "tenants.db",
)

_CREATE = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    api_key     TEXT NOT NULL UNIQUE,
    plan        TEXT NOT NULL DEFAULT 'free',
    domain      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    events_today INTEGER NOT NULL DEFAULT 0,
    events_date TEXT NOT NULL DEFAULT ''
);
"""

# Rate limits by plan
PLAN_LIMITS: Dict[str, int] = {
    "free": 100,         # events/day
    "pro": 10000,
    "enterprise": 1000000,
    "creator": 1000000,  # internal — no limit
}


@dataclass
class Tenant:
    tenant_id: str
    name: str
    api_key: str
    plan: str
    domain: str
    created_at: float
    active: bool
    events_today: int
    events_date: str

    @property
    def daily_limit(self) -> int:
        return PLAN_LIMITS.get(self.plan, 100)

    @property
    def can_ingest(self) -> bool:
        today = time.strftime("%Y-%m-%d")
        if self.events_date != today:
            return True  # new day, counter resets
        return self.events_today < self.daily_limit


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CREATE)
    return conn


def _row_to_tenant(row) -> Tenant:
    return Tenant(
        tenant_id=row["tenant_id"],
        name=row["name"],
        api_key=row["api_key"],
        plan=row["plan"],
        domain=row["domain"],
        created_at=row["created_at"],
        active=bool(row["active"]),
        events_today=row["events_today"],
        events_date=row["events_date"],
    )


def create_tenant(name: str, plan: str = "free", domain: str = "") -> Dict:
    """Create a new tenant with a unique API key."""
    tenant_id = "t_" + secrets.token_hex(8)
    api_key = "vx_" + secrets.token_hex(24)
    now = time.time()

    if not domain:
        domain = name.lower().replace(" ", "_")[:30]

    conn = _conn()
    conn.execute(
        "INSERT INTO tenants (tenant_id, name, api_key, plan, domain, created_at) VALUES (?,?,?,?,?,?)",
        (tenant_id, name, api_key, plan, domain, now),
    )
    conn.commit()
    conn.close()

    logger.info("TENANT created | id=%s name=%s plan=%s domain=%s", tenant_id, name, plan, domain)
    return {
        "tenant_id": tenant_id,
        "api_key": api_key,
        "name": name,
        "plan": plan,
        "domain": domain,
        "daily_limit": PLAN_LIMITS.get(plan, 100),
    }


def validate_key(api_key: str) -> Optional[Tenant]:
    """Validate an API key and return the tenant, or None."""
    if not api_key:
        return None
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM tenants WHERE api_key = ? AND active = 1",
            (api_key,),
        ).fetchone()
        conn.close()
        if row:
            return _row_to_tenant(row)
    except Exception:
        pass
    return None


def record_event(tenant_id: str) -> None:
    """Increment daily event counter for a tenant."""
    today = time.strftime("%Y-%m-%d")
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT events_date FROM tenants WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row and row["events_date"] == today:
            conn.execute(
                "UPDATE tenants SET events_today = events_today + 1 WHERE tenant_id = ?",
                (tenant_id,),
            )
        else:
            # New day — reset counter
            conn.execute(
                "UPDATE tenants SET events_today = 1, events_date = ? WHERE tenant_id = ?",
                (today, tenant_id),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_tenant(tenant_id: str) -> Optional[Tenant]:
    """Get tenant by ID."""
    try:
        conn = _conn()
        row = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        conn.close()
        if row:
            return _row_to_tenant(row)
    except Exception:
        pass
    return None


def list_tenants() -> list:
    """List all active tenants."""
    try:
        conn = _conn()
        rows = conn.execute("SELECT * FROM tenants WHERE active = 1 ORDER BY created_at DESC").fetchall()
        conn.close()
        return [_row_to_tenant(r) for r in rows]
    except Exception:
        return []
