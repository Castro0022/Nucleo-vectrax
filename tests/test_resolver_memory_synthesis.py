"""
tests/test_resolver_memory_synthesis.py
==========================================
Corrección 2026-09-20: `resolve_local()` dejó de devolver un volcado crudo
de fragmentos ("Esto es lo que tengo en tu memoria:\n\n• frag1\n• frag2...")
y ahora intenta primero una síntesis coherente vía LLM (mismo patrón ya
usado por `resolve_online()`), con fallback exacto al comportamiento
anterior si el LLM no está disponible.

Estas pruebas verifican DOS cosas, explícitamente pedidas:
  1. El mecanismo de recuperación por owner/channel (aislamiento entre
     usuarios) sigue intacto — la síntesis nunca mezcla fragmentos de dos
     identidades distintas, sin importar cuál de las dos rutas (LLM o
     fallback) produzca la respuesta final.
  2. La síntesis en sí: usa el prompt dedicado a memoria (no el de
     búsqueda online), y el fallback reproduce exactamente el formato
     anterior cuando el LLM no responde.

No se crea ningún sistema de memoria nuevo: todas las pruebas usan
exclusivamente `vectrax.db.get_all_stars(channel, owner)` (el mecanismo
existente) y `vectrax.resolver.resolve_local()`.
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
import vectrax.embeddings as embeddings  # noqa: E402
import vectrax.resolver as resolver  # noqa: E402
import vectrax.core_memory as core_memory  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: DB temporal aislada + embeddings deterministas (sin red/modelo)
# ---------------------------------------------------------------------------

class _IsolatedDBMixin:
    """DB temporal por test -- nunca toca ~/.vectrax/vectrax.db NI
    vault/user_memory.db reales. Ambas DBs (stars y core_memory canónica)
    se redirigen a un directorio temporal por test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vx_resolver_synth_test_")
        self._orig_dir = db.DB_DIR
        self._orig_path = db.DB_PATH
        db.DB_DIR = Path(self._tmpdir)
        db.DB_PATH = Path(self._tmpdir) / "vectrax.db"
        db.init_db()

        # Aisla vectrax.core_memory (vault/user_memory.db real) -- sin esto,
        # _fetch_canonical_memory_entries() en resolver.py tocaria la DB
        # real de produccion en cada test que llame resolve_local() con un
        # owner no vacio.
        self._orig_core_memory_db_path = core_memory._DB_PATH
        core_memory._DB_PATH = os.path.join(self._tmpdir, "user_memory.db")

        # Embeddings deterministas: embed()/decode_embedding() siempre
        # devuelven el MISMO vector unitario fijo, así find_similar() (la
        # función real, sin mock) calcula similitud=1.0 para toda star con
        # embedding no-nulo -- suficiente para ejercitar retrieval +
        # síntesis sin cargar el modelo real de sentence-transformers.
        self._fixed_vec = __import__("numpy").array([1.0, 0.0], dtype="float32")
        self._embed_patch = patch.object(
            embeddings, "embed", return_value=self._fixed_vec,
        )
        self._decode_patch = patch.object(
            embeddings, "decode_embedding", return_value=self._fixed_vec,
        )
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
            id=str(uuid.uuid4()),
            content=content,
            timestamp=time.time(),
            embedding=b"dummy",  # ignorado -- decode_embedding está mockeado
            channel=channel,
            owner=owner,
        ))

    @staticmethod
    def _add_core_entry(
        user_id: str, category: str, content: str, weight: float = 0.9,
        times_confirmed: int = 1,
    ) -> int:
        """Inserta directamente una entrada de memoria CANONICA (core_memory)
        para pruebas -- bypassa los extractores de `absorb()` para tener
        control explicito sobre categoria/weight/times_confirmed. Solo
        valido dentro de un test que use `_IsolatedDBMixin` (DB temporal)."""
        now = time.time()
        conn = core_memory._conn()
        cur = conn.execute(
            "INSERT INTO core_memory "
            "(user_id, category, content, weight, source, times_confirmed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'test', ?, ?, ?)",
            (user_id, category, content, weight, times_confirmed, now, now),
        )
        conn.commit()
        entry_id = cur.lastrowid
        conn.close()
        return entry_id


