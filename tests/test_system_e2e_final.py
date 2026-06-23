"""
Vectrax System — Final End-to-End Test Suite
================================================
Comprehensive test covering ALL subsystems in a unified run:

  1. Word Gravity Index (WGI)
  2. Gravity Activator
  3. Telegram Guard (Silent Mode)
  4. API Rate Limit Gate
  5. SmartRouter (intent classification + routing)
  6. Identity Layer + Identity Anchor
  7. Response Consolidator
  8. Language Gate
  9. Law Enforcement + Decision Authority
 10. Full Pipeline Integration (gateway → router → resolver)

Run:
    pytest tests/test_system_e2e_final.py -v

Creado: 2026-06-08
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import threading

import pytest


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_wgi_db(monkeypatch, tmp_path):
    """Redirect WGI to a temp database for each test."""
    db_path = str(tmp_path / "test_wgi.db")
    try:
        monkeypatch.setattr("core.word_gravity._DB_PATH", db_path)
        monkeypatch.setattr("core.word_gravity._initialized", False)
    except Exception:
        pass
    try:
        import core.gravity_activator
        monkeypatch.setattr(
            "core.gravity_activator._load_constellation_content",
            lambda cids, limit=5: [],
        )
    except Exception:
        pass
    yield db_path


@pytest.fixture(autouse=True)
def reset_api_gates():
    """Reset all API gates before each test."""
    try:
        from core.api_gate import reset_all_gates
        reset_all_gates()
    except Exception:
        pass
    yield
    try:
        from core.api_gate import reset_all_gates
        reset_all_gates()
    except Exception:
        pass


# ===========================================================================
# 1. WORD GRAVITY INDEX — Core CRUD + Seed + Decay
# ===========================================================================

class TestWGI_CoreOperations:
    """Word Gravity Index: insert, update, mass capping, seed, decay."""

    def test_upsert_new_word(self):
        from core.word_gravity import upsert_word, get_word_mass
        mass = upsert_word("vectrax", delta=0.5)
        assert mass == 0.5
        assert get_word_mass("vectrax") == 0.5

    def test_upsert_increment(self):
        from core.word_gravity import upsert_word, get_word_mass
        upsert_word("mercado", delta=0.3)
        upsert_word("mercado", delta=0.2)
        assert abs(get_word_mass("mercado") - 0.5) < 0.001

    def test_mass_capped_at_one(self):
        from core.word_gravity import upsert_word, get_word_mass
        upsert_word("cap_test", delta=0.8)
        upsert_word("cap_test", delta=0.5)
        assert get_word_mass("cap_test") == 1.0

    def test_stopword_ignored(self):
        from core.word_gravity import upsert_word
        assert upsert_word("el", delta=0.5) == 0.0

    def test_seed_creates_foundational_words(self):
        from core.word_gravity import seed_global_index, get_word_mass
        count = seed_global_index()
        assert count > 0
        assert get_word_mass("vectrax") == 0.98
        assert get_word_mass("mario") == 0.92

    def test_seed_idempotent(self):
        from core.word_gravity import seed_global_index
        count1 = seed_global_index()
        count2 = seed_global_index()
        assert count1 > 0
        assert count2 == 0

    def test_extract_keywords_filters_stopwords(self):
        from core.word_gravity import extract_keywords
        kws = extract_keywords("el mercado de las estrellas en la memoria")
        assert "mercado" in kws
        assert "estrellas" in kws
        assert "el" not in kws
        assert "de" not in kws

    def test_normalize_accents_and_case(self):
        from core.word_gravity import normalize_word
        assert normalize_word("VECTRAX") == "vectrax"
        assert normalize_word("convergéncia") == "convergencia"
        assert normalize_word("núcleo") == "nucleo"

    def test_per_user_scope_isolation(self):
        from core.word_gravity import upsert_word, get_word_mass, SCOPE_GLOBAL
        upsert_word("proyecto", scope=SCOPE_GLOBAL, delta=0.4)
        upsert_word("proyecto", scope="tg:user_a", delta=0.8)
        assert abs(get_word_mass("proyecto", SCOPE_GLOBAL) - 0.4) < 0.001
        assert abs(get_word_mass("proyecto", "tg:user_a") - 0.8) < 0.001

    def test_effective_mass_user_overrides_global(self):
        from core.word_gravity import upsert_word, get_effective_mass, SCOPE_GLOBAL
        upsert_word("dato", scope=SCOPE_GLOBAL, delta=0.5)
        upsert_word("dato", scope="tg:100", delta=0.9)
        mass, scope = get_effective_mass("dato", "tg:100")
        assert mass == 0.9
        assert scope == "tg:100"

    def test_effective_mass_global_wins_if_higher(self):
        from core.word_gravity import upsert_word, get_effective_mass, SCOPE_GLOBAL
        upsert_word("core", scope=SCOPE_GLOBAL, delta=0.95)
        upsert_word("core", scope="tg:100", delta=0.3)
        mass, scope = get_effective_mass("core", "tg:100")
        assert mass == 0.95
        assert scope == SCOPE_GLOBAL

    def test_decay_reduces_inactive_words(self):
        from core.word_gravity import (
            upsert_word, get_word_mass, apply_decay,
            _conn, DECAY_GRACE_DAYS,
        )
        upsert_word("oldword", delta=0.5, category="concept")
        conn = _conn()
        old_time = time.time() - (DECAY_GRACE_DAYS + 1) * 86400
        conn.execute(
            "UPDATE word_gravity SET last_activated = ? WHERE word = 'oldword'",
            (old_time,),
        )
        conn.commit()
        conn.close()
        apply_decay()
        assert get_word_mass("oldword") < 0.5

    def test_identity_words_never_decay(self):
        from core.word_gravity import (
            upsert_word, get_word_mass, apply_decay,
            _conn, SCOPE_GLOBAL, DECAY_GRACE_DAYS,
        )
        upsert_word("vectrax", scope=SCOPE_GLOBAL, delta=0.98, category="identity")
        conn = _conn()
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

    def test_constellation_ids_accumulated(self):
        from core.word_gravity import upsert_word, get_word_entry
        upsert_word("star", delta=0.1, constellation_id="id1")
        upsert_word("star", delta=0.1, constellation_id="id2")
        entry = get_word_entry("star")
        assert "id1" in entry.constellation_ids
        assert "id2" in entry.constellation_ids


# ===========================================================================
# 2. GRAVITY ACTIVATOR — Activation, Lock, Bandsaw Prevention
# ===========================================================================

class TestGravityActivator:
    """Gravity Activator: pre-router constellation activation."""

    def _seed(self):
        from core.word_gravity import seed_global_index
        seed_global_index()

    def test_vectrax_triggers_lock(self):
        """'Despierta Vectrax ?' must trigger gravity_lock (bandsaw prevention)."""
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("Despierta Vectrax ?", "tg:123")
        assert result.activated is True
        assert result.lock_internal is True
        assert result.max_word == "vectrax"
        assert result.max_mass == 0.98

    def test_low_mass_no_activation(self):
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("hola buenos dias", "tg:123")
        assert result.activated is False
        assert result.lock_internal is False

    def test_medium_mass_activates_no_lock(self):
        from core.word_gravity import upsert_word
        upsert_word("proyecto", delta=0.7, category="concept")
        from core.gravity_activator import activate_gravity
        result = activate_gravity("háblame del proyecto", "tg:123")
        assert result.activated is True
        assert result.lock_internal is False

    def test_context_generated(self):
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("Despierta Vectrax", "tg:123")
        assert "GRAVITY" in result.context
        assert "vectrax" in result.context

    def test_user_polysemy_override(self):
        self._seed()
        from core.word_gravity import upsert_word
        upsert_word("observador", scope="tg:mario", delta=0.95, category="domain")
        from core.gravity_activator import activate_gravity
        result = activate_gravity("el observador", "tg:mario")
        assert result.activated is True
        assert result.max_mass == 0.95
        assert result.lock_internal is True

    def test_multiple_activations(self):
        self._seed()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("Vectrax y el mercado gravitacional", "tg:123")
        assert result.activated is True
        assert len(result.activations) >= 2
        words = [a.word for a in result.activations]
        assert "vectrax" in words


# ===========================================================================
# 3. MULTI-USER ISOLATION — Concurrent gravity scopes
# ===========================================================================

class TestMultiUserGravityIsolation:
    """Two users never see each other's gravity data."""

    USER_A = "tg:trader_001"
    USER_B = "tg:chef_002"

    def _setup(self):
        from core.word_gravity import seed_global_index, upsert_word
        seed_global_index()
        upsert_word("mercado", scope=self.USER_A, delta=0.92,
                    category="domain", constellation_id="pat_btc")
        upsert_word("mercado", scope=self.USER_B, delta=0.70,
                    category="concept", constellation_id="pat_super")
        upsert_word("analisis", scope=self.USER_A, delta=0.75,
                    category="domain", constellation_id="pat_ta")
        upsert_word("cocina", scope=self.USER_B, delta=0.80,
                    category="domain", constellation_id="pat_cocina")

    def test_same_word_different_mass(self):
        self._setup()
        from core.word_gravity import get_word_mass
        assert get_word_mass("mercado", self.USER_A) > 0.90
        assert get_word_mass("mercado", self.USER_B) < 0.80

    def test_constellations_isolated(self):
        self._setup()
        from core.word_gravity import get_constellation_ids
        cids_a = get_constellation_ids("mercado", self.USER_A)
        cids_b = get_constellation_ids("mercado", self.USER_B)
        assert "pat_btc" in cids_a
        assert "pat_super" in cids_b
        assert "pat_super" not in cids_a
        assert "pat_btc" not in cids_b

    def test_exclusive_words_invisible_to_other(self):
        self._setup()
        from core.gravity_activator import activate_gravity
        res_a = activate_gravity("dame un analisis", self.USER_A)
        res_b = activate_gravity("dame un analisis", self.USER_B)
        assert res_a.activated is True
        assert res_b.activated is False

    def test_concurrent_upserts_no_bleed(self):
        from core.word_gravity import upsert_word, get_word_mass, get_constellation_ids
        upsert_word("receta", scope=self.USER_A, delta=0.3, constellation_id="a1")
        upsert_word("receta", scope=self.USER_B, delta=0.6, constellation_id="b1")
        upsert_word("receta", scope=self.USER_A, delta=0.1, constellation_id="a2")
        assert abs(get_word_mass("receta", self.USER_A) - 0.4) < 0.01
        assert abs(get_word_mass("receta", self.USER_B) - 0.6) < 0.01
        assert "b1" not in get_constellation_ids("receta", self.USER_A)
        assert "a1" not in get_constellation_ids("receta", self.USER_B)


