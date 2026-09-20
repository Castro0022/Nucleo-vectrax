"""
tests/test_personal_relationship_privacy.py
==============================================
Corrección 2026-09-20 — caso real: "¿Quién es mi novia?" fue clasificada
como intent=UNKNOWN, enrutada a route=online, el sistema "descubrió" en
internet una persona que no existía ("Dani") y la presentó como respuesta
verificada (OK=✓, 100% de éxito en Pipeline Train), sin haber ejecutado
ninguna verificación real.

Estas pruebas cubren el contrato corregido, extremo a extremo:
  1. Consultas relacionales personales se clasifican como memoria personal
     (memory_lookup / local) en las 3 capas de clasificación.
  2. Consultan EXCLUSIVAMENTE memoria aislada del usuario (nunca internet).
  3. Sin evidencia personal suficiente, declaran desconocimiento -- nunca
     salen a la web a "descubrir" quién podría ser esa persona.
  4. El guardrail de privacidad de NucleusAuthority bloquea incondicionalmente
     ONLINE/PLACES/MARKET para estas consultas, incluso si el enrutamiento
     ascendente (SmartRouter) las propusiera.
  5. Los campos delivered/completed/grounded/verified son independientes --
     "completado" nunca se confunde con "verificado".
  6. Aislamiento multiusuario: cada usuario solo ve su propia evidencia.
  7. End-to-end: una consulta relacional nunca puede caer en online.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import vectrax.db as db  # noqa: E402
from vectrax.models import Star  # noqa: E402
import vectrax.core_memory as core_memory  # noqa: E402
import vectrax.embeddings as embeddings  # noqa: E402
import vectrax.resolver as resolver  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Detector genérico is_personal_relationship_query() -- sin nombres propios
# ---------------------------------------------------------------------------

class TestPersonalRelationshipDetector(unittest.TestCase):
    """Vocabulario GENÉRICO -- nunca depende de un nombre propio de persona."""

    def test_detects_common_relational_questions_es(self):
        positives = [
            "¿Quién es mi novia?",
            "quien es mi novio",
            "¿Cómo se llama mi esposa?",
            "¿Cuál es el nombre de mi jefe?",
            "¿Quién es mi socia?",
            "Quién es mi hermana",
        ]
        for text in positives:
            with self.subTest(text=text):
                self.assertTrue(resolver.is_personal_relationship_query(text))

    def test_detects_common_relational_questions_en(self):
        positives = [
            "who is my girlfriend",
            "Who is my boss?",
            "what's my wife's name",
        ]
        for text in positives:
            with self.subTest(text=text):
                self.assertTrue(resolver.is_personal_relationship_query(text))

    def test_does_not_match_statements_to_store(self):
        """Un enunciado que declara la relación (a guardar) NO es una
        consulta -- nunca debe activar el guardrail de privacidad."""
        negatives = [
            "mi novia se llama Ana",
            "Ana es mi socia",
            "trabajo con mi hermana",
            "hoy vi a mi novia",
        ]
        for text in negatives:
            with self.subTest(text=text):
                self.assertFalse(resolver.is_personal_relationship_query(text))

    def test_unrelated_questions_not_matched(self):
        negatives = [
            "¿Quién es el presidente de Francia?",
            "¿Qué hora es?",
            "who is Albert Einstein",
        ]
        for text in negatives:
            with self.subTest(text=text):
                self.assertFalse(resolver.is_personal_relationship_query(text))

    def test_no_hardcoded_names_in_vocabulary(self):
        """El vocabulario relacional es genérico -- nunca nombres propios de
        una persona concreta ni reglas especiales para nadie."""
        blob = resolver._PERSONAL_RELATIONSHIP_QUERY_RE.pattern.lower()
        for forbidden in ("mario", "dani", "ana", "bravo"):
            self.assertNotIn(forbidden, blob)


# ---------------------------------------------------------------------------
# 2. Las 3 capas de clasificación coinciden: memory_lookup / local
# ---------------------------------------------------------------------------

class TestClassificationLayersAgree(unittest.TestCase):
    """resolver.classify(), SemanticClassifier y SmartRouter deben coincidir
    en que esto es memoria personal, nunca búsqueda web."""

    def test_resolver_classify_is_local(self):
        self.assertEqual(resolver.classify("¿Quién es mi novia?"), "local")

    def test_semantic_classifier_is_memory_lookup(self):
        from core.semantic_classifier import get_semantic_classifier, SemanticIntent
        result = get_semantic_classifier().classify("¿Quién es mi novia?")
        self.assertEqual(result.intent, SemanticIntent.MEMORY_LOOKUP)
        self.assertEqual(result.frame, "ask_memory")

    def test_smart_router_is_local_not_online(self):
        from core.smart_router import Intent, SmartRouter
        intent, signals = SmartRouter().classify_intent("¿Quién es mi novia?")
        self.assertEqual(intent, Intent.LOCAL)
        self.assertNotEqual(intent, Intent.ONLINE)

    def test_memory_rescue_bug_fixed_for_memory_lookup_mapping(self):
        """Regresión puntual: el rescate 'semántico=memory vs regex=online'
        comparaba contra el valor equivocado y nunca disparaba para
        MEMORY_LOOKUP (que mapea a Intent.LOCAL, no a Intent.MEMORY)."""
        from core.smart_router import SmartRouter, Intent
        router = SmartRouter()
        # Forzar regex=ONLINE y semantic=LOCAL con confianza baja para
        # ejercitar explícitamente la rama de rescate.
        import core.semantic_classifier as sc_mod

        class _FakeSemanticResult:
            intent = sc_mod.SemanticIntent.MEMORY_LOOKUP
            confidence = 0.2
            frame = "ask_memory"
            entities = []
            suggested_tools = []
            scores = {}

        with patch.object(router, "_classify_semantic", return_value=_FakeSemanticResult()):
            with patch.object(
                router, "_classify_regex",
                return_value=(Intent.ONLINE, {"word_count": 5}),
            ):
                intent, signals = router.classify_intent("¿Quién es mi novia?")
        self.assertEqual(intent, Intent.LOCAL)
        self.assertEqual(signals.get("classification_method"), "semantic_memory_rescue")


# ---------------------------------------------------------------------------
# Fixture: DB temporal aislada (mismo patrón que test_resolver_memory_synthesis)
# ---------------------------------------------------------------------------

class _IsolatedDBMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vx_privacy_test_")
        self._orig_dir = db.DB_DIR
        self._orig_path = db.DB_PATH
        db.DB_DIR = Path(self._tmpdir)
        db.DB_PATH = Path(self._tmpdir) / "vectrax.db"
        db.init_db()

        self._orig_core_memory_db_path = core_memory._DB_PATH
        core_memory._DB_PATH = os.path.join(self._tmpdir, "user_memory.db")

        self._fixed_vec = __import__("numpy").array([1.0, 0.0], dtype="float32")
        self._embed_patch = patch.object(embeddings, "embed", return_value=self._fixed_vec)
        self._decode_patch = patch.object(embeddings, "decode_embedding", return_value=self._fixed_vec)
        self._embed_patch.start()
        self._decode_patch.start()

    def tearDown(self):
        self._embed_patch.stop()
        self._decode_patch.stop()
        db.DB_DIR = self._orig_dir
        db.DB_PATH = self._orig_path
        core_memory._DB_PATH = self._orig_core_memory_db_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @staticmethod
    def _add_star(content: str, channel: str, owner: str) -> None:
        db.insert_star(Star(
            id=str(uuid.uuid4()), content=content, timestamp=time.time(),
            embedding=b"dummy", channel=channel, owner=owner,
        ))


# ---------------------------------------------------------------------------
# 3. resolve_local() / resolve(): nunca escalan a internet, declaran
#    desconocimiento honesto cuando no hay evidencia.
# ---------------------------------------------------------------------------

class TestNoWebEscalationForPersonalRelationshipQueries(_IsolatedDBMixin, unittest.TestCase):

    def test_resolve_never_calls_resolve_online_without_memory(self):
        """resolve() (orquestador legacy) NO debe caer a resolve_online()
        aunque la memoria esté vacía -- ver bug real: 'no hay evidencia'
        + fallback automático a internet."""
        with patch.object(resolver, "resolve_online") as mock_online:
            result = resolver.resolve(
                "¿Quién es mi novia?", channel="user", owner="privacy_user_empty",
            )
        mock_online.assert_not_called()
        self.assertEqual(result.mode, "local")
        self.assertEqual(result.context_stars, 0)

    def test_resolve_local_declares_unknown_when_no_evidence(self):
        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            res = resolver.resolve_local(
                "¿Quién es mi novia?", channel="user", owner="privacy_user_empty2",
            )
        self.assertEqual(res.context_stars, 0)
        self.assertIn(
            res.sovereign_answer,
            ("No tengo información sobre eso en mi memoria.",
             "I don't have information about that in my memory."),
        )

    def test_resolve_local_answers_from_own_evidence_when_present(self):
        """Cuando SÍ hay evidencia propia, resolve_local() la usa -- nunca
        se bloquea la respuesta legítima, solo la fuga a internet."""
        self._add_star("Ana es mi socia.", "user", "privacy_user_with_data")
        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            res = resolver.resolve_local(
                "¿Quién es mi socia?", channel="user", owner="privacy_user_with_data",
                threshold=0.0,
            )
        self.assertGreater(res.context_stars, 0)
        self.assertIn("Ana", res.sovereign_answer)


# ---------------------------------------------------------------------------
# 4. NucleusAuthority: guardrail de privacidad + grounded/verified honestos
# ---------------------------------------------------------------------------

class TestNucleusAuthorityPrivacyGuard(_IsolatedDBMixin, unittest.TestCase):

    def test_end_to_end_resolves_local_and_declares_unknown(self):
        """Desde PARTE 4 (RESOLVE_PERSONAL_MEMORY), esta consulta se decide
        en el paso 5.5 de _decide() -- ANTES de que exista candidata
        LOCAL/SmartRouter -- así que la acción final es PERSONAL_MEMORY,
        no LOCAL. Ambas despachan por el mismo ejecutor (resolve_local),
        así que la garantía real (nunca sale a internet) se preserva."""
        from core.nucleus.nucleus_authority import NucleusAuthority
        resp = NucleusAuthority().resolve(
            "¿Quién es mi novia?", channel="user", owner="privacy_e2e_user", source="telegram",
        )
        self.assertEqual(resp.final_action, "PERSONAL_MEMORY")
        # PARTE 3 (2026-09-20): PERSONAL_MEMORY ahora despacha a la
        # capacidad de recuperación universal (retrieve_personal_memory),
        # no al resolve_local() genérico -- la garantía real (nunca sale a
        # internet) se preserva igual.
        self.assertEqual(resp.tool_executed, "retrieve_personal_memory")
        self.assertNotEqual(resp.tool_executed, "resolve_online")
        self.assertIn(
            resp.answer,
            ("No tengo información sobre eso en mi memoria.",
             "I don't have information about that in my memory."),
        )

    def test_guardrail_overrides_online_even_if_smart_router_proposes_it(self):
        """Desde PARTE 4, la garantía es incluso más fuerte que un guardrail
        posterior (paso 7b): el paso 5.5 de _decide() asigna
        RESOLVE_PERSONAL_MEMORY directamente para esta consulta ANTES de
        que SmartRouter sea invocado, así que SmartRouter.route() nunca se
        llega a ejecutar aunque esté forzado a devolver ONLINE. El mock de
        SmartRoute(ONLINE) queda deliberadamente sin usar -- eso es lo que
        se está verificando (Núcleo decide una sola vez, sin reinterpretación
        posterior)."""
        from core.nucleus.nucleus_authority import NucleusAuthority
        from core.smart_router import SmartRouter, SmartRoute, Strategy, Intent, RiskLevel

        fake_route = SmartRoute(
            intent=Intent.ONLINE, topic="general", risk_level=RiskLevel.LOW,
            strategy=Strategy.RESOLVE_ONLINE, confidence=0.9,
            reason="forced-for-test",
        )
        with patch.object(SmartRouter, "route", return_value=fake_route) as mock_route:
            resp = NucleusAuthority().resolve(
                "¿Quién es mi novia?", channel="user", owner="privacy_guard_user",
                source="telegram",
            )
        mock_route.assert_not_called()
        self.assertEqual(resp.final_action, "PERSONAL_MEMORY")
        self.assertIn("RESOLVE_PERSONAL_MEMORY", resp.reason)

    def test_grounded_and_verified_false_when_no_evidence(self):
        """Sin evidencia real, grounded/verified deben ser False -- nunca
        se marca como verificado solo porque el ciclo terminó."""
        from core.transport.pipeline_worker import _compute_grounding
        from core.nucleus.nucleus_authority import NucleusAuthority

        resp = NucleusAuthority().resolve(
            "¿Quién es mi novia?", channel="user", owner="privacy_ground_empty",
            source="telegram",
        )
        grounded, verified = _compute_grounding(resp)
        self.assertFalse(grounded)
        self.assertFalse(verified)

    def test_grounded_and_verified_true_with_real_personal_evidence(self):
        self._add_star("Ana es mi socia.", "user", "privacy_ground_full")
        from core.transport.pipeline_worker import _compute_grounding
        from core.nucleus.nucleus_authority import NucleusAuthority

        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            resp = NucleusAuthority().resolve(
                "¿Quién es mi socia?", channel="user", owner="privacy_ground_full",
                source="telegram",
            )
        grounded, verified = _compute_grounding(resp)
        self.assertTrue(grounded)
        self.assertTrue(verified)

    def test_online_route_is_grounded_but_never_verified(self):
        """Una respuesta ONLINE puede estar 'grounded' (fuentes reales) pero
        JAMÁS 'verified' en el sentido de evidencia del MISMO usuario --
        son conceptos distintos, no intercambiables."""
        from core.transport.pipeline_worker import _compute_grounding
        from core.nucleus.nucleus_authority import NucleusResponse

        resp = NucleusResponse(
            final_action="ONLINE",
            evidence={"sources": ["https://example.com"], "engines_used": ["duckduckgo"]},
        )
        grounded, verified = _compute_grounding(resp)
        self.assertTrue(grounded)
        self.assertFalse(verified)


# ---------------------------------------------------------------------------
# 5. Aislamiento multiusuario
# ---------------------------------------------------------------------------

class TestMultiUserIsolationForRelationshipQueries(_IsolatedDBMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._add_star("Ana es mi socia.", "user", "user_alpha")
        self._add_star("Carla es mi socia.", "user", "user_beta")

    def test_each_user_only_sees_own_relationship_evidence(self):
        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            res_alpha = resolver.resolve_local(
                "¿Quién es mi socia?", channel="user", owner="user_alpha", threshold=0.0,
            )
            res_beta = resolver.resolve_local(
                "¿Quién es mi socia?", channel="user", owner="user_beta", threshold=0.0,
            )
        self.assertIn("Ana", res_alpha.sovereign_answer)
        self.assertNotIn("Carla", res_alpha.sovereign_answer)
        self.assertIn("Carla", res_beta.sovereign_answer)
        self.assertNotIn("Ana", res_beta.sovereign_answer)

    def test_third_user_with_no_data_gets_honest_unknown_not_other_users_data(self):
        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            res = resolver.resolve_local(
                "¿Quién es mi socia?", channel="user", owner="user_gamma_empty", threshold=0.0,
            )
        self.assertEqual(res.context_stars, 0)
        self.assertNotIn("Ana", res.sovereign_answer)
        self.assertNotIn("Carla", res.sovereign_answer)


if __name__ == "__main__":
    unittest.main()
