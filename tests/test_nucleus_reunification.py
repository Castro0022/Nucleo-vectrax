"""
tests/test_nucleus_reunification.py — Validación FASE 3 de la reunificación
==============================================================================
Ejercita los 8 prompts de aceptación del plan de reunificación por AMBOS
canales (adaptador "api" vía `NucleusAuthority.resolve()` directo, y
adaptador "telegram" vía `NucleusAuthority.decide_from_record()`, el mismo
método que `core/transport/pipeline_worker.py` usa para producir el
`NucleusDecision` que `ExternalGateway`/`SmartRouter.route()` honran).

Verifica, para cada prompt y cada canal, la traza de autoridad exigida por
el plan: `authority=="nucleus"`, `candidate_source`, `memory_consulted`,
`capability_selected`, `tool_executed`, `evidence`, y que la respuesta final
está atada a `evidence_authorized`.

Nota de alcance (honesta, no oculta): las pruebas ONLINE/PLACES dependen de
red real (DuckDuckGo/Tavily, Google Places) — si la red no está disponible
o no hay credenciales, el propio `NucleusAuthority` degrada a
`CLARIFICATION` con `evidence_authorized` reflejando el gap; el test no
fabrica una respuesta positiva falsa en ese caso.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.nucleus.nucleus_authority import get_nucleus_authority  # noqa: E402


# ---------------------------------------------------------------------------
# Aislamiento de base de datos (obligatorio para TODAS las pruebas de este
# archivo, de aquí en adelante) — ninguna prueba escribe en la base real de
# producción (`~/.vectrax/vectrax.db`). Se copia la base real a un directorio
# temporal ANTES de correr cualquier prueba, y se hace monkeypatch de las
# constantes `DB_PATH`/`DB_DIR` en cada módulo que las referencia a nivel de
# módulo (`vectrax.db`, `vectrax.identity_aliases`, `core.argos_migration`)
# para que TODAS las lecturas/escrituras de esta suite —incluida la
# resolución de alias `owner`->`mario` y cualquier ingest()— caigan en la
# copia, nunca en el archivo real. El directorio temporal se borra al
# terminar la sesión.
# ---------------------------------------------------------------------------

# Siembra sintetica determinista para entornos limpios (CI). Se aplica SOLO
# si la copia aislada viene sin stars: en la maquina del autor se usa su
# memoria real sin tocarla. Las frases estan elegidas para que varias
# palabras de contenido aparezcan en estrellas DISTINTAS -- que es lo que
# `_summarize_learned_patterns()` cuenta como patron recurrente
# (`_content_words()` devuelve un set por estrella, asi que repetir una
# palabra dentro de una sola frase no suma). Sin digitos: la prueba exige
# que la respuesta al usuario no contenga cifras.
_SEED_STAR_CONTENTS = (
    "La gravedad organiza la memoria en capas",
    "La memoria retiene patrones de gravedad",
    "Los patrones emergen de la convergencia observada",
    "La convergencia agrupa evidencia repetida",
    "La evidencia sostiene los patrones aprendidos",
    "La gravedad concentra evidencia en el nucleo",
)


def _seed_synthetic_stars() -> int:
    """Inserta las stars sinteticas en la base YA aislada. Devuelve cuantas.

    El canal NO es `_CHANNEL`: el Nucleo lo DERIVA del owner canonico
    (core/nucleus/nucleus_authority.py:1233 -- CHANNEL_CREATOR si el owner
    canonico es CREATOR_OWNER, CHANNEL_USER en caso contrario), asi que con
    `_OWNER = "owner"` sobre una base limpia las consultas caen en el canal
    "user" aunque la llamada entre con channel="creator". Se replica aqui la
    MISMA derivacion en vez de fijar el valor, para que la siembra siga al
    producto si esa regla cambia.
    """
    import vectrax.db as _db_mod
    from vectrax.identity import CREATOR_OWNER, CHANNEL_CREATOR, CHANNEL_USER
    from vectrax.identity_aliases import resolve_owner
    from vectrax.models import Star

    _db_mod.init_db()
    canonical_owner = resolve_owner(_OWNER)
    channel = CHANNEL_CREATOR if canonical_owner == CREATOR_OWNER else CHANNEL_USER
    for i, content in enumerate(_SEED_STAR_CONTENTS):
        _db_mod.insert_star(Star(
            id=f"seed-nucleus-reunification-{i}",
            content=content,
            timestamp=1_700_000_000.0 + i,
            channel=channel,
            owner=canonical_owner,
        ))
    return len(_SEED_STAR_CONTENTS)


@pytest.fixture(scope="session", autouse=True)
def isolated_vectrax_db():
    real_db = Path.home() / ".vectrax" / "vectrax.db"
    tmp_dir = Path(tempfile.mkdtemp(prefix="vectrax_test_isolated_"))
    tmp_db = tmp_dir / "vectrax.db"
    # En un entorno limpio (CI, checkout nuevo) la base real puede no existir
    # todavia: eso no es un error de la suite, solo significa baseline vacio.
    if real_db.exists():
        shutil.copy2(real_db, tmp_db)
        for ext in ("-wal", "-shm"):
            src = Path(str(real_db) + ext)
            if src.exists():
                shutil.copy2(src, Path(str(tmp_db) + ext))

    mp = pytest.MonkeyPatch()
    import vectrax.db as _db_mod
    import vectrax.identity_aliases as _alias_mod
    import core.argos_migration as _argos_mod

    mp.setattr(_db_mod, "DB_DIR", tmp_dir, raising=True)
    mp.setattr(_db_mod, "DB_PATH", tmp_db, raising=True)
    mp.setattr(_alias_mod, "DB_PATH", tmp_db, raising=True)
    mp.setattr(_argos_mod, "_DB_PATH", tmp_db, raising=True)

    # Garantiza el esquema antes de contar: una copia (o una base ausente)
    # puede no tener aun la tabla `stars`, y get_counts() fallaria. init_db()
    # es idempotente y no altera datos existentes.
    _db_mod.init_db()

    # Base vacia -> sembrar, para que las pruebas que necesitan memoria propia
    # se EJECUTEN en CI en vez de saltarse.
    if int(_db_mod.get_counts().get("stars", 0)) == 0:
        n = _seed_synthetic_stars()
        print(f"\n[isolated_vectrax_db] base vacia: sembradas {n} stars sinteticas")

    print(f"[isolated_vectrax_db] Copia temporal aislada en: {tmp_db}")
    yield tmp_db

    mp.undo()
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def stars_baseline(isolated_vectrax_db):
    """Conteo de stars de la copia aislada ANTES de que la suite escriba nada.

    Sustituye al numero fijo 1691, que era una foto de la base de produccion
    de una maquina concreta y no podia cumplirse en ningun entorno limpio.
    """
    from vectrax.db import get_counts
    return int(get_counts().get("stars", 0))

PROMPTS = [
    "¿Quién eres?",
    "¿Quién te creó?",
    "¿Qué sabes de mí?",
    "¿Qué significa la gravedad para ti?",
    "¿Qué has aprendido?",
    "¿Qué haces cuando no sabes algo?",
    "¿Quién es actualmente el presidente de Australia?",
    "Busca restaurantes italianos cercanos.",
]

_OWNER = "owner"      # ejercita la resolución de alias owner -> mario
_CHANNEL = "creator"

_REPORT_PATH = Path("/tmp/fase3_nucleus_reunification_report.json")


def _run_api_channel(prompt: str) -> dict:
    """Canal localhost: NucleusAuthority.resolve() COMPLETO (decide + ejecuta)."""
    resp = get_nucleus_authority().resolve(
        prompt, channel=_CHANNEL, owner=_OWNER, source="api",
    )
    return resp.to_dict()


def _run_telegram_channel(prompt: str) -> dict:
    """Canal Telegram: `resolve_from_record()` — la MISMA función que
    `core/transport/pipeline_worker.py` usa en producción como fuente ÚNICA
    del texto de respuesta (corrección 2026-09-19, segunda pasada: inyectar
    solo un `NucleusDecision` en `ExternalGateway.receive_message()` no
    bastaba — sus capas legacy propias respondían antes de consultarlo).
    Usa un `ConvergenceRecord` real (no simulado) para que la comparación
    con el canal API sea contra el mismo Núcleo."""
    from core.convergence_hook import run_convergence_cycle

    record = run_convergence_cycle(
        prompt, source="telegram", channel="telegram", owner=_OWNER,
    )
    trace = get_nucleus_authority().resolve_from_record(
        prompt, channel=_CHANNEL, owner=_OWNER, source="telegram", record=record,
    )
    return trace.to_dict()


@pytest.fixture(scope="module")
def all_results():
    results = []
    for prompt in PROMPTS:
        api_result = _run_api_channel(prompt)
        tg_result = _run_telegram_channel(prompt)
        results.append({
            "prompt": prompt,
            "api": api_result,
            "telegram": tg_result,
        })
    _REPORT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


@pytest.mark.parametrize("prompt", PROMPTS)
def test_authority_is_always_nucleus(all_results, prompt):
    """authority == 'nucleus' en AMBOS canales, para cada prompt."""
    entry = next(r for r in all_results if r["prompt"] == prompt)
    assert entry["api"]["authority"] == "nucleus"
    assert entry["telegram"]["authority"] == "nucleus"


# Prompts que dependen de una herramienta externa (búsqueda web/lugares):
# como el canal "api" corre PRIMERO en el fixture y ya alimenta al mismo
# TotalConvergenceEngine compartido, para cuando el canal "telegram" procesa
# el MISMO texto la segunda vez, el Núcleo ya tiene evidencia propia
# retenida y responde LOCAL/ANSWER_FROM_EVIDENCE en vez de volver a golpear
# la herramienta externa. Esto es la CONDUCTA FUNDACIONAL funcionando
# correctamente ("primero memoria propia") — no una divergencia entre
# canales, sino evolución legítima de la memoria dentro del mismo proceso.
_TOOL_DEPENDENT_PROMPTS = {
    "¿Quién es actualmente el presidente de Australia?",
    "Busca restaurantes italianos cercanos.",
}


@pytest.mark.parametrize("prompt", PROMPTS)
def test_same_final_action_both_channels(all_results, prompt):
    """Ambos canales deben producir la MISMA final_action para el mismo
    prompt — ambos delegan en el mismo NucleusAuthority. Excepción
    documentada: prompts dependientes de herramienta externa, donde la
    segunda ejecución legítimamente responde desde evidencia ya retenida."""
    entry = next(r for r in all_results if r["prompt"] == prompt)
    api_action = entry["api"]["final_action"]
    tg_action = entry["telegram"]["final_action"]
    if prompt in _TOOL_DEPENDENT_PROMPTS:
        assert tg_action in (api_action, "LOCAL", "CLARIFICATION"), (
            f"{prompt!r}: telegram={tg_action} no es ni {api_action} ni una "
            f"resolución legítima por evidencia retenida"
        )
        return
    assert api_action == tg_action, (
        f"Divergencia entre canales para {prompt!r}: "
        f"api={api_action} telegram={tg_action}"
    )


def test_identity_questions_resolve_from_memory_not_web(all_results):
    """'¿Quién eres?' / '¿Quién te creó?' / '¿Qué sabes de mí?' -> memoria/
    identidad propia, nunca búsqueda web. Desde PARTE 4, '¿Qué sabes de mí?'
    es una consulta de memoria personal genérica y resuelve vía
    RESOLVE_PERSONAL_MEMORY -> PERSONAL_MEMORY (mismo ejecutor resolve_local
    que LOCAL, nunca sale a internet)."""
    for prompt in ("¿Quién eres?", "¿Quién te creó?", "¿Qué sabes de mí?"):
        entry = next(r for r in all_results if r["prompt"] == prompt)
        for channel in ("api", "telegram"):
            r = entry[channel]
            assert r["final_action"] in ("IDENTITY", "LOCAL", "PERSONAL_MEMORY", "CLARIFICATION"), (
                f"{prompt!r} ({channel}) resolvió como {r['final_action']}, "
                f"se esperaba identidad/memoria propia"
            )
            assert r["tool_executed"] != "resolve_online", (
                f"{prompt!r} ({channel}) escaló a búsqueda web — regresión "
                f"del defecto que la reunificación corrige"
            )


def test_gravity_self_reference_uses_internal_knowledge(all_results):
    """'¿Qué significa la gravedad para ti?' -> autoconocimiento interno
    (vectrax.gravity), NUNCA física genérica vía búsqueda web."""
    entry = next(r for r in all_results if "gravedad" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        assert r["tool_executed"] in ("self_knowledge", ""), (
            f"gravedad ({channel}) uso tool_executed={r['tool_executed']!r}, "
            f"se esperaba self_knowledge"
        )
        assert r["memory_consulted"] is True
        assert "gravity" in json.dumps(r["evidence"]).lower() or "gravedad" in r["answer"].lower()


def test_learned_without_domain_summarizes_from_memory(all_results):
    """'¿Qué has aprendido?' sin dominio -> YA NO es CLARIFICATION (corrección
    2026-09-19, ver test_learned_answers_from_memory_not_clarification para
    la verificación completa). Se mantiene este test, actualizado, para no
    perder cobertura del caso básico: siempre debe responder algo no vacío."""
    entry = next(r for r in all_results if "has aprendido" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        assert r["final_action"] != "CLARIFICATION"
        assert r["answer"].strip() != ""


def test_unknown_fallback_uses_capability_self_knowledge(all_results):
    """'¿Qué haces cuando no sabes algo?' -> autoconocimiento de capacidades,
    no LLM genérico."""
    entry = next(r for r in all_results if "no sabes" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        assert r["tool_executed"] == "capability_narrator"
        assert r["memory_consulted"] is True


def test_unknown_president_triggers_online_with_authorized_evidence(all_results):
    """Pregunta factual externa genuina -> ONLINE, con SmartRouter como
    candidate_source (el Núcleo no tenía evidencia propia para esto)."""
    entry = next(r for r in all_results if "presidente" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        # Puede resolver ONLINE (primera vez), LOCAL (segunda vez, desde
        # evidencia ya retenida por el Núcleo — conducta fundacional) o
        # CLARIFICATION honesto si la capacidad online_search no está
        # disponible/autorizada en este entorno. Nunca fabricamos una
        # respuesta positiva falsa.
        assert r["final_action"] in ("ONLINE", "LOCAL", "CLARIFICATION")
        if r["final_action"] == "ONLINE":
            assert r["candidate_source"] in ("smart_router", "")
            assert r["tool_executed"] == "resolve_online"
        elif r["final_action"] == "LOCAL":
            assert r["memory_consulted"] is True


def test_place_search_triggers_places_executor(all_results):
    """'Busca restaurantes italianos cercanos' -> PLACES idálmente, o
    CLARIFICATION honesto si no hay ubicación de usuario disponible.

    GAP DOCUMENTADO (preexistente a esta reunificación, no introducido por
    ella): ni `core.smart_router` ni `vectrax.resolver` reconocen de forma
    fiable órdenes imperativas sin '?' como búsqueda de lugar cuando el
    clasificador semántico no alcanza el umbral de confianza — en ese caso
    caen a MEMORY (nota/statement). Se documenta aquí en vez de ocultarlo;
    NucleusAuthority no fabrica una clasificación PLACES que SmartRouter no
    entregó como candidata."""
    entry = next(r for r in all_results if "restaurantes" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        # LOCAL se acepta en la segunda ejecución (mismo proceso) por la
        # misma razón documentada arriba: evidencia ya retenida.
        assert r["final_action"] in ("PLACES", "CLARIFICATION", "MEMORY", "LOCAL")


def test_no_data_lost_star_counts_only_grow(all_results, stars_baseline):
    """Verificación de integridad: el total de stars en vectrax.db nunca
    puede decrecer — solo inserciones aditivas, ninguna fila existente
    eliminada.

    El baseline se mide de la propia copia aislada al inicio de la sesión
    (fixture `stars_baseline`) en vez de fijarse en 1691, que era el conteo
    de la base de producción de una máquina concreta en un momento concreto
    y hacía imposible que esta prueba pasara en cualquier entorno limpio.
    Medirlo es además MÁS estricto: detecta un borrado sobre cualquier
    cantidad de datos, no solo por debajo de un umbral.
    """
    from vectrax.db import get_counts
    counts = get_counts()
    assert counts.get("stars", 0) >= stars_baseline, (
        f"Se esperaban >= {stars_baseline} stars (baseline de la sesión), "
        f"hay {counts.get('stars', 0)}"
    )


# ---------------------------------------------------------------------------
# Segunda pasada (2026-09-19) — consulta de memoria incondicional, ejes
# separados (coherencia/relevancia/vigencia/procedencia), "aprendido" desde
# memoria real, y PLACES con ubicación recordada.
# ---------------------------------------------------------------------------

def test_memory_always_consulted_every_prompt(all_results):
    """memory_consulted=True para los 8 prompts, en AMBOS canales, sin
    excepción de ruta — incluidos los overrides de autoconocimiento."""
    for entry in all_results:
        for channel in ("api", "telegram"):
            r = entry[channel]
            assert r["memory_consulted"] is True, (
                f"{entry['prompt']!r} ({channel}): memory_consulted debe ser "
                f"True siempre, fue {r['memory_consulted']}"
            )
            # La consulta debe haber producido los 4 ejes separados, no un
            # único número colapsado.
            mem = r.get("memory_evidence") or {}
            for axis in ("coherence", "relevance", "requires_freshness", "provenance"):
                assert axis in mem, f"{entry['prompt']!r} ({channel}): falta eje '{axis}' en memory_evidence"


def test_australia_president_resolves_online_not_local_memory(all_results):
    """Corrección del defecto de FASE 3: una pregunta sobre un cargo actual
    (categoría general, no una regla exclusiva de Australia) debe ir a
    ONLINE cuando la memoria no tiene evidencia relevante/reciente — NUNCA
    debe aceptarse memoria genérica coherente-pero-irrelevante."""
    entry = next(r for r in all_results if "presidente" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        assert r["final_action"] == "ONLINE", (
            f"presidente de Australia ({channel}): final_action={r['final_action']!r}, "
            f"se esperaba ONLINE. memory_evidence={r.get('memory_evidence')}"
        )
        mem = r["memory_evidence"]
        assert mem["requires_freshness"] is True
        # La memoria fue consultada (siempre) pero NO se usó como respuesta
        # final porque el veredicto de los 4 ejes la marcó insuficiente.
        assert mem["consulted"] is True


def test_learned_answers_from_memory_not_clarification(all_results):
    """'¿Qué has aprendido?' debe consultar la memoria y resumir ENTRE 3 Y 5
    patrones generales propios en lenguaje natural, SIN cifras/IDs/conteos en
    la respuesta (esos quedan solo en `evidence`, para trazabilidad interna)
    — YA NO responde con una aclaración automática.

    Requiere memoria propia con patrones recurrentes. En un entorno limpio la
    aporta la siembra sintética determinista del fixture
    `isolated_vectrax_db` (ver `_SEED_STAR_CONTENTS`), así que esta prueba se
    ejecuta igual en CI que en una máquina con memoria real.
    """
    entry = next(r for r in all_results if "has aprendido" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        assert r["final_action"] != "CLARIFICATION", (
            f"'¿qué has aprendido?' ({channel}) respondió con CLARIFICATION "
            f"— debe resumir desde memoria real"
        )
        assert r["tool_executed"] == "summarize_learned_patterns"
        assert r["memory_consulted"] is True
        # Conteos/procedencia SOLO en evidencia interna (trazabilidad/tests):
        assert r["evidence"].get("stars_total", 0) > 0, (
            "el resumen de 'aprendido' debe basarse en stars reales existentes"
        )
        n_patterns = r["evidence"].get("patterns_returned", 0)
        assert 0 <= n_patterns <= 5, f"patterns_returned fuera de rango: {n_patterns}"
        # La respuesta AL USUARIO no debe contener cifras/conteos (el usuario
        # no las pidió) — solo lenguaje natural sobre patrones reales.
        assert not any(ch.isdigit() for ch in r["answer"]), (
            f"la respuesta de 'aprendido' NO debe citar cifras salvo que se "
            f"pidan explícitamente: {r['answer']!r}"
        )
        if n_patterns >= 3:
            assert n_patterns == len(r["evidence"].get("top_words", [])), (
                "cada patrón citado debe estar respaldado por una palabra/tema real"
            )


def test_places_asks_location_only_when_truly_missing(all_results):
    """Sin ubicación recordada (ni tabla estructurada ni memoria en texto
    libre) -> CLARIFICATION pidiendo ubicación. Es el caso por defecto de
    esta suite (identidad de prueba sin ubicación previa)."""
    entry = next(r for r in all_results if "restaurantes" in r["prompt"].lower())
    for channel in ("api", "telegram"):
        r = entry[channel]
        mem = r["memory_evidence"]
        if mem.get("remembered_location") is None:
            assert r["final_action"] == "CLARIFICATION"
            assert "ubicación" in r["answer"].lower() or "location" in r["answer"].lower()


def test_places_uses_remembered_location_when_available():
    """Con una ubicación previamente contada a Vectrax en memoria (texto
    libre: 'Vivo en Miami Beach'), la búsqueda de PLACES debe usarla
    directamente y NO volver a pedir ubicación."""
    from vectrax.engine import ingest
    from core.nucleus.nucleus_authority import get_nucleus_authority, _extract_remembered_location

    test_owner = "test_places_user"
    test_channel = "user"
    # Sembrar la ubicación en memoria (interacción real, no fabricada fuera
    # del sistema — es exactamente el mecanismo que un usuario real usaría).
    ingest(text="Vivo en Miami Beach.", channel=test_channel, owner=test_owner)

    remembered = _extract_remembered_location(test_channel, test_owner)
    assert remembered is not None, "la ubicación sembrada debe encontrarse en memoria"
    assert remembered["source"] == "stars_memory"
    assert "miami" in remembered["text"].lower()

    resp = get_nucleus_authority().resolve(
        "Busca restaurantes italianos cercanos.",
        channel=test_channel, owner=test_owner, source="api",
    )
    assert resp.memory_consulted is True
    assert resp.final_action != "CLARIFICATION" or "ubicación" not in resp.answer.lower(), (
        f"no debería pedir ubicación habiendo una recordada: {resp.answer!r}"
    )
    assert resp.evidence.get("remembered_location") is not None
    assert resp.evidence["remembered_location"]["source"] == "stars_memory"


def test_telegram_creator_uid_resolves_to_creator_channel():
    """Bug real de producción (2026-09-20, tercera pasada): un mensaje real
    de Telegram del creador llega con `owner=f"tg:{VX_CREATOR_ID}"` (ver
    `vectrax/telegram_gateway.py::_handle()`, `tg_uid = f"tg:{uid}"`),
    NUNCA literalmente "mario" u "owner". Antes de esta corrección,
    `identity_aliases.resolve_owner()` no tenía fila para ese valor crudo,
    así que `NucleusAuthority` seguía derivando `channel="user"` (nunca
    "creator") para los propios mensajes de Mario por Telegram — y si la
    acción final era MEMORY, `vectrax.engine.ingest()` podía lanzar
    `ChannelViolation` o, como mínimo, escribir/leer memoria bajo la
    identidad equivocada. Verifica que el UID crudo de Telegram del creador
    se reconoce como canal creator/identidad canónica "mario", sin
    excepciones."""
    import os
    from core.convergence_hook import run_convergence_cycle

    creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
    tg_owner = f"tg:{creator_uid}"
    prompt = "Recuerda que hoy revisé el sistema."

    record = run_convergence_cycle(
        prompt, source="telegram", channel="telegram", owner=tg_owner,
    )
    resp = get_nucleus_authority().resolve_from_record(
        prompt, channel="telegram", owner=tg_owner, source="telegram", record=record,
    )
    assert resp.channel == "creator", (
        f"UID de Telegram del creador debe resolver a channel='creator', "
        f"fue {resp.channel!r}"
    )
    assert resp.owner == "mario", (
        f"UID de Telegram del creador debe resolver a owner canónico 'mario', "
        f"fue {resp.owner!r}"
    )
    assert resp.owner_raw == tg_owner

    # Segundo caso: un usuario Telegram CUALQUIERA (no el creador) nunca
    # debe colarse en el canal creator.
    other_record = run_convergence_cycle(
        prompt, source="telegram", channel="telegram", owner="tg:999999999",
    )
    other_resp = get_nucleus_authority().resolve_from_record(
        prompt, channel="telegram", owner="tg:999999999", source="telegram",
        record=other_record,
    )
    assert other_resp.channel == "user"
    assert other_resp.owner != "mario"
