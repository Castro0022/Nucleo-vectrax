"""
Tests — Core Memory Living Response
======================================
Validates that get_living_response() produces clean, synthesized output
instead of dumping raw memory fragments.

Scenario: user has 50+ core_memory entries from months of conversation.
The response should be max ~5-6 sentences, no contradictions, no garbage.

Creado: 2026-06-08
Creador: Mario Bravo Castro
Co-Authored-By: Oz <oz-agent@warp.dev>
"""
from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(autouse=True)
def isolated_core_db(monkeypatch, tmp_path):
    """Redirect core_memory to a temp DB."""
    db_path = str(tmp_path / "test_user_memory.db")
    monkeypatch.setattr("vectrax.core_memory._DB_PATH", db_path)
    yield db_path


def _insert_entry(user_id, category, content, weight=0.5):
    """Insert a core_memory entry directly."""
    from vectrax.core_memory import _conn
    now = time.time()
    conn = _conn()
    conn.execute(
        "INSERT INTO core_memory (user_id, category, content, weight, source, "
        "times_confirmed, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, category, content, weight, "test", 1, now, now),
    )
    conn.commit()
    conn.close()


USER = "tg:test_mario"


class TestLivingResponseSynthesis:
    """get_living_response must produce clean, capped output."""

    def _populate_heavy_memory(self):
        """Simulate a user with months of accumulated memory."""
        # Identity — including contradictory locations
        _insert_entry(USER, "identity", "Se llama Mario Bravo Castro.", 1.0)
        _insert_entry(USER, "identity", "Vivo en Miami.", 0.85)
        _insert_entry(USER, "identity", "Vivo en Chile.", 0.5)  # contradictory
        _insert_entry(USER, "identity", "Soy de Santiago.", 0.4)

        # Work — many noisy entries from "soy" regex
        _insert_entry(USER, "work", "Trabaja como el creador de Vectrax.", 0.9)
        _insert_entry(USER, "work", "Trabaja como yo quien diseñó imagino y.", 0.3)
        _insert_entry(USER, "work", "Trabaja como tu creador por aquí no.", 0.2)
        _insert_entry(USER, "work", "Trabaja como tus proyectos.", 0.15)
        _insert_entry(USER, "work", "Trabaja como el vendedor.", 0.1)
        _insert_entry(USER, "work", "Trabaja como usuario.", 0.1)
        _insert_entry(USER, "work", "Trabaja como desarrollador.", 0.4)

        # Relationships
        _insert_entry(USER, "relationship", "Ana es su socia.", 0.9)
        _insert_entry(USER, "relationship", "Joy es su contacto.", 0.5)
        _insert_entry(USER, "relationship", "Carlos es su amigo.", 0.3)

        # Goals — many noisy "Quieres..." from conversation
        _insert_entry(USER, "goal", "Quiere crear la mejor IA personal del mundo.", 0.8)
        _insert_entry(USER, "goal", "Quiere observar qué estructura aparece.", 0.3)
        _insert_entry(USER, "goal", "Quiere que parezca una presencia.", 0.2)
        _insert_entry(USER, "goal", "Quiere que le haga un análisis completo.", 0.15)
        _insert_entry(USER, "goal", "Quiere hablar contigo sobre personalidad.", 0.1)
        for i in range(20):
            _insert_entry(USER, "goal", f"Quiere cosas genéricas {i}.", 0.05)

        # Preferences
        _insert_entry(USER, "preference", "Le apasiona la tecnología.", 0.7)
        _insert_entry(USER, "preference", "Prefiere respuestas directas.", 0.6)

    def test_response_is_short(self):
        """Response must be max ~8 sentences (opening + 6 fragments + closing)."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        sentences = [s.strip() for s in resp.split(".") if s.strip()]
        # Opening "Sí. Estás..." + max 6 fragments + closing "Eso ya no..."
        assert len(sentences) <= 10, f"Too many sentences ({len(sentences)}): {resp}"

    def test_response_not_empty(self):
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        assert len(resp) > 20

    def test_contains_name(self):
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        assert "Mario" in resp

    def test_contains_highest_weight_work(self):
        """Should include 'creador de Vectrax' (weight=0.9), not garbage."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        assert "Vectrax" in resp or "creador" in resp

    def test_no_garbage_fragments(self):
        """Must NOT contain raw garbage from noisy extractions."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        # These are actual garbage fragments from the user's report
        assert "yo quien diseñó imagino" not in resp
        assert "tu creador por aquí no" not in resp
        assert "tus proyectos" not in resp
        assert "como usuario" not in resp
        assert "como el vendedor" not in resp

    def test_no_20_goals_dumped(self):
        """Must NOT dump all 20+ generic goals."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        # Count "Quieres" occurrences — max 1
        quieres_count = resp.lower().count("quieres")
        assert quieres_count <= 1, f"Too many goals ({quieres_count}): {resp}"

    def test_max_one_work_fragment(self):
        """Only the highest-weight work entry should appear."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        trabajas_count = resp.lower().count("trabajas")
        assert trabajas_count <= 1, f"Too many work entries ({trabajas_count}): {resp}"

    def test_relationship_included(self):
        """At least one relationship should appear."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        assert "Ana" in resp

    def test_max_two_relationships(self):
        """At most 2 relationships."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        rel_count = sum(1 for name in ["Ana", "Joy", "Carlos"] if name in resp)
        assert rel_count <= 2

    def test_empty_memory_returns_seed(self):
        """No entries → return seed prompt."""
        from vectrax.core_memory import get_living_response
        resp = get_living_response("tg:nobody", "es")
        assert "no tengo" in resp.lower() or "aún" in resp.lower()

    def test_english_response(self):
        """English response when lang='en'."""
        _insert_entry("tg:en_user", "identity", "Se llama John.", 1.0)
        _insert_entry("tg:en_user", "work", "Trabaja como engineer.", 0.8)
        from vectrax.core_memory import get_living_response
        resp = get_living_response("tg:en_user", "en")
        assert "John" in resp
        assert "core memory" in resp.lower()

    def test_dedup_similar_entries(self):
        """Similar entries should be deduplicated."""
        _insert_entry("tg:dedup", "identity", "Se llama Pedro.", 1.0)
        _insert_entry("tg:dedup", "identity", "Vivo en Madrid.", 0.8)
        _insert_entry("tg:dedup", "identity", "Vivo en Madrid España.", 0.7)  # redundant
        from vectrax.core_memory import get_living_response
        resp = get_living_response("tg:dedup", "es")
        madrid_count = resp.lower().count("madrid")
        assert madrid_count == 1, f"Duplicate location: {resp}"

    def test_total_char_length_reasonable(self):
        """Response should be reasonable length, not a wall of text."""
        self._populate_heavy_memory()
        from vectrax.core_memory import get_living_response
        resp = get_living_response(USER, "es")
        # A clean response should be under ~400 chars
        assert len(resp) < 500, f"Response too long ({len(resp)} chars): {resp}"


class TestRedundancyDetection:
    """Test the _is_redundant helper directly."""

    def test_substring_detected(self):
        from vectrax.core_memory import _is_redundant
        assert _is_redundant("Vivo en Madrid.", ["Vivo en Madrid España."]) is True

    def test_reverse_substring_detected(self):
        from vectrax.core_memory import _is_redundant
        assert _is_redundant("Vivo en Madrid España.", ["Vivo en Madrid."]) is True

    def test_high_overlap_detected(self):
        from vectrax.core_memory import _is_redundant
        assert _is_redundant(
            "Trabaja como el creador de Vectrax.",
            ["Trabajas como el creador de Vectrax."],
        ) is True

    def test_different_entries_not_redundant(self):
        from vectrax.core_memory import _is_redundant
        assert _is_redundant("Ana es tu socia.", ["Eres Mario."]) is False

    def test_empty_existing_not_redundant(self):
        from vectrax.core_memory import _is_redundant
        assert _is_redundant("Cualquier cosa.", []) is False