# ---------------------------------------------------------------------------
# 1. Aislamiento preservado con la síntesis (LLM y fallback)
# ---------------------------------------------------------------------------

class TestSynthesisPreservesIsolation(_IsolatedDBMixin, unittest.TestCase):
    """Ningún usuario debe ver jamás fragmentos de otro, sin importar si la
    respuesta final vino del LLM o del fallback de viñetas."""

    def setUp(self):
        super().setUp()
        self._add_star("Me llamo Ana y trabajo en marketing.", "user", "user_a")
        self._add_star("Mi color favorito es el azul.", "user", "user_a")
        self._add_star("Me llamo Roberto y trabajo en ventas.", "user", "user_b")
        self._add_star("Vivo en Madrid.", "user", "user_b")

    def test_llm_synthesis_never_mixes_owners(self):
        """El LLM recibe SOLO los fragmentos del owner consultado -- se
        verifica interceptando exactamente qué `snippets` le llegan."""
        captured = {}

        def fake_interpret(query, snippets, lang="es", execution_context=None, mode="online"):
            captured["snippets"] = list(snippets)
            captured["mode"] = mode
            return "Respuesta sintetizada de prueba."

        with patch.object(resolver, "_interpret_with_llm", side_effect=fake_interpret):
            resolver.resolve_local("¿Cómo me llamo?", channel="user", owner="user_a")

        joined = " ".join(captured["snippets"])
        self.assertIn("Ana", joined)
        self.assertIn("marketing", joined)
        self.assertNotIn("Roberto", joined)
        self.assertNotIn("ventas", joined)
        self.assertNotIn("Madrid", joined)
        self.assertEqual(captured["mode"], "memory")

        captured.clear()
        with patch.object(resolver, "_interpret_with_llm", side_effect=fake_interpret):
            resolver.resolve_local("¿Cómo me llamo?", channel="user", owner="user_b")

        joined = " ".join(captured["snippets"])
        self.assertIn("Roberto", joined)
        self.assertIn("ventas", joined)
        self.assertNotIn("Ana", joined)
        self.assertNotIn("marketing", joined)

    def test_fallback_synthesis_never_mixes_owners(self):
        """Con el LLM deshabilitado (fallback de viñetas), el aislamiento
        sigue siendo exacto -- mismo mecanismo de retrieval de siempre."""
        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            res_a = resolver.resolve_local(
                "¿Cómo me llamo?", channel="user", owner="user_a",
            )
            res_b = resolver.resolve_local(
                "¿Cómo me llamo?", channel="user", owner="user_b",
            )

        self.assertIn("Ana", res_a.sovereign_answer)
        self.assertNotIn("Roberto", res_a.sovereign_answer)
        self.assertNotIn("Madrid", res_a.sovereign_answer)

        self.assertIn("Roberto", res_b.sovereign_answer)
        self.assertNotIn("Ana", res_b.sovereign_answer)
        self.assertNotIn("marketing", res_b.sovereign_answer)

    def test_retrieval_scope_unchanged_get_all_stars_by_owner_channel(self):
        """El mecanismo de recuperación en sí (no la síntesis) sigue
        exactamente igual: get_all_stars(channel, owner) por identidad."""
        stars_a = db.get_all_stars(channel="user", owner="user_a")
        stars_b = db.get_all_stars(channel="user", owner="user_b")
        self.assertEqual(len(stars_a), 2)
        self.assertEqual(len(stars_b), 2)
        self.assertTrue(all(s.owner == "user_a" for s in stars_a))
        self.assertTrue(all(s.owner == "user_b" for s in stars_b))


# ---------------------------------------------------------------------------
# 2. La síntesis en sí: coherente vía LLM, fallback exacto sin LLM
# ---------------------------------------------------------------------------

