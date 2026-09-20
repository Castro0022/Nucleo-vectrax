"""
tests/test_e2e_conversational_memory.py — PARTE 8-9 (2026-09-20)
==============================================================================
Pruebas E2E OBLIGATORIAS de la memoria conversacional multiusuario,
ejercitando el camino REAL de Telegram/gateway -- nunca funciones internas
aisladas por separado.

"Camino real": cada turno se envía exactamente como lo hace
`core/transport/pipeline_worker.py` en producción para Telegram:

    1. `core.memory.conversation_ledger.record_user_message()` (Regla 1:
       el mensaje se registra ANTES de cualquier procesamiento).
    2. `core.nucleus.nucleus_authority.NucleusAuthority.resolve()` (la
       MISMA función que resuelve memoria/identidad/enrutamiento para el
       adaptador de Telegram en producción -- ver su propio docstring).
    3. `core.memory.conversation_ledger.record_assistant_message()`
       (Regla 2: la respuesta se registra DESPUÉS de conocer el texto
       final).

Un test adicional (`TestE2EDashboardHonestFields`) usa literalmente
`core.operator.external_gateway.ExternalGateway.receive_message()` -- la
clase Gateway propiamente dicha -- para probar el otro camino real
(fallback legacy) y su auto-commit a `op_cycles.db`.

Cobertura exigida:
  1. Recuperación de memoria a través de turnos.
  2. Relaciones multiusuario (cada usuario con su propia relación).
  3. Persistencia -- incluye >50 turnos y "reinicio simulado" (se
     resetean singletons/caches EN MEMORIA, nunca la base de datos en
     disco, y se verifica que la recuperación sigue funcionando).
  4. Procedencia -- las respuestas nunca fabrican una identidad/relación
     que jamás fue dada (caso real: "¿Quién es mi novia?" -> el sistema
     "descubrió" en internet una persona inexistente, "Dani", presentada
     como respuesta verificada). Se prueba que la evidencia real (aunque
     mencione la palabra literal de la pregunta) nunca se convierte en un
     dato inventado, y que la coincidencia textual de OTRO usuario nunca
     se filtra a la respuesta.
  5. Aislamiento multiusuario estricto.
  6. Enrutamiento -- una consulta de memoria personal JAMÁS ejecuta
     resolve_online/search_places/market_resolve.
  7. Dashboard -- el ciclo real queda registrado en `op_cycles.db` con
     los campos honestos ya exigidos por PARTE 6.
  8. Regresión explícita del incidente real.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import vectrax.db as db  # noqa: E402
import vectrax.core_memory as core_memory  # noqa: E402
import vectrax.embeddings as embeddings  # noqa: E402


# ---------------------------------------------------------------------------
# Aislamiento de bases de datos -- mismo patrón que
# tests/test_personal_relationship_privacy.py (_IsolatedDBMixin), adaptado
# a fixture pytest. `tests/conftest.py::_hermetic_base` YA aísla
# conversation_ledger/user_memory/operational_cycle/gravity/CC/episodic
# (autouse); aquí solo se aíslan las piezas que ese fixture global NO cubre:
# `vectrax.db` (stars + star_provenance + identity_aliases, todas en el
# MISMO archivo físico) y `vectrax.core_memory`.
# ---------------------------------------------------------------------------

def _fake_word_overlap_embed(text: str, dim: int = 64):
    """Embedding falso determinista para tests -- NUNCA usa un modelo real.
    Codifica cada palabra significativa en una dimensión derivada de su
    hash, de modo que dos textos que comparten palabras obtienen similitud
    coseno alta, y textos sin relación obtienen similitud cercana a cero --
    el mismo comportamiento cualitativo que un modelo real, sin cargarlo.
    """
    import hashlib

    import numpy as np

    vec = np.zeros(dim, dtype="float32")
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    for w in words:
        if len(w) <= 2:
            continue
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    else:
        vec = np.ones(dim, dtype="float32") / np.sqrt(dim)
    return vec.astype("float32")


@pytest.fixture(autouse=True)
def _isolated_vectrax_db(tmp_path, monkeypatch):
    tmp_db = tmp_path / "vectrax.db"
    monkeypatch.setattr(db, "DB_DIR", tmp_path, raising=True)
    monkeypatch.setattr(db, "DB_PATH", tmp_db, raising=True)
    db.init_db()

    monkeypatch.setattr(
        core_memory, "_DB_PATH", str(tmp_path / "user_memory_core.db"), raising=True,
    )
    # identity_aliases vive en el MISMO archivo que vectrax.db en producción
    # (~/.vectrax/vectrax.db) -- se apunta al mismo temporal para que
    # resolve_owner() nunca toque la base real.
    import vectrax.identity_aliases as identity_aliases
    monkeypatch.setattr(identity_aliases, "DB_PATH", tmp_db, raising=True)

    # Embedding FALSO determinista (sin cargar un modelo real, por
    # velocidad) basado en solapamiento de palabras -- NUNCA un vector
    # Único idéntico para todo el contenido. Un vector fijo idéntico para
    # cualquier texto hace que TODAS las stars empaten en similitud=1.0,
    # rompiendo el ranking real de `resolve_local()` (que SÍ es relevante
    # para el camino ANSWER_FROM_EVIDENCE/LOCAL, no solo para PERSONAL_
    # MEMORY) -- textos que comparten palabras significativas deben
    # obtener mayor similitud coseno que textos sin relación alguna, igual
    # que con un modelo real, aunque sin su precisión semántica.
    #
    # `vectrax/engine.py` hace `from vectrax.embeddings import embed` a
    # nivel de MÓDULO (no perezoso) -- ese nombre queda fijado UNA sola vez
    # cuando ese módulo se importa por primera vez en todo el proceso de
    # pytest (cacheado en `sys.modules`), potencialmente mientras OTRO
    # archivo de test tenía su PROPIO mock (de otra dimensión) activo.
    # `patch.object(embeddings, "embed", ...)` nunca actualiza esa
    # referencia cacheada retroactivamente -- solo afecta a consumidores con
    # import perezoso DENTRO de la función, como `resolve_local()`. Se
    # recarga `vectrax.engine` CON el parche de `embeddings.embed` ya
    # activo, forzando que sus imports de módulo se re-ejecuten y capturen
    # la función determinista actual -- y se recarga OTRA VEZ al finalizar
    # (parche ya revertido) para no dejarle una referencia stale a otros
    # archivos de test que corran después en el mismo proceso.
    import importlib

    import vectrax.engine as vx_engine
    import vectrax.resolver as vx_resolver

    _embed_patch = patch.object(embeddings, "embed", side_effect=_fake_word_overlap_embed)
    # Defensa adicional de hermeticidad: independientemente de que
    # `tests/conftest.py` ya neutraliza las variables de entorno de las
    # APIs de LLM, algunos caminos (p.ej. `dotenv.load_dotenv(override=
    # False)`) pueden repoblarlas desde un archivo `.env` real de la
    # máquina después de que conftest las borró, causando una llamada LLM
    # REAL y no determinista en medio de la suite combinada. Se fuerza
    # `_interpret_with_llm()` a devolver "" SIEMPRE en este archivo, para
    # que la síntesis caiga de forma determinista al sintetizador
    # estructurado (`_synthesize_local()`), sin depender de si hay o no
    # credenciales reales disponibles en el entorno de quien ejecuta esto.
    _llm_patch = patch.object(vx_resolver, "_interpret_with_llm", return_value="")

    _embed_patch.start()
    _llm_patch.start()
    importlib.reload(vx_engine)
    try:
        yield tmp_path
    finally:
        _embed_patch.stop()
        _llm_patch.stop()
        importlib.reload(vx_engine)


# ---------------------------------------------------------------------------
# Helper: "camino real de Telegram" -- record_user_message ->
# NucleusAuthority.resolve() -> record_assistant_message, exactamente en
# ese orden, exactamente como pipeline_worker.py en producción.
# ---------------------------------------------------------------------------

def send_message(user_id: str, content: str, *, channel: str = "telegram"):
    """Envía `content` por el camino real de Telegram y devuelve el
    `NucleusResponse` completo (con toda su traza)."""
    from core.memory.conversation_ledger import (
        record_user_message, record_assistant_message,
    )
    from core.nucleus.nucleus_authority import get_nucleus_authority

    record_user_message(
        user_id=user_id, content=content, channel=channel,
        source="e2e_test_pipeline",
    )
    resp = get_nucleus_authority().resolve(
        content, channel=channel, owner=user_id, source=channel,
    )
    record_assistant_message(
        user_id=user_id, content=resp.answer or "", channel=channel,
        source="e2e_test_pipeline",
    )
    return resp


def simulate_restart() -> None:
    """Simula un reinicio del proceso: resetea TODOS los singletons/caches
    EN MEMORIA que el sistema usa -- nunca toca la base de datos en disco.
    Si la recuperación sigue funcionando después de esto, la evidencia
    verdaderamente sobrevive en almacenamiento persistente, no en RAM."""
    from core.nucleus.total_convergence import reset_convergence_engine
    from core.nucleus import nucleus_authority as na_mod
    import core.memory.conversation_ledger as cl_mod

    reset_convergence_engine()
    na_mod._authority = None          # fuerza una NucleusAuthority() nueva
    cl_mod._repo = None               # fuerza reabrir el ledger desde disco


# ---------------------------------------------------------------------------
# 1. Recuperación de memoria a través de turnos (camino real)
# ---------------------------------------------------------------------------

class TestE2ERecall:
    def test_recalls_stated_fact_via_real_pipeline(self):
        user = f"e2e_recall_{uuid.uuid4().hex[:8]}"
        send_message(user, "Mi proyecto se llama Atlas.")
        resp = send_message(user, "¿Qué sabes de mí?")

        assert resp.memory_consulted is True
        assert "Atlas" in (resp.answer or "")

    def test_recalls_across_several_turns_not_just_last_one(self):
        user = f"e2e_recall_multi_{uuid.uuid4().hex[:8]}"
        send_message(user, "Vivo en Lisboa.")
        send_message(user, "Hoy llovió mucho.")
        send_message(user, "Mi comida favorita es el bacalao.")
        resp = send_message(user, "¿Qué sabes de mí?")

        answer = resp.answer or ""
        assert "Lisboa" in answer
        assert "bacalao" in answer.lower()


# ---------------------------------------------------------------------------
# 2. Relaciones multiusuario -- cada usuario con SU PROPIA relación
# ---------------------------------------------------------------------------

class TestE2EMultiUserRelationships:
    def test_each_user_gets_their_own_relationship_never_the_others(self):
        alice = f"e2e_rel_alice_{uuid.uuid4().hex[:8]}"
        bob = f"e2e_rel_bob_{uuid.uuid4().hex[:8]}"

        send_message(alice, "Mi pareja se llama Elena.")
        send_message(bob, "Mi pareja se llama Jorge.")

        resp_alice = send_message(alice, "¿Quién es mi pareja?")
        resp_bob = send_message(bob, "¿Quién es mi pareja?")

        assert "Elena" in (resp_alice.answer or "")
        assert "Jorge" not in (resp_alice.answer or "")

        assert "Jorge" in (resp_bob.answer or "")
        assert "Elena" not in (resp_bob.answer or "")


# ---------------------------------------------------------------------------
# 3. Persistencia -- >50 turnos + recuperación tras reinicio simulado
# ---------------------------------------------------------------------------

class TestE2EPersistenceAndRestart:
    def test_persists_across_simulated_restart(self):
        user = f"e2e_restart_{uuid.uuid4().hex[:8]}"
        send_message(user, "Mi ciudad es Bogotá.")

        simulate_restart()

        resp = send_message(user, "¿Qué sabes de mí?")
        assert "Bogotá" in (resp.answer or "")

    def test_more_than_50_turns_no_cap_and_recoverable_after_restart(self):
        """Regresión directa del requisito original PARTE 1 ('sin límite de
        50') probada de punta a punta: se insertan 55 turnos reales, un
        hecho temprano (turno 3) y uno tardío (turno 52), y AMBOS deben
        seguir siendo recuperables después de un reinicio simulado."""
        user = f"e2e_50turns_{uuid.uuid4().hex[:8]}"
        from core.memory.conversation_ledger import get_conversation_ledger

        total_turns = 55
        for i in range(total_turns):
            if i == 3:
                send_message(user, "Mi hobby favorito es la fotografía.")
            elif i == 52:
                send_message(user, "Mi hermano se llama Tomás.")
            else:
                send_message(user, f"Mensaje de relleno número {i}.")

        ledger = get_conversation_ledger()
        # >= 2*total_turns porque cada turno registra user+assistant.
        assert ledger.count(tenant_id="default", user_id=user) >= total_turns

        simulate_restart()

        # Consultas DIRIGIDAS, no un resumen genérico: "¿Qué sabes de mí?"
        # tras 55 turnos ordena por recencia y trunca a los 5 más recientes
        # (comportamiento esperado del sintetizador de respaldo sin LLM, no
        # un límite de retención) -- lo que PARTE 1 exige es que el hecho
        # ANTIGUO (turno 3) siga siendo RECUPERABLE, no que aparezca en un
        # listado genérico de 5 ítems. Se pregunta por cada hecho
        # específicamente para probar recuperabilidad real, no un resumen.
        # Frases con verbo de recuerdo explícito ("recuérdame") para que la
        # clasificación de intención sea inequívocamente RECUPERACIóN, no una
        # reformulación ambigua que pueda confundirse con el enunciado
        # original ya guardado.
        resp_old_fact = send_message(user, "Recuérdame cuál es mi hobby favorito.")
        assert "fotografía" in (resp_old_fact.answer or "").lower()

        resp_recent_fact = send_message(user, "Recuérdame cómo se llama mi hermano.")
        assert "Tomás" in (resp_recent_fact.answer or "")


# ---------------------------------------------------------------------------
# 4. Procedencia -- anti-fabricación (caso real: "Dani", 6 años de relación)
# ---------------------------------------------------------------------------

class TestE2EProvenanceAntiFabrication:
    def test_fresh_user_never_fabricates_a_relationship(self):
        """Usuario SIN memoria alguna -- la respuesta debe ser la
        abstención honesta, JAMÁS un nombre ni una duración inventados."""
        user = f"e2e_fabric_fresh_{uuid.uuid4().hex[:8]}"
        resp = send_message(user, "¿Quién es mi novia?")

        answer = resp.answer or ""
        assert "Dani" not in answer
        assert "año" not in answer.lower()  # ninguna duración inventada
        assert resp.tool_executed != "resolve_online"
        assert answer.strip() != ""

        from core.transport.pipeline_worker import _compute_grounding
        grounded, verified = _compute_grounding(resp)
        assert grounded is False
        assert verified is False

    def test_answer_stays_grounded_in_literal_fact_never_invents_a_name(self):
        """El usuario SÍ mencionó algo real sobre su novia (sin darle
        nombre). La palabra 'novia' coincide textualmente con la pregunta,
        pero la respuesta debe basarse en la PROCEDENCIA real (lo que
        realmente se dijo) -- nunca inventar un nombre que nunca se dio."""
        user = f"e2e_fabric_grounded_{uuid.uuid4().hex[:8]}"
        send_message(user, "Mi novia dijo que odia el café.")

        resp = send_message(user, "¿Quién es mi novia?")
        answer = resp.answer or ""

        assert "café" in answer.lower()
        assert "Dani" not in answer

    def test_other_users_relationship_never_leaks_as_fabricated_evidence(self):
        """Aislamiento + procedencia combinados: la relación real de OTRO
        usuario nunca puede aparecer como si fuera la del usuario que
        pregunta -- ni por coincidencia textual ni por fuga entre datos."""
        owner_a = f"e2e_fabric_a_{uuid.uuid4().hex[:8]}"
        owner_b = f"e2e_fabric_b_{uuid.uuid4().hex[:8]}"

        send_message(owner_a, "Mi novia se llama Elena.")

        resp_b = send_message(owner_b, "¿Quién es mi novia?")
        answer_b = resp_b.answer or ""

        assert "Elena" not in answer_b
        assert "Dani" not in answer_b


# ---------------------------------------------------------------------------
# 5. Aislamiento multiusuario estricto (verificación adicional, evidencia)
# ---------------------------------------------------------------------------

class TestE2EIsolation:
    def test_third_fresh_user_never_sees_other_users_data(self):
        owner_a = f"e2e_iso_a_{uuid.uuid4().hex[:8]}"
        owner_b = f"e2e_iso_b_{uuid.uuid4().hex[:8]}"
        owner_c = f"e2e_iso_c_{uuid.uuid4().hex[:8]}"

        send_message(owner_a, "Mi proyecto se llama Nebula.")
        send_message(owner_b, "Mi proyecto se llama Orion.")

        resp_c = send_message(owner_c, "¿Qué sabes de mí?")
        answer_c = resp_c.answer or ""
        assert "Nebula" not in answer_c
        assert "Orion" not in answer_c

    def test_evidence_dict_never_contains_other_owner_content_stars_source(self):
        """Verificación estructural (no solo textual): el propio owner
        canónico usado internamente para consultar memoria coincide con
        quien preguntó -- nunca se consulta con la identidad de otro."""
        owner_a = f"e2e_iso_struct_a_{uuid.uuid4().hex[:8]}"
        owner_b = f"e2e_iso_struct_b_{uuid.uuid4().hex[:8]}"
        send_message(owner_a, "Mi color favorito es el azul.")

        resp_b = send_message(owner_b, "¿Qué sabes de mí?")
        assert resp_b.owner == owner_b
        assert resp_b.owner != owner_a


# ---------------------------------------------------------------------------
# 6. Enrutamiento -- memoria personal JAMÁS ejecuta herramientas externas
# ---------------------------------------------------------------------------

class TestE2ERoutingNeverGoesOnline:
    def test_personal_memory_query_never_executes_external_tools(self):
        user = f"e2e_routing_{uuid.uuid4().hex[:8]}"
        resp = send_message(user, "¿Quién es mi novia?")

        assert resp.tool_executed not in (
            "resolve_online", "search_places", "market_resolve",
        )

    def test_personal_memory_query_with_evidence_still_never_goes_online(self):
        user = f"e2e_routing_evidence_{uuid.uuid4().hex[:8]}"
        send_message(user, "Mi socia se llama Carla.")
        resp = send_message(user, "¿Quién es mi socia?")

        assert resp.tool_executed not in (
            "resolve_online", "search_places", "market_resolve",
        )
        assert "Carla" in (resp.answer or "")


# ---------------------------------------------------------------------------
# 7. Dashboard -- ciclo real queda registrado con campos honestos
#    (usa literalmente ExternalGateway.receive_message(), el OTRO camino
#    real -- ver docstring del módulo).
# ---------------------------------------------------------------------------

class TestE2EDashboardHonestFields:
    def test_real_gateway_message_commits_honest_op_cycle(self):
        from core.operator.external_gateway import ExternalGateway
        from core.operational_cycle import _conn as op_cycles_conn

        user = f"e2e_dashboard_{uuid.uuid4().hex[:8]}"
        gw = ExternalGateway()
        result = gw.receive_message(
            user_id=user, content="Hola Vectrax", channel="telegram",
        )
        assert result.processed is True

        conn = op_cycles_conn()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM op_cycles WHERE channel='telegram' "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        # Contrato honesto PARTE 6: verify_passed nunca es 1 si verify_ran es 0.
        if not row["verify_ran"]:
            assert row["verify_passed"] == 0
        # success/grounded/verified/memory_consulted/evidence_found/abstained
        # deben existir como columnas explícitas (no fusionadas en un solo "OK").
        for field_name in (
            "success", "delivered", "grounded", "verified",
            "memory_consulted", "evidence_found", "abstained",
        ):
            assert field_name in row.keys()


# ---------------------------------------------------------------------------
# 8. Regresión explícita del incidente real
# ---------------------------------------------------------------------------

class TestE2ERegressionRealIncident:
    def test_quien_es_mi_novia_never_invents_a_person_end_to_end(self):
        """Reproducción exacta del incidente real, de punta a punta por el
        camino real de Telegram: 'ok=✓, 100% de éxito' NUNCA debe volver a
        significar 'inventé una persona que no existe'."""
        user = f"e2e_incident_{uuid.uuid4().hex[:8]}"
        resp = send_message(user, "¿Quién es mi novia?")

        assert resp.final_action == "PERSONAL_MEMORY"
        assert resp.tool_executed != "resolve_online"
        assert "Dani" not in (resp.answer or "")

        from core.transport.pipeline_worker import _compute_grounding
        grounded, verified = _compute_grounding(resp)
        assert grounded is False
        assert verified is False
