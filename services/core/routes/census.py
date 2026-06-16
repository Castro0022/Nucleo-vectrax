"""
/v1/census — Single Source of Truth for the Vectrax Universe.

Returns the unified census: star counts, convergences, market stats,
user stats — all from one computation. Every other endpoint derives
its totals from this same source.

Lightweight, TTL-cached (10s). Safe to poll frequently.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["census"])


@router.get("/census")
async def get_census_endpoint() -> Dict[str, Any]:
    """Universe census — single source of truth for all totals."""
    from core.universe_census import get_census
    return get_census().to_dict()