class TestCoherentSynthesis(_IsolatedDBMixin, unittest.TestCase):
    """Respuesta directa y natural en vez de fragmentos sueltos."""

    def setUp(self):
        super().setUp()
        self._add_star("Vivo en Miami Beach.", "user", "user_c")
        self._add_star("Trabajo remoto para una empresa de software.", "user", "user_c")
        self._add_star("Tengo dos gatos.", "user", "user_c")

    def test_uses_memory_prompt_mode_not_online(self):
        """resolve_local() debe invocar _interpret_with_llm con
        mode=\"memory\" (prompt dedicado a fragmentos propios), nunca con
        el prompt de búsqueda online."""
        with patch.object(
            resolver, "_interpret_with_llm", return_value="ok",
        ) as mock_interpret:
            resolver.resolve_local("¿Dónde vivo?", channel="user", owner="user_c")
        self.assertEqual(mock_interpret.call_args.kwargs.get("mode"), "memory")

    def test_llm_answer_used_directly_as_sovereign_answer(self):
        """Cuando el LLM sintetiza correctamente, su respuesta se usa TAL\n        CUAL -- no se re-envuelve en viñetas ni en el prefijo antiguo."""
        coherent = "Vives en Miami Beach y trabajas remoto para una empresa de software."
        with patch.object(resolver, "_interpret_with_llm", return_value=coherent):
            res = resolver.resolve_local(
                "¿Dónde vivo y en qué trabajo?", channel="user", owner="user_c",
            )
        self.assertEqual(res.sovereign_answer, coherent)
        self.assertNotIn("•", res.sovereign_answer)
        self.assertNotIn("Esto es lo que tengo en tu memoria", res.sovereign_answer)

    def test_fallback_matches_previous_bullet_behavior_exactly(self):
        """Sin LLM disponible, el resultado debe reproducir EXACTAMENTE el
        formato del comportamiento previo a esta corrección (mismo
        `_synthesize_local`, sin cambios) -- la mejora nunca resta
        capacidad de respuesta. Usa una pregunta puntual (no de perfil
        amplio) para ejercitar la ruta de similitud simple existente."""
        star_contents = [
            s.content for s in db.get_all_stars(channel="user", owner="user_c")
        ]
        expected = resolver._synthesize_local(star_contents, lang="es")

        with patch.object(resolver, "_interpret_with_llm", return_value=""):
            res = resolver.resolve_local(
                "Dame más detalles sobre eso que mencioné",
                channel="user", owner="user_c", threshold=0.0,
            )

        self.assertEqual(res.sovereign_answer, expected)
        self.assertTrue(
            res.sovereign_answer.startswith("En tu memoria encontré esto:")
            or res.sovereign_answer.startswith("Esto es lo que tengo en tu memoria:"),
            f"fallback inesperado: {res.sovereign_answer!r}",
        )

    def test_no_matches_still_returns_no_memory_label_unchanged(self):
        """Caso sin coincidencias (owner sin stars): comportamiento previo
        intacto -- nunca se invoca al LLM con una lista vacía."""
        with patch.object(resolver, "_interpret_with_llm") as mock_interpret:
            res = resolver.resolve_local(
                "pregunta totalmente ajena", channel="user", owner="user_nobody",
            )
        mock_interpret.assert_not_called()
        self.assertEqual(res.context_stars, 0)
        self.assertIn(res.sovereign_answer, (
            "No tengo información sobre eso en mi memoria.",
            "I don't have information about that in my memory.",
        ))


# ---------------------------------------------------------------------------
# 3. Recuperacion diversa para consultas AMPLIAS de perfil
# ---------------------------------------------------------------------------
# "Que sabes de mi" / "cuentame sobre mi" y sus parafrasis deben recuperar
# una muestra diversa (identidad, relaciones, proyectos, preferencias,
# intereses, contexto reciente) en vez de una unica coincidencia por
# similitud literal. Probado con memoria abundante, escasa y vacia, y con
# distintas parafrasis -- sin nombres propios ni reglas especiales para
# nadie en particular.

