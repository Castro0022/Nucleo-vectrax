"""
Tests para AI Provider Stars — IAs externas como entidades vivas del universo.

Cubre:
  1. record_provider_experience() crea estrellas ai:* en el gravity index,
     con domain=ai_provider y actualiza hits / cc_score / outcome_history.
  2. Localidad: el éxito de un proveedor en un task_type no contamina otros
     task_types ni a otros proveedores.
  3. Selección gravitacional: con VX_PROVIDER_GRAVITY ausente el scoring es
     idéntico (bonus 0.0); con peso positivo y masa real, inclina el score.
  4. Universo: el censo cuenta el dominio ai_provider y el observer expone
     estrellas ai:*.
"""
import pytest

from core.learn import gravity_engine
from core.learn.gravity_engine import GravityIndex


@pytest.fixture
def fresh_index(tmp_path, monkeypatch):
    """Aísla el gravity index en un archivo temporal por test."""
    idx = GravityIndex(path=str(tmp_path / "gravity_index.json"))
    monkeypatch.setattr(gravity_engine, "_index", idx)
    # Asegura que la selección gravitacional esté desactivada salvo que el
    # test la habilite explícitamente.
    monkeypatch.delenv("VX_PROVIDER_GRAVITY", raising=False)
    return idx


# ───────────────────────────────────────────────────────────────────────────
# 1. Registro de experiencia
# ───────────────────────────────────────────────────────────────────────────

def test_record_creates_provider_star(fresh_index):
    from core.learn.provider_stars import (
        record_provider_experience,
        provider_fingerprint,
        PROVIDER_DOMAIN,
    )

    assert record_provider_experience("openai", "coding", "success", 0.9) is True

    rec = fresh_index.get(provider_fingerprint("openai", "coding"))
    assert rec is not None
    assert rec.domain == PROVIDER_DOMAIN
    assert rec.intent == "coding"
    assert rec.hits == 1
    assert "success" in rec.outcome_history


def test_repeated_experiences_accumulate_mass(fresh_index):
    from core.learn.provider_stars import (
        record_provider_experience,
        provider_fingerprint,
    )

    for _ in range(3):
        record_provider_experience("openai", "coding", "success", 1.0)

    rec = fresh_index.get(provider_fingerprint("openai", "coding"))
    assert rec.hits == 3
    assert rec.cc_score > 0
    assert rec.outcome_history.count("success") == 3


def test_record_rejects_empty_inputs(fresh_index):
    from core.learn.provider_stars import record_provider_experience

    assert record_provider_experience("", "coding") is False
    assert record_provider_experience("openai", "") is False


def test_unknown_outcome_falls_back_to_success(fresh_index):
    from core.learn.provider_stars import (
        record_provider_experience,
        provider_fingerprint,
    )

    record_provider_experience("gemini", "vision", "weird_outcome", 0.7)
    rec = fresh_index.get(provider_fingerprint("gemini", "vision"))
    assert rec.outcome_history == ["success"]


# ───────────────────────────────────────────────────────────────────────────
# 2. Localidad de la afinidad (evidencia por dominio, sin herencia)
# ───────────────────────────────────────────────────────────────────────────

def test_affinity_is_local_per_task_and_provider(fresh_index):
    from core.learn.provider_stars import (
        record_provider_experience,
        provider_affinity,
    )

    for _ in range(5):
        record_provider_experience("openai", "coding", "success", 1.0)

    # openai domina coding (única estrella con masa allí)
    assert provider_affinity("openai", "coding") == 1.0
    # no se hereda a otro task del mismo proveedor
    assert provider_affinity("openai", "vision") == 0.0
    # no se hereda a otro proveedor en el mismo task
    assert provider_affinity("gemini", "coding") == 0.0


def test_affinity_share_rebalances_with_evidence(fresh_index):
    from core.learn.provider_stars import (
        record_provider_experience,
        provider_affinity,
    )

    for _ in range(8):
        record_provider_experience("openai", "coding", "success", 1.0)
    for _ in range(2):
        record_provider_experience("gemini", "coding", "success", 1.0)

    a_openai = provider_affinity("openai", "coding")
    a_gemini = provider_affinity("gemini", "coding")

    assert a_openai > a_gemini
    assert 0.99 <= (a_openai + a_gemini) <= 1.01


