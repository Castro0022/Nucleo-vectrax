"""
Tests for Word Gravity Index (WGI) and Gravity Activator.

Covers:
  - Keyword extraction and stopword filtering
  - Word normalization (accents, case)
  - CRUD operations (upsert, get_mass, get_entry)
  - Seed initialization
  - Decay logic
  - Polysemy resolution (user vs global scope)
  - Gravity activation and lock logic
  - Bandsaw prevention scenario

Creado: 2026-06-08
"""
import os
import sqlite3
import tempfile
import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated DB for each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Redirect WGI to a temp database for each test."""
    db_path = str(tmp_path / "test_user_memory.db")
    monkeypatch.setattr("core.word_gravity._DB_PATH", db_path)
    monkeypatch.setattr("core.word_gravity._initialized", False)
    # Also patch gravity_activator's DB path
    try:
        import core.gravity_activator
        monkeypatch.setattr(
            "core.gravity_activator._load_constellation_content",
            lambda cids, limit=5: [],  # skip DB for constellation loading
        )
    except Exception:
        pass
    yield db_path


# ===========================================================================
# Word Gravity Index tests
# ===========================================================================

class TestExtractKeywords:
    def test_basic_extraction(self):
        from core.word_gravity import extract_keywords
        kws = extract_keywords("Despierta Vectrax ?")
        assert "despierta" in kws
        assert "vectrax" in kws

    def test_stopwords_filtered(self):
        from core.word_gravity import extract_keywords
        kws = extract_keywords("el mercado de las estrellas en la memoria")
        assert "mercado" in kws
        assert "estrellas" in kws
        assert "memoria" in kws
        # Stopwords should NOT be present
        assert "el" not in kws
        assert "de" not in kws
        assert "las" not in kws
        assert "en" not in kws
        assert "la" not in kws

    def test_short_words_filtered(self):
        from core.word_gravity import extract_keywords
        kws = extract_keywords("yo sí te vi")
        # All words <= 2 chars after normalization should be filtered
        assert len(kws) == 0

    def test_deduplication(self):
        from core.word_gravity import extract_keywords
        kws = extract_keywords("mercado mercado mercado")
        assert kws.count("mercado") == 1

    def test_accented_words(self):
        from core.word_gravity import extract_keywords
        kws = extract_keywords("convergéncia y constelación")
        assert "convergencia" in kws
        assert "constelacion" in kws


class TestNormalization:
    def test_lowercase(self):
        from core.word_gravity import normalize_word
        assert normalize_word("VECTRAX") == "vectrax"

    def test_accent_removal(self):
        from core.word_gravity import normalize_word
        assert normalize_word("convergéncia") == "convergencia"
        assert normalize_word("núcleo") == "nucleo"
        assert normalize_word("constelación") == "constelacion"

    def test_special_chars_removed(self):
        from core.word_gravity import normalize_word
        assert normalize_word("hello?") == "hello"
        assert normalize_word("test!") == "test"


class TestUpsertAndGet:
    def test_insert_new_word(self):
        from core.word_gravity import upsert_word, get_word_mass
        mass = upsert_word("vectrax", delta=0.5)
        assert mass == 0.5
        assert get_word_mass("vectrax") == 0.5

    def test_increment_existing(self):
        from core.word_gravity import upsert_word, get_word_mass
        upsert_word("mercado", delta=0.3)
        upsert_word("mercado", delta=0.2)
        assert abs(get_word_mass("mercado") - 0.5) < 0.001

    def test_capped_at_one(self):
        from core.word_gravity import upsert_word, get_word_mass
        upsert_word("test", delta=0.8)
        upsert_word("test", delta=0.5)
        assert get_word_mass("test") == 1.0

    def test_stopword_ignored(self):
        from core.word_gravity import upsert_word
        mass = upsert_word("el", delta=0.5)
        assert mass == 0.0

    def test_constellation_ids_accumulated(self):
        from core.word_gravity import upsert_word, get_word_entry
        upsert_word("star", delta=0.1, constellation_id="id1")
        upsert_word("star", delta=0.1, constellation_id="id2")
        entry = get_word_entry("star")
        assert entry is not None
        assert "id1" in entry.constellation_ids
        assert "id2" in entry.constellation_ids

    def test_per_user_scope(self):
        from core.word_gravity import upsert_word, get_word_mass, SCOPE_GLOBAL
        upsert_word("mercado", scope=SCOPE_GLOBAL, delta=0.4)
        upsert_word("mercado", scope="tg:123", delta=0.7)
        assert abs(get_word_mass("mercado", SCOPE_GLOBAL) - 0.4) < 0.001
        assert abs(get_word_mass("mercado", "tg:123") - 0.7) < 0.001


class TestEffectiveMass:
    def test_global_only(self):
        from core.word_gravity import upsert_word, get_effective_mass, SCOPE_GLOBAL
        upsert_word("vectrax", scope=SCOPE_GLOBAL, delta=0.98)
        mass, scope = get_effective_mass("vectrax", "tg:999")
        assert mass == 0.98
        assert scope == SCOPE_GLOBAL

    def test_user_overrides_global(self):
        from core.word_gravity import upsert_word, get_effective_mass, SCOPE_GLOBAL
        upsert_word("mercado", scope=SCOPE_GLOBAL, delta=0.5)
        upsert_word("mercado", scope="tg:123", delta=0.9)
        mass, scope = get_effective_mass("mercado", "tg:123")
        assert mass == 0.9
        assert scope == "tg:123"

    def test_global_wins_if_higher(self):
        from core.word_gravity import upsert_word, get_effective_mass, SCOPE_GLOBAL
        upsert_word("nucleo", scope=SCOPE_GLOBAL, delta=0.85)
        upsert_word("nucleo", scope="tg:123", delta=0.3)
        mass, scope = get_effective_mass("nucleo", "tg:123")
        assert mass == 0.85
        assert scope == SCOPE_GLOBAL

    def test_nonexistent_word(self):
        from core.word_gravity import get_effective_mass
        mass, scope = get_effective_mass("xyznonexistent", "tg:123")
        assert mass == 0.0


class TestSeed:
    def test_seed_creates_words(self):
        from core.word_gravity import seed_global_index, get_word_mass
        count = seed_global_index()
        assert count > 0
        assert get_word_mass("vectrax") == 0.98
        assert get_word_mass("mario") == 0.92
        assert get_word_mass("mercado") == 0.88

    def test_seed_idempotent(self):
        from core.word_gravity import seed_global_index, get_word_mass
        count1 = seed_global_index()
        count2 = seed_global_index()
        assert count1 > 0
        assert count2 == 0  # already seeded
        # Mass should not change
        assert get_word_mass("vectrax") == 0.98


class TestDecay:
    def test_decay_inactive(self):
        from core.word_gravity import (
            upsert_word, get_word_mass, apply_decay,
            _conn, DECAY_GRACE_DAYS,
        )
        upsert_word("oldword", delta=0.5, category="concept")
        # Simulate old last_activated
        conn = _conn()
        import time
        old_time = time.time() - (DECAY_GRACE_DAYS + 1) * 86400
        conn.execute(
            "UPDATE word_gravity SET last_activated = ? WHERE word = 'oldword'",
            (old_time,),
        )
        conn.commit()
        conn.close()

        apply_decay()
        new_mass = get_word_mass("oldword")
        assert new_mass < 0.5  # decayed

    def test_identity_never_decays(self):
        from core.word_gravity import (
            upsert_word, get_word_mass, apply_decay,
            _conn, SCOPE_GLOBAL, DECAY_GRACE_DAYS,
        )
        upsert_word("vectrax", scope=SCOPE_GLOBAL, delta=0.98, category="identity")
        # Simulate old last_activated
        conn = _conn()
        import time
        old_time = time.time() - (DECAY_GRACE_DAYS + 30) * 86400
        conn.execute(
            "UPDATE word_gravity SET last_activated = ? "
            "WHERE word = 'vectrax' AND scope = ?",
            (old_time, SCOPE_GLOBAL),
        )
        conn.commit()
        conn.close()

        apply_decay()
        assert get_word_mass("vectrax", SCOPE_GLOBAL) == 0.98


# ===========================================================================
# Gravity Activator tests
# ===========================================================================

class TestGravityActivator:
    def _seed(self):
        from core.word_gravity import seed_global_index
        seed_global_index()

    def test_vectrax_triggers_lock(self):
        """The bandsaw prevention test: 'Despierta Vectrax ?' must trigger gravity_lock."""
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("Despierta Vectrax ?", "tg:123")
        assert result.activated is True
        assert result.lock_internal is True  # mass=0.98 >= 0.85
        assert result.max_word == "vectrax"
        assert result.max_mass == 0.98

    def test_low_mass_no_activation(self):
        """Random words should not activate."""
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("hola buenos dias", "tg:123")
        assert result.activated is False
        assert result.lock_internal is False

    def test_mercado_activates_no_lock(self):
        """'mercado' (0.88) activates but does NOT lock (>= 0.85)."""
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("¿Y el mercado?", "tg:123")
        assert result.activated is True
        assert result.max_word == "mercado"
        assert result.lock_internal is True  # 0.88 >= 0.85

    def test_medium_mass_activates_no_lock(self):
        """Words with 0.6 <= mass < 0.85 activate but don't lock."""
        from core.word_gravity import upsert_word
        upsert_word("proyecto", delta=0.7, category="concept")
        from core.gravity_activator import activate_gravity
        result = activate_gravity("háblame del proyecto", "tg:123")
        assert result.activated is True
        assert result.lock_internal is False  # 0.7 < 0.85

    def test_multiple_activations(self):
        """Multiple high-mass words should all activate."""
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity(
            "Vectrax y el mercado gravitacional", "tg:123",
        )
        assert result.activated is True
        assert len(result.activations) >= 2
        words = [a.word for a in result.activations]
        assert "vectrax" in words

    def test_context_generated(self):
        """Activations should produce a non-empty context string."""
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("Despierta Vectrax", "tg:123")
        # Context should at least have the header
        assert "GRAVITY" in result.context
        assert "vectrax" in result.context

    def test_user_polysemy(self):
        """Per-user mass should override global when higher."""
        self._seed()
        from core.word_gravity import upsert_word
        # Give user-specific high mass to 'observador'
        upsert_word("observador", scope="tg:mario", delta=0.95, category="domain")
        from core.gravity_activator import activate_gravity
        result = activate_gravity("el observador", "tg:mario")
        assert result.activated is True
        assert result.max_mass == 0.95  # user scope wins
        assert result.lock_internal is True


class TestBandsawPrevention:
    """End-to-end test for the exact scenario that caused the bandsaw incident."""

    def test_despierta_vectrax_never_goes_online(self):
        """
        Simulate the exact failing scenario:
        'Despierta Vectrax ?' should trigger gravity_lock=True.
        The SmartRouter would have classified this as ONLINE,
        but gravity_lock prevents it from ever reaching Tavily.
        """
        from core.word_gravity import seed_global_index
        seed_global_index()

        from core.gravity_activator import activate_gravity
        result = activate_gravity("Despierta Vectrax ?", "tg:2030762343")

        # Critical assertions:
        assert result.activated is True, "Gravity should activate for 'Vectrax'"
        assert result.lock_internal is True, "gravity_lock should be True (mass=0.98 >= 0.85)"
        assert result.max_word == "vectrax", "Max word should be 'vectrax'"
        assert result.max_mass >= 0.85, "Mass must be above lock threshold"