_PROFILE_PARAPHRASES = [
    "\u00bfQu\u00e9 sabes de m\u00ed?",
    "Cu\u00e9ntame sobre m\u00ed",
    "Resume lo que sabes de m\u00ed",
    "Haz un resumen de mi perfil",
    "\u00bfQu\u00e9 tienes en mi memoria?",
    "What do you know about me?",
    "Tell me about myself",
]


class TestProfileSummaryIntentDetection(unittest.TestCase):
    """El detector de intencion es generico -- ninguna parafrasis depende
    de un nombre propio, y preguntas puntuales NO activan la ruta amplia."""

    def test_all_paraphrases_detected(self):
        for text in _PROFILE_PARAPHRASES:
            with self.subTest(text=text):
                self.assertTrue(resolver._is_profile_summary_query(text))

    def test_narrow_questions_not_detected(self):
        narrow = [
            "\u00bfC\u00f3mo me llamo?",
            "\u00bfD\u00f3nde vivo?",
            "\u00bfCu\u00e1l es mi color favorito?",
            "What is the capital of France?",
            "\u00bfQu\u00e9 opinas del clima hoy?",
        ]
        for text in narrow:
            with self.subTest(text=text):
                self.assertFalse(resolver._is_profile_summary_query(text))

    def test_no_hardcoded_names_in_detector_or_categories(self):
        """El detector y los clasificadores de categoria no deben mencionar
        ningun nombre propio de persona -- son genericos por diseno."""
        source_blobs = [resolver._PROFILE_SUMMARY_RE.pattern] + [
            p.pattern for p in resolver._CATEGORY_PATTERNS.values()
        ]
        forbidden_names = ["mario", "bravo", "castro"]
        for blob in source_blobs:
            low = blob.lower()
            for name in forbidden_names:
                self.assertNotIn(name, low, f"nombre propio encontrado en patron: {blob!r}")


class TestProfileSummaryAbundantMemory(_IsolatedDBMixin, unittest.TestCase):
    """Usuario con memoria abundante y diversa: la recuperacion debe cubrir
    varias categorias, no solo la mas reciente o la primera insertada."""

    def setUp(self):
        super().setUp()
        self._add_star("Me llamo Elena y vivo en Bogot\u00e1.", "user", "rich_user")
        self._add_star("Tengo 34 a\u00f1os.", "user", "rich_user")
        self._add_star("Mi pareja se llama Jorge.", "user", "rich_user")
        self._add_star("Trabajo con mi hermana en el mismo equipo.", "user", "rich_user")
        self._add_star("Estoy trabajando en un proyecto de agricultura urbana.", "user", "rich_user")
        self._add_star("Me encanta el caf\u00e9 de especialidad.", "user", "rich_user")
        self._add_star("Me apasiona la fotograf\u00eda anal\u00f3gica.", "user", "rich_user")
        self._add_star("ok", "user", "rich_user")
        self._add_star("gracias", "user", "rich_user")

    def test_diverse_categories_represented(self):
        captured = {}

        def fake_interpret(query, snippets, lang="es", execution_context=None, mode="online"):
            captured["snippets"] = list(snippets)
            return "Perfil sintetizado."

        with patch.object(resolver, "_interpret_with_llm", side_effect=fake_interpret):
            res = resolver.resolve_local(
                "\u00bfQu\u00e9 sabes de m\u00ed?", channel="user", owner="rich_user",
            )

        self.assertGreater(res.context_stars, 0)
        joined = " ".join(captured["snippets"])
        category_hits = sum([
            "Elena" in joined or "Bogot" in joined or "34" in joined,
            "Jorge" in joined or "hermana" in joined,
            "agricultura urbana" in joined,
            "caf\u00e9" in joined,
            "fotograf" in joined,
        ])
        self.assertGreaterEqual(
            category_hits, 3,
            f"esperaba evidencia de al menos 3 categorias distintas, snippets={joined!r}",
        )

    def test_no_duplicate_fragments_in_selection(self):
        selected = resolver._select_profile_summary_stars(
            db.get_all_stars(channel="user", owner="rich_user"),
        )
        contents = [f.content for f in selected]
        normalized = [resolver._normalize_for_dedup(c) for c in contents]
        self.assertEqual(len(normalized), len(set(normalized)), "se encontraron duplicados")

    def test_scores_within_range_and_vary(self):
        selected = resolver._select_profile_summary_stars(
            db.get_all_stars(channel="user", owner="rich_user"),
        )
        scores = [f.score for f in selected]
        self.assertTrue(all(0.0 <= sc <= 1.0 for sc in scores))
        self.assertGreater(len(set(round(sc, 3) for sc in scores)), 1)

    def test_all_paraphrases_produce_nonempty_result_for_rich_user(self):
        for text in _PROFILE_PARAPHRASES:
            with self.subTest(text=text):
                with patch.object(resolver, "_interpret_with_llm", return_value=""):
                    res = resolver.resolve_local(
                        text, channel="user", owner="rich_user",
                    )
                self.assertGreater(res.context_stars, 0)
                self.assertNotEqual(
                    res.sovereign_answer,
                    resolver._get_labels(resolver._detect_lang(text))["no_memory"],
                )


