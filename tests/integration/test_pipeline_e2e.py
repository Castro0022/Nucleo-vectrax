"""
Integration tests — Full message pipeline end-to-end.

Tests the real flow a Telegram message follows:
  input → SmartRouter → Resolver → ResponseConsolidator → DB

No external services (Telegram, OpenAI, Gemini) are called.
All tests use a temporary SQLite database.

Run:
    pytest tests/integration/test_pipeline_e2e.py -v
"""

from __future__ import annotations

import tempfile
import shutil
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures — isolated temp DB for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    """Redirect vectrax.db to a temp directory so tests don't touch real data."""
    tmp = tempfile.mkdtemp(prefix="vx_test_")
    tmp_path = Path(tmp)

    import vectrax.db as db
    monkeypatch.setattr(db, "DB_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "vectrax.db")
    db.init_db()

    yield tmp_path

    shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# 1. Unified DB — init_db creates ALL tables in one call
# ===========================================================================

class TestUnifiedDB:
    """Verify that init_db() creates the complete schema."""

    def test_all_tables_created(self):
        import sqlite3
        import vectrax.db as db

        conn = sqlite3.connect(db.DB_PATH)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        expected = {
            "stars", "constellations", "proposals", "conversations",
            "trajectories", "queries", "responses", "provider_weights",
            "feedback", "resolution_log",
        }
        assert expected.issubset(tables), f"Missing: {expected - tables}"

    def test_cross_reference_columns(self):
        import sqlite3
        import vectrax.db as db

        conn = sqlite3.connect(db.DB_PATH)
        q_cols = {r[1] for r in conn.execute("PRAGMA table_info(queries)")}
        r_cols = {r[1] for r in conn.execute("PRAGMA table_info(resolution_log)")}
        conn.close()

        assert "star_id" in q_cols
        assert "query_id" in r_cols

    def test_insert_query_and_response(self):
        import vectrax.db as db

        qid = db.insert_query("test prompt", topic="code", routing_mode="SINGLE")
        assert isinstance(qid, int) and qid > 0

        rid = db.insert_response(qid, "openai", "response text", latency_ms=120)
        assert isinstance(rid, int) and rid > 0

        full = db.get_query_with_responses(qid)
        assert full is not None
        assert full["prompt"] == "test prompt"
        assert len(full["responses"]) == 1
        assert full["responses"][0]["provider"] == "openai"

    def test_query_star_link(self):
        import vectrax.db as db
        from vectrax.models import Star

        star = Star(content="nota de prueba", channel="user", owner="test")
        db.insert_star(star)

        qid = db.insert_query("qué dije ayer", topic="general", star_id=star.id)
        q = db.get_query(qid)
        assert q["star_id"] == star.id

        full = db.get_query_with_responses(qid)
        assert full["star"] is not None
        assert full["star"]["content"] == "nota de prueba"


# ===========================================================================
# 2. SmartRouter — classify + route full pipeline
# ===========================================================================

class TestSmartRouterPipeline:
    """Test the 5-step routing pipeline end-to-end."""

    def _router(self):
        from core.smart_router import SmartRouter
        return SmartRouter()

    def test_memory_statement_routes_correctly(self):
        r = self._router()
        route = r.route("hoy tuve una buena sesión de trabajo", "user", "test")
        assert route.intent.value == "memory"
        assert route.strategy.value == "resolve_memory"
        assert route.confidence >= 0.5

    def test_online_question_routes_correctly(self):
        r = self._router()
        route = r.route("qué es la fotosíntesis?", "user", "test")
        assert route.intent.value == "online"
        assert route.strategy.value == "resolve_online"

    def test_command_routes_correctly(self):
        r = self._router()
        route = r.route("/help", "user", "test")
        assert route.intent.value == "command"
        assert route.strategy.value == "execute_command"

    def test_market_query_routes_correctly(self):
        r = self._router()
        route = r.route("precio de btc", "user", "test")
        assert route.intent.value == "market"
        assert route.strategy.value == "resolve_market"

    def test_local_memory_routes_correctly(self):
        r = self._router()
        route = r.route("qué dije ayer sobre trading", "user", "test")
        assert route.intent.value == "local"
        assert route.strategy.value == "resolve_local"

    def test_sensitive_topic_gets_high_risk(self):
        r = self._router()
        route = r.route("analiza mi entrada de btc en soporte", "user", "test")
        assert route.risk_level.value == "HIGH"
        assert route.topic == "trading"

    def test_route_metadata_has_latency(self):
        r = self._router()
        route = r.route("hola mundo", "user", "test")
        assert "routing_latency_ms" in route.metadata
        assert route.metadata["routing_latency_ms"] >= 0

    def test_record_feedback_does_not_crash(self):
        r = self._router()
        route = r.route("test", "user", "test")
        # Should not raise even if learning modules are unavailable
        r.record_feedback(route, success=True, response_latency_ms=100.0)

    def test_stats_after_routing(self):
        r = self._router()
        r.route("nota uno", "user", "test")
        r.route("nota dos", "user", "test")
        r.record_outcome(r.route("qué es python", "user", "test"), True, 50.0)

        stats = r.stats()
        assert stats["total_routes"] >= 1
        assert "strategy_distribution" in stats


# ===========================================================================
# 3. Resolver — classify intent (regex path)
# ===========================================================================

class TestResolverClassify:
    """Test the resolver's classify() function directly."""

    def test_question_mark_is_online(self):
        from vectrax.resolver import classify
        assert classify("cuántos planetas hay?") == "online"

    def test_semantic_query_without_mark(self):
        from vectrax.resolver import classify
        assert classify("explica cómo funciona la gravedad") == "online"

    def test_plain_statement_is_memory(self):
        from vectrax.resolver import classify
        assert classify("hoy entrené 40 minutos") == "memory"

    def test_self_reference_is_local(self):
        from vectrax.resolver import classify
        assert classify("qué dije sobre bitcoin") == "local"

    def test_my_history_is_local(self):
        from vectrax.resolver import classify
        assert classify("show my history") == "local"


# ===========================================================================
# 4. ResponseConsolidator — dedup + priority
# ===========================================================================

class TestConsolidatorIntegration:
    """Test consolidation across realistic multi-source responses."""

    def _consolidator(self):
        from core.response_consolidator import ResponseConsolidator
        return ResponseConsolidator()

    def _frag(self, text, source, complete=True):
        from core.response_consolidator import ResponseFragment
        return ResponseFragment(text=text, source=source, is_complete=complete)

    def test_memory_always_wins(self):
        c = self._consolidator()
        frags = [
            self._frag("Tu nombre es Mario.", "memory"),
            self._frag("No tengo esa información.", "llm"),
        ]
        text, trace = c.consolidate(frags)
        assert "Mario" in text
        assert "No tengo" not in text

    def test_places_over_llm(self):
        c = self._consolidator()
        frags = [
            self._frag("Farmacia Cruz Verde — Av. 5 #100. Abierta.", "places"),
            self._frag("Busca una farmacia en Google Maps.", "llm"),
        ]
        text, trace = c.consolidate(frags)
        assert "Cruz Verde" in text

    def test_exact_dedup(self):
        c = self._consolidator()
        frags = [
            self._frag("Python es genial.", "llm", complete=False),
            self._frag("Python es genial.", "online", complete=False),
        ]
        text, trace = c.consolidate(frags)
        assert trace["duplicates_removed"] >= 1

    def test_single_fragment_passthrough(self):
        c = self._consolidator()
        text, trace = c.consolidate([self._frag("Respuesta única.", "llm")])
        assert text == "Respuesta única."
        assert trace["strategy"] == "single_fragment"

    def test_empty_returns_empty(self):
        c = self._consolidator()
        text, trace = c.consolidate([])
        assert text == ""


# ===========================================================================
# 5. Intent Engine — parse action intent
# ===========================================================================

class TestIntentEngine:
    """Test that IntentEngine extracts executable intents."""

    def _engine(self):
        from core.intent_engine import IntentEngine
        return IntentEngine()

    def test_create_intent(self):
        e = self._engine()
        pkg = e.parse("crea un módulo de autenticación con JWT")
        assert pkg.action.value == "create"
        assert pkg.confidence > 0.5

    def test_modify_intent(self):
        e = self._engine()
        pkg = e.parse("corrige el bug en el router de Vectrax")
        assert pkg.action.value == "modify"

    def test_query_intent_from_question(self):
        e = self._engine()
        pkg = e.parse("qué hora es en Tokyo?")
        assert pkg.action.value == "query"

    def test_analyze_intent(self):
        e = self._engine()
        pkg = e.parse("analiza el rendimiento del sistema de memoria")
        assert pkg.action.value == "analyze"

    def test_scope_system_for_architecture(self):
        e = self._engine()
        pkg = e.parse("rediseña la arquitectura del sistema completo")
        assert pkg.scope.value in ("system", "cross_module")


# ===========================================================================
# 6. Full roundtrip: route → classify → log in DB
# ===========================================================================

class TestFullRoundtrip:
    """Simulate the pipeline worker's routing + logging flow."""

    def test_route_and_log_query(self):
        from core.smart_router import SmartRouter
        import vectrax.db as db

        router = SmartRouter()
        route = router.route("qué es machine learning?", "user", "test")

        # Log to unified DB (simulating what pipeline_worker does)
        qid = db.insert_query(
            prompt="qué es machine learning?",
            topic=route.topic,
            routing_mode=route.strategy.value,
            routing_reason=route.reason,
            confidence=route.confidence,
            risk_level=route.risk_level.value,
        )

        db.insert_response(qid, "openai", "ML es un campo de la IA...", latency_ms=250)

        db.update_query_result(
            qid,
            final="ML es un campo de la IA...",
            providers_called="openai",
        )

        # Verify full roundtrip
        full = db.get_query_with_responses(qid)
        assert full["topic"] == route.topic
        assert full["routing_mode"] == route.strategy.value
        assert full["final"] == "ML es un campo de la IA..."
        assert len(full["responses"]) == 1

    def test_route_memory_creates_star_and_links(self):
        from core.smart_router import SmartRouter
        from vectrax.models import Star
        import vectrax.db as db

        router = SmartRouter()
        route = router.route("hoy entrené 40 minutos de cardio", "user", "test")
        assert route.strategy.value == "resolve_memory"

        # Simulate star creation (what engine.ingest does)
        star = Star(content="hoy entrené 40 minutos de cardio", channel="user", owner="test")
        db.insert_star(star)

        # Log query linked to star
        qid = db.insert_query(
            prompt="hoy entrené 40 minutos de cardio",
            topic=route.topic,
            routing_mode=route.strategy.value,
            confidence=route.confidence,
            star_id=star.id,
        )

        full = db.get_query_with_responses(qid)
        assert full["star_id"] == star.id
        assert full["star"]["content"] == "hoy entrené 40 minutos de cardio"

    def test_recent_queries_returns_ordered(self):
        import vectrax.db as db

        db.insert_query("primero", topic="general")
        db.insert_query("segundo", topic="code")
        db.insert_query("tercero", topic="code")

        recent = db.get_recent_queries(limit=10)
        assert len(recent) == 3
        assert recent[0]["prompt"] == "tercero"  # most recent first

        by_topic = db.get_recent_queries(topic="code", limit=10)
        assert len(by_topic) == 2
        assert all(q["topic"] == "code" for q in by_topic)