# ===========================================================================
# 4. TELEGRAM GUARD — Silent Mode
# ===========================================================================

class TestTelegramGuard:
    """Telegram output guard: block telemetry, allow user replies + critical."""

    def test_user_reply_always_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Cualquier texto", is_user_reply=True) is True

    def test_user_reply_reason_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Cualquier texto", reason="user_reply") is True

    def test_empty_text_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("") is False

    def test_router_digest_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("📊 Router Digest — 2026-06-08") is False

    def test_idea_notification_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("💡 Nueva idea HIGH: optimizar router") is False

    def test_telemetry_pattern_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("router_telemetry update: 5 routes") is False

    def test_gravity_growth_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("gravity update growth cycle 12") is False

    def test_universe_growth_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("🌌 Universe growth: 5 new stars") is False

    def test_memory_stats_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Memory stats: 500 patterns, 20 users") is False

    def test_scheduler_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Scheduler: 3 tasks pending") is False

    def test_worker_heartbeat_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("worker_heartbeat at 14:30") is False

    def test_intent_classification_leak_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("[INTENT: ONLINE] processing query") is False

    def test_debug_prefix_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("DEBUG processing pipeline step 3") is False

    def test_proactive_reason_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Te recuerdo que...", reason="proactive") is False

    def test_scheduled_reason_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Reporte diario", reason="scheduled") is False

    def test_digest_reason_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Resumen semanal", reason="digest") is False

    def test_reentry_reason_passes(self):
        # Reentry is a deliberate continuity re-engagement, not telemetry —
        # it must reach the user (previously it was silently suppressed).
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Hola de nuevo", reason="reentry") is True

    def test_critical_alert_always_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("CRITICAL: database corruption detected") is True

    def test_urgente_alert_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("URGENTE: sistema caído") is True

    def test_approval_request_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Requiere aprobación: deploy v2.1") is True

    def test_operable_scenario_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("ESCENARIO OPERABLE DETECTADO: BTC 4/4") is True

    def test_normal_conversation_passes(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Hola Mario, aquí está tu respuesta.") is True

    def test_system_prefix_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("⚙️ Sistema: reiniciando workers") is False

    def test_idea_id_pattern_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("Created IDEA-AB12CD34 for optimization") is False

    def test_learn_cycle_blocked(self):
        from core.telegram_guard import should_send_to_user
        assert should_send_to_user("LEARN] cycle 42 complete") is False


# ===========================================================================
# 5. API RATE LIMIT GATE — 429 Backoff
# ===========================================================================

class TestAPIGate:
    """API gate: exponential backoff for 429 errors."""

    def test_gate_starts_open(self):
        from core.api_gate import check_gate
        assert check_gate("openai") is True
        assert check_gate("gemini") is True
        assert check_gate("tavily") is True

    def test_429_closes_gate(self):
        from core.api_gate import check_gate, record_429
        cooldown = record_429("openai")
        assert cooldown == 60  # first 429 → 60s
        assert check_gate("openai") is False

    def test_exponential_backoff(self):
        from core.api_gate import record_429
        c1 = record_429("test_provider")
        c2 = record_429("test_provider")
        c3 = record_429("test_provider")
        assert c1 == 60
        assert c2 == 120
        assert c3 == 300

    def test_max_cooldown_cap(self):
        from core.api_gate import record_429
        for _ in range(10):
            cd = record_429("capped_provider")
        assert cd == 900  # max 15 min

    def test_success_resets_gate(self):
        from core.api_gate import check_gate, record_429, record_success
        record_429("reset_test")
        assert check_gate("reset_test") is False
        record_success("reset_test")
        assert check_gate("reset_test") is True

    def test_gate_status_shows_info(self):
        from core.api_gate import record_429, get_gate_status
        record_429("status_test")
        status = get_gate_status("status_test")
        assert status["provider"] == "status_test"
        assert status["open"] is False
        assert status["consecutive_429s"] == 1
        assert status["total_429s"] == 1
        assert status["remaining_s"] > 0

    def test_all_gates_status(self):
        from core.api_gate import record_429, get_all_gates
        record_429("prov_a")
        record_429("prov_b")
        all_gates = get_all_gates()
        assert "prov_a" in all_gates
        assert "prov_b" in all_gates

    def test_reset_single_gate(self):
        from core.api_gate import record_429, check_gate, reset_gate
        record_429("reset_one")
        assert check_gate("reset_one") is False
        reset_gate("reset_one")
        assert check_gate("reset_one") is True

    def test_providers_independent(self):
        from core.api_gate import check_gate, record_429
        record_429("openai")
        assert check_gate("openai") is False
        assert check_gate("gemini") is True

    def test_thread_safety(self):
        """Multiple threads recording 429s concurrently should not corrupt state."""
        from core.api_gate import record_429, get_gate_status
        errors = []

        def hammer(provider, n):
            try:
                for _ in range(n):
                    record_429(provider)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=hammer, args=("thread_test", 20))
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        status = get_gate_status("thread_test")
        assert status["total_429s"] == 80  # 4 threads × 20