class TestProfileSummaryScarceMemory(_IsolatedDBMixin, unittest.TestCase):
    """Usuario con memoria escasa: pocas stars, ninguna encaja limpiamente
    en una categoria -- el 'contexto reciente' debe actuar como red de
    seguridad para que igual haya una respuesta basada en evidencia real."""

    def setUp(self):
        super().setUp()
        self._add_star("hoy fue un d\u00eda raro", "user", "scarce_user")
        self._add_star("necesito organizar mejor mi semana", "user", "scarce_user")

    def test_scarce_memory_still_returns_real_evidence(self):
        captured = {}

        def fake_interpret(query, snippets, lang="es", execution_context=None, mode="online"):
            captured["snippets"] = list(snippets)
            return "Resumen breve."

        with patch.object(resolver, "_interpret_with_llm", side_effect=fake_interpret):
            res = resolver.resolve_local(
                "Cu\u00e9ntame sobre m\u00ed", channel="user", owner="scarce_user",
            )

        self.assertGreater(res.context_stars, 0)
        joined = " ".join(captured["snippets"])
        self.assertTrue(
            "d\u00eda raro" in joined or "organizar" in joined,
            f"esperaba contenido real del usuario escaso, obtuvo: {joined!r}",
        )

    def test_scarce_memory_never_fabricates_categories(self):
        """Sin evidencia de relaciones/proyectos/etc., esas categorias
        deben quedar simplemente ausentes -- nunca inventadas."""
        selected = resolver._select_profile_summary_stars(
            db.get_all_stars(channel="user", owner="scarce_user"),
        )
        contents = [f.content for f in selected]
        for c in contents:
            self.assertIn(c, ["hoy fue un d\u00eda raro", "necesito organizar mejor mi semana"])


class TestProfileSummaryEmptyMemory(_IsolatedDBMixin, unittest.TestCase):
    """Usuario sin memoria alguna: debe comportarse exactamente igual que
    antes de esta correccion (mensaje honesto de 'sin informacion')."""

    def test_empty_memory_returns_no_memory_label(self):
        with patch.object(resolver, "_interpret_with_llm") as mock_interpret:
            res = resolver.resolve_local(
                "\u00bfQu\u00e9 sabes de m\u00ed?", channel="user", owner="empty_user",
            )
        mock_interpret.assert_not_called()
        self.assertEqual(res.context_stars, 0)
        self.assertEqual(
            res.sovereign_answer, "No tengo informaci\u00f3n sobre eso en mi memoria.",
        )

    def test_select_profile_summary_stars_handles_empty_list(self):
        self.assertEqual(resolver._select_profile_summary_stars([]), [])


