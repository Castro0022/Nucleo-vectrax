"""
Tests — estrellas documentales estratégicas (strategic_assets).

Verifican que el Asset Evidence Map se registra como estrella del universo
(domain ``strategic_asset``), de forma idempotente y aislada del índice real.

Run:
    pytest tests/test_strategic_assets.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture
def fresh_gravity(monkeypatch, tmp_path):
    """Aísla el gravity index en un archivo temporal (sin tocar ~/.vectrax)."""
    import core.learn.gravity_engine as ge
    idx = ge.GravityIndex(path=str(tmp_path / "gi.json"))
    monkeypatch.setattr(ge, "_index", idx, raising=False)
    yield idx
    monkeypatch.setattr(ge, "_index", None, raising=False)


def test_seed_registers_asset_star(fresh_gravity):
    from core.learn import strategic_assets as sa
    seeded = sa.seed_strategic_asset_stars()
    assert "VECTRAX_ASSET_EVIDENCE_MAP" in seeded

    stars = sa.list_strategic_asset_stars()
    assert any(s["name"] == "vectrax_asset_evidence_map" for s in stars)
    assert any(
        s["fingerprint"] == "asset:vectrax_asset_evidence_map" for s in stars
    )


def test_seed_is_idempotent(fresh_gravity):
    from core.learn import strategic_assets as sa
    first = sa.seed_strategic_asset_stars()
    assert first  # sembró algo la primera vez
    second = sa.seed_strategic_asset_stars()
    assert second == []  # no re-siembra


def test_asset_star_lives_in_dedicated_domain(fresh_gravity):
    """La estrella documental vive en su propio dominio (no contamina otros)."""
    from core.learn import strategic_assets as sa
    sa.seed_strategic_asset_stars()
    stars = sa.list_strategic_asset_stars()
    assert stars
    assert all(s["domain"] == sa.ASSET_DOMAIN for s in stars)
    assert sa.ASSET_DOMAIN == "strategic_asset"