# ===========================================================================
# 6. SMART ROUTER — Intent Classification
# ===========================================================================

class TestSmartRouterClassification:
    """SmartRouter: correct intent and strategy for various inputs."""

    def _router(self):
        from core.smart_router import SmartRouter
        return SmartRouter()

    def test_market_intent(self):
        from core.smart_router import Strategy
        route = self._router().route("precio de btc", "user", "test")
        assert route.strategy == Strategy.RESOLVE_MARKET

    def test_identity_intent(self):
        from core.smart_router import Strategy
        route = self._router().route("quién soy", "user", "test")
        assert route.strategy == Strategy.RESOLVE_IDENTITY

    def test_memory_intent(self):
        route = self._router().route("hoy entrené 40 minutos", "user", "test")
        assert route.intent.value == "memory"

    def test_online_intent(self):
        route = self._router().route("qué es la fotosíntesis?", "user", "test")
        assert route.intent.value == "online"

    def test_command_intent(self):
        route = self._router().route("/help", "user", "test")
        assert route.intent.value == "command"

    def test_local_memory_intent(self):
        route = self._router().route("qué dije ayer sobre trading", "user", "test")
        assert route.intent.value == "local"

    def test_sensitive_topic_gets_high_risk(self):
        route = self._router().route("analiza mi entrada de btc en soporte", "user", "test")
        assert route.risk_level.value == "HIGH"

    def test_route_metadata_has_latency(self):
        route = self._router().route("hola mundo", "user", "test")
        assert "routing_latency_ms" in route.metadata
        assert route.metadata["routing_latency_ms"] >= 0

    def test_feedback_recording_no_crash(self):
        r = self._router()
        route = r.route("test message", "user", "test")
        r.record_feedback(route, success=True, response_latency_ms=50.0)

    def test_stats_after_routing(self):
        r = self._router()
        route1 = r.route("nota uno", "user", "test")
        route2 = r.route("nota dos", "user", "test")
        # stats() only counts routes registered via record_outcome/record_feedback
        r.record_outcome(route1, True, 50.0)
        r.record_outcome(route2, True, 30.0)
        stats = r.stats()
        assert stats["total_routes"] >= 2
        assert "strategy_distribution" in stats