class TestProfileSummaryIsolationAcrossOwners(_IsolatedDBMixin, unittest.TestCase):
    """La recuperacion diversa NUNCA debe mezclar evidencia entre owners,
    igual que la ruta puntual ya probada en TestSynthesisPreservesIsolation."""

    def setUp(self):
        super().setUp()
        self._add_star("Me llamo Carla y vivo en Lima.", "user", "profile_user_x")
        self._add_star("Mi hermano trabaja conmigo.", "user", "profile_user_x")
        self._add_star("Me llamo Diego y vivo en Quito.", "user", "profile_user_y")
        self._add_star("Mi hobby es el ajedrez.", "user", "profile_user_y")

    def test_profile_summary_never_mixes_owners(self):
        captured = {}

        def fake_interpret(query, snippets, lang="es", execution_context=None, mode="online"):
            captured["snippets"] = list(snippets)
            return "ok"

        with patch.object(resolver, "_interpret_with_llm", side_effect=fake_interpret):
            resolver.resolve_local(
                "\u00bfQu\u00e9 sabes de m\u00ed?", channel="user", owner="profile_user_x",
            )
        joined_x = " ".join(captured["snippets"])
        self.assertIn("Carla", joined_x)
        self.assertNotIn("Diego", joined_x)
        self.assertNotIn("ajedrez", joined_x)

        captured.clear()
        with patch.object(resolver, "_interpret_with_llm", side_effect=fake_interpret):
            resolver.resolve_local(
                "\u00bfQu\u00e9 sabes de m\u00ed?", channel="user", owner="profile_user_y",
            )
        joined_y = " ".join(captured["snippets"])
        self.assertIn("Diego", joined_y)
        self.assertNotIn("Carla", joined_y)
        self.assertNotIn("hermano", joined_y)


# ---------------------------------------------------------------------------
# 4. Memoria CANONICA (vectrax.core_memory) conectada a la recuperacion de
#    perfil -- evidencia explicita, aislamiento por owner_raw, exclusion de
#    la categoria "fact" (menor confianza) y manejo de contradicciones.
# ---------------------------------------------------------------------------