def test_constellation_aggregates_provider_stars(fresh_index):
    from core.learn.provider_stars import (
        record_provider_experience,
        provider_constellation,
    )

    record_provider_experience("anthropic", "reasoning", "success", 1.0)
    record_provider_experience("anthropic", "planning", "success", 1.0)

    c = provider_constellation("anthropic")
    assert c["provider"] == "anthropic"
    assert c["task_types"] == 2
    assert c["total_hits"] == 2
    assert {s["task_type"] for s in c["stars"]} == {"reasoning", "planning"}


# ───────────────────────────────────────────────────────────────────────────
# 3. Selección gravitacional (gated, aditiva)
# ───────────────────────────────────────────────────────────────────────────

def test_affinity_bonus_zero_when_disabled(fresh_index, monkeypatch):
    from core.learn.provider_stars import (
        record_provider_experience,
        affinity_bonus,
    )

    for _ in range(5):
        record_provider_experience("openai", "coding", "success", 1.0)

    monkeypatch.delenv("VX_PROVIDER_GRAVITY", raising=False)
    assert affinity_bonus("openai", ["coding"]) == 0.0


def test_affinity_bonus_positive_when_enabled(fresh_index, monkeypatch):
    from core.learn.provider_stars import (
        record_provider_experience,
        affinity_bonus,
    )

    for _ in range(5):
        record_provider_experience("openai", "coding", "success", 1.0)

    monkeypatch.setenv("VX_PROVIDER_GRAVITY", "1")
    assert affinity_bonus("openai", ["coding"]) > 0.0
    # sin evidencia para ese task → sigue 0.0
    assert affinity_bonus("openai", ["vision"]) == 0.0


def test_smart_router_score_unchanged_when_disabled(fresh_index, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")
    monkeypatch.delenv("VX_PROVIDER_GRAVITY", raising=False)

    from core.smart_router import SmartRouter
    from core.learn.provider_stars import record_provider_experience

    router = SmartRouter()
    base = router._score_providers("code", "")

    # Aunque haya masa, con la selección desactivada el score no cambia.
    for _ in range(20):
        record_provider_experience("gemini", "coding", "success", 1.0)
    after = router._score_providers("code", "")

    assert after == base


def test_smart_router_gravity_tilts_score_when_enabled(fresh_index, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")
    monkeypatch.delenv("VX_PROVIDER_GRAVITY", raising=False)

    from core.smart_router import SmartRouter
    from core.learn.provider_stars import record_provider_experience

    router = SmartRouter()
    base = router._score_providers("code", "")

    for _ in range(20):
        record_provider_experience("gemini", "coding", "success", 1.0)

    monkeypatch.setenv("VX_PROVIDER_GRAVITY", "3")
    boosted = router._score_providers("code", "")

    assert boosted.get("gemini", 0.0) > base.get("gemini", 0.0)


# ───────────────────────────────────────────────────────────────────────────
# 4. Presencia en el universo (censo + observer)
# ───────────────────────────────────────────────────────────────────────────

def test_census_counts_ai_provider_domain(fresh_index):
    from core.learn.provider_stars import record_provider_experience
    from core import universe_census

    record_provider_experience("openai", "coding", "success", 1.0)

    # invalida el cache TTL del censo para forzar recomputo
    universe_census._cache["census"] = None
    universe_census._cache["ts"] = 0.0
    census = universe_census.get_census()

    assert "ai_provider" in census.domains
    assert census.domains["ai_provider"] >= 1


def test_observer_exposes_ai_stars(fresh_index):
    from core.learn.provider_stars import record_provider_experience
    from core.self_observation.universe_observer import observe_universe

    record_provider_experience("openai", "coding", "success", 1.0)

    snap = observe_universe()
    ids = {s.get("id", "") for s in snap.gravity_stars}
    assert any(i.startswith("ai:openai:") for i in ids)