# ===========================================================================
# 7. RESPONSE CONSOLIDATOR — Dedup + Priority
# ===========================================================================

class TestResponseConsolidator:
    """Response consolidation: dedup, priority, single output."""

    def _consolidator(self):
        from core.response_consolidator import ResponseConsolidator
        return ResponseConsolidator()

    def _frag(self, text, source, complete=True):
        from core.response_consolidator import ResponseFragment
        return ResponseFragment(text=text, source=source, is_complete=complete)

    def test_memory_wins_over_llm(self):
        c = self._consolidator()
        frags = [
            self._frag("Tu nombre es Mario.", "memory"),
            self._frag("No tengo esa información.", "llm"),
        ]
        text, trace = c.consolidate(frags)
        assert "Mario" in text
        assert "No tengo" not in text

    def test_places_wins_over_llm(self):
        c = self._consolidator()
        frags = [
            self._frag("Farmacia Cruz Verde — Av. 5 #100.", "places"),
            self._frag("Busca en Google Maps.", "llm"),
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
# 8. LANGUAGE GATE — Detect + Enforce
# ===========================================================================

class TestLanguageGate:
    """Language detection and consistency enforcement."""

    def test_detect_spanish(self):
        from core.language_gate import detect_language
        assert detect_language("Hola, ¿cómo estás?") == "es"

    def test_detect_english(self):
        from core.language_gate import detect_language
        assert detect_language("Hello, how are you doing today?") == "en"

    def test_detect_french(self):
        from core.language_gate import detect_language
        assert detect_language("Bonjour, comment ça va aujourd'hui?") == "fr"

    def test_detect_italian(self):
        from core.language_gate import detect_language
        assert detect_language("Ciao, come stai oggi?") == "it"

    def test_detect_german(self):
        from core.language_gate import detect_language
        assert detect_language("Hallo, wie geht es Ihnen heute?") == "de"

    def test_detect_portuguese(self):
        from core.language_gate import detect_language
        # Avoid 'que' (triggers ES/FR disambiguation). Use strong PT exclusive markers.
        assert detect_language("Bom dia, tudo bem? Obrigado pela ajuda hoje") == "pt"

    def test_unknown_for_empty(self):
        from core.language_gate import detect_language
        assert detect_language("") == "unknown"

    def test_spanish_not_confused_with_portuguese(self):
        from core.language_gate import detect_language
        lang = detect_language("Necesito que me digas cómo funciona el sistema")
        assert lang == "es"


# ===========================================================================
# 9. IDENTITY + LAW ENFORCEMENT
# ===========================================================================

class TestIdentityAndLaws:
    """Identity integrity, fundamental laws, decision authority."""

    def test_identity_immutable(self):
        from core.operator.identity import IDENTITY, verify_identity_integrity
        assert IDENTITY.name == "Vectrax Core"
        assert IDENTITY.creator == "Mario Bravo Castro"
        assert verify_identity_integrity() is True

    def test_7_fundamental_laws(self):
        from core.operator.identity import FUNDAMENTAL_LAWS
        assert len(FUNDAMENTAL_LAWS) == 7

    def test_invariant_check(self):
        from core.invariants import check_identity_integrity
        assert check_identity_integrity() is None

    def test_all_laws_pass(self):
        from core.operator.law_enforcement import enforce_all_laws
        result = enforce_all_laws(
            input_classified=True,
            classification="market",
            interaction_recorded=True,
            action_logged=True,
            has_correlation_id=True,
        )
        assert result.all_passed is True

    def test_violations_detected(self):
        from core.operator.law_enforcement import enforce_all_laws
        result = enforce_all_laws(
            input_classified=False,
            interaction_recorded=False,
            action_logged=False,
            has_correlation_id=False,
        )
        assert result.all_passed is False
        assert len(result.violations) >= 2

    def test_internal_decisions_auto(self):
        from core.operator.decision_authority import can_auto_execute
        assert can_auto_execute("classify_message") is True
        assert can_auto_execute("store_memory") is True

    def test_external_decisions_require_auth(self):
        from core.operator.decision_authority import requires_creator
        assert requires_creator("apply_autopatch") is True
        assert requires_creator("execute_trade") is True

    def test_blocked_operations_always_blocked(self):
        from core.operator.decision_authority import is_blocked
        assert is_blocked("modify_identity") is True
        assert is_blocked("modify_fundamental_laws") is True


# ===========================================================================
# 10. FULL PIPELINE INTEGRATION
# ===========================================================================

class TestFullPipelineIntegration:
    """End-to-end message processing through ExternalGateway."""

    def test_gateway_processes_greeting(self):
        """A greeting should be processed without crash (response depends on LLM availability)."""
        from core.operator.external_gateway import ExternalGateway
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="test:e2e_final_001",
            content="hola",
            channel="web",
        )
        assert result.processed is True
        assert result.error == ""  # no error even if response is empty (no LLM)

    def test_gateway_processes_identity_query(self):
        """Identity query without prior memory should return seed prompt."""
        from core.operator.external_gateway import ExternalGateway
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id="test:e2e_final_002",
            content="quién soy",
            channel="web",
        )
        assert result.processed is True
        assert result.response != ""

    def test_hypothesis_survives_reset(self):
        """Hypothesis persists in SQLite across singleton resets."""
        from core.operator.hypothesis_engine import (
            get_hypothesis_engine, reset_hypothesis_engine,
        )
        engine = get_hypothesis_engine()
        hyp = engine.propose(
            description="E2E final test hypothesis",
            tags=["e2e_final"],
        )
        hyp_id = hyp.id
        reset_hypothesis_engine()
        engine2 = get_hypothesis_engine()
        loaded = engine2.get(hyp_id)
        assert loaded is not None
        assert loaded.description == "E2E final test hypothesis"
        reset_hypothesis_engine()