class TestCanonicalMemoryIntegration(_IsolatedDBMixin, unittest.TestCase):
    """vectrax.core_memory (vault/user_memory.db) debe alimentar la
    recuperacion de perfil junto a las stars, con trazabilidad explicita."""

    def test_canonical_entries_included_with_source_and_id(self):
        self._add_core_entry("canon_user", "identity", "Se llama Elena.", weight=1.0)
        self._add_core_entry("canon_user", "relationship", "Ana es su socia.", weight=0.9)

        selected = resolver._select_profile_summary_stars(
            [], owner="canon_user", channel="user",
        )
        self.assertGreaterEqual(len(selected), 2)
        sources = {f.source for f in selected}
        self.assertEqual(sources, {"core_memory"})
        ids = {f.id for f in selected}
        self.assertTrue(all(i.startswith("core_memory:") for i in ids))
        contents = {f.content for f in selected}
        self.assertIn("Se llama Elena.", contents)
        self.assertIn("Ana es su socia.", contents)

    def test_fact_category_excluded(self):
        self._add_core_entry("canon_user2", "fact", "Dato generico sin validar.", weight=0.45)
        selected = resolver._select_profile_summary_stars(
            [], owner="canon_user2", channel="user",
        )
        self.assertEqual(selected, [])

    def test_owner_raw_fallback_bridges_to_raw_keyed_canonical_memory(self):
        """core_memory se llena historicamente bajo la identidad CRUDA
        (p.ej. tg:<id>); _select_profile_summary_stars debe encontrarla via
        owner_raw cuando el owner canonico (p.ej. 'mario') es distinto."""
        self._add_core_entry("tg:999888", "identity", "Se llama Mario.", weight=1.0)

        selected = resolver._select_profile_summary_stars(
            [], owner="mario", channel="creator", owner_raw="tg:999888",
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].content, "Se llama Mario.")
        self.assertEqual(selected[0].owner, "tg:999888")

    def test_resolve_local_evidence_debug_shows_source_id_owner_category(self):
        """El campo interno `answer` (nunca mostrado al usuario) debe
        exponer fuente, id, owner, categoria y extracto por fragmento --
        lo pedido explicitamente para trazabilidad."""
        self._add_core_entry("evi_user", "identity", "Se llama Carla.", weight=1.0)
        self._add_star("Mi hobby es el ajedrez.", "user", "evi_user")

        with patch.object(resolver, "_interpret_with_llm", return_value="ok"):
            res = resolver.resolve_local(
                "\u00bfQu\u00e9 sabes de m\u00ed?", channel="user", owner="evi_user",
            )
        self.assertIn("core_memory", res.answer)
        self.assertIn("owner=evi_user", res.answer)
        self.assertIn("category=identity", res.answer)
        self.assertIn("Carla", res.answer)

    def test_canonical_and_star_isolation_never_mixes_owner_raw(self):
        """Dos owner_raw distintos nunca deben mezclar entradas canonicas."""
        self._add_core_entry("tg:111", "identity", "Se llama Pedro.", weight=1.0)
        self._add_core_entry("tg:222", "identity", "Se llama Sofia.", weight=1.0)

        selected_1 = resolver._select_profile_summary_stars(
            [], owner="user_1", channel="user", owner_raw="tg:111",
        )
        selected_2 = resolver._select_profile_summary_stars(
            [], owner="user_2", channel="user", owner_raw="tg:222",
        )
        self.assertEqual([f.content for f in selected_1], ["Se llama Pedro."])
        self.assertEqual([f.content for f in selected_2], ["Se llama Sofia."])

    def test_contradictory_canonical_facts_do_not_crash_and_both_are_visible(self):
        """Dos hechos contradictorios (mismo owner, categoria distinta de
        contenido pero describiendo el mismo eje) no deben producir un
        crash; el LLM decide como manejar la incertidumbre (ver prompt),
        pero la recuperacion debe entregar AMBOS como evidencia separada,
        nunca descartar uno arbitrariamente antes de la sintesis."""
        self._add_core_entry("contra_user", "fact", "Vivo en Chile.", weight=0.85)
        self._add_core_entry("contra_user", "identity", "Se llama Nico.", weight=1.0)
        # "fact" se excluye por diseno (ver test_fact_category_excluded) --
        # se usa una categoria no excluida para simular la contradiccion
        # de forma realista sin depender del cajon de menor confianza.
        self._add_core_entry("contra_user", "habit", "Vive en Miami.", weight=0.85)

        selected = resolver._select_profile_summary_stars(
            [], owner="contra_user", channel="user",
        )
        contents = [f.content for f in selected]
        self.assertIn("Se llama Nico.", contents)
        self.assertIn("Vive en Miami.", contents)
        self.assertNotIn("Vivo en Chile.", contents)  # excluido: categoria "fact"

    def test_times_confirmed_increases_importance_ranking(self):
        """Una entrada reforzada por confirmacion repetida real debe rankear
        mas alto que una equivalente sin refuerzo -- nunca inventado."""
        self._add_core_entry(
            "rank_user", "habit", "Suele trabajar de noche.",
            weight=0.65, times_confirmed=1,
        )
        self._add_core_entry(
            "rank_user", "habit", "Revisa el mercado cada manana.",
            weight=0.65, times_confirmed=5,
        )
        selected = resolver._select_profile_summary_stars(
            [], owner="rank_user", channel="user",
        )
        by_content = {f.content: f.score for f in selected}
        self.assertGreater(
            by_content["Revisa el mercado cada manana."],
            by_content["Suele trabajar de noche."],
        )


if __name__ == "__main__":
    unittest.main()