# ===========================================================================
# 11. BANDSAW PREVENTION — Complete Scenario
# ===========================================================================

class TestBandsawPrevention:
    """
    Full integration test for the bandsaw incident prevention.
    'Despierta Vectrax ?' must NEVER reach web search.
    """

    def test_despierta_vectrax_gravity_locks(self):
        from core.word_gravity import seed_global_index
        seed_global_index()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("Despierta Vectrax ?", "tg:2030762343")
        assert result.activated is True
        assert result.lock_internal is True
        assert result.max_word == "vectrax"
        assert result.max_mass >= 0.85

    def test_que_es_vectrax_also_locks(self):
        from core.word_gravity import seed_global_index
        seed_global_index()
        from core.gravity_activator import activate_gravity
        result = activate_gravity("¿Qué es Vectrax?", "tg:999")
        assert result.activated is True
        assert result.lock_internal is True


# ===========================================================================
# 12. CROSS-SUBSYSTEM INTEGRATION
# ===========================================================================

class TestCrossSubsystemIntegration:
    """Tests that verify multiple subsystems working together correctly."""

    def test_guard_plus_gate_coordination(self):
        """API gate closed + telegram guard should both function independently."""
        from core.api_gate import record_429, check_gate
        from core.telegram_guard import should_send_to_user

        record_429("openai")
        assert check_gate("openai") is False
        # Telegram guard should still work independently
        assert should_send_to_user("Respuesta normal") is True
        assert should_send_to_user("📊 Router Digest") is False

    def test_gravity_with_router(self):
        """Gravity lock should influence routing decisions."""
        from core.word_gravity import seed_global_index
        seed_global_index()
        from core.gravity_activator import activate_gravity

        # "Vectrax" triggers lock → router should NOT go ONLINE
        result = activate_gravity("Vectrax ?", "tg:123")
        assert result.lock_internal is True

        # "qué es la fotosíntesis" does NOT trigger lock → router can go ONLINE
        result2 = activate_gravity("qué es la fotosíntesis", "tg:123")
        assert result2.lock_internal is False

    def test_consolidator_with_language_gate(self):
        """Consolidator output should be in a detectable language."""
        from core.response_consolidator import ResponseConsolidator, ResponseFragment
        from core.language_gate import detect_language

        c = ResponseConsolidator()
        frags = [
            ResponseFragment(text="Tu nombre es Mario Bravo.", source="memory"),
        ]
        text, _ = c.consolidate(frags)
        lang = detect_language(text)
        assert lang == "es"

    def test_guard_allows_critical_even_during_telemetry_storm(self):
        """Critical alerts bypass the guard even if preceded by telemetry."""
        from core.telegram_guard import should_send_to_user
        # Simulate a burst of telemetry (all blocked)
        for i in range(10):
            assert should_send_to_user(f"📊 Router Digest cycle {i}") is False
        # Critical alert must still pass through
        assert should_send_to_user("CRITICAL: database down") is True
        assert should_send_to_user("URGENTE: sistema caído") is True

    def test_gate_reopens_after_cooldown(self):
        """Gate should reopen after the backoff window passes."""
        from core.api_gate import check_gate, record_429, _gates, _lock

        record_429("fast_reopen")
        assert check_gate("fast_reopen") is False

        # Manually expire the gate
        with _lock:
            _gates["fast_reopen"].gate_closed_until = time.time() - 1

        assert check_gate("fast_reopen") is True
