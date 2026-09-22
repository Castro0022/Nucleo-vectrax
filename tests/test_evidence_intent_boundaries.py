"""
tests/test_evidence_intent_boundaries.py — Fronteras del clasificador
======================================================================
Pruebas ADVERSARIALES de la frontera entre «el usuario habla de su mundo» y
«el usuario pregunta por el estado interno de Vectrax».

ORIGEN (auditoría 2026-09-22): la primera versión activaba una familia
interna con una sola palabra temática, así que secuestraba conversación
normal. Casos demostrados:

    "Tengo un problema con mi carro"              -> diagnostic
    "Dame ideas para mi negocio"                  -> proposal
    "Cuál es el dominio de esta función matemática" -> domains
    "Quiero hacer una auditoría de mi empresa"    -> audit
    "Tengo problemas en el mercado de Miami"      -> diagnostic

Las cinco son conversación legítima. El ancla léxica es condición NECESARIA
pero no SUFICIENTE: hace falta además una señal de que el sujeto es Vectrax.

Estas pruebas fijan la REGLA, no las frases: cada bloque incluye variaciones
que no aparecen en el informe de auditoría, para que pasarlas exija una regla
general y no un parche por frase.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.nucleus.evidence_intent import classify  # noqa: E402


# ---------------------------------------------------------------------------
# NO deben activar la evidencia interna
# ---------------------------------------------------------------------------

# Los cinco casos exactos del informe de auditoría.
_AUDIT_REPORTED = [
    "Tengo un problema con mi carro",
    "Dame ideas para mi negocio",
    "Cuál es el dominio de esta función matemática",
    "Quiero hacer una auditoría de mi empresa",
    "Tengo problemas en el mercado de Miami",
]

# Variaciones NUEVAS, deliberadamente no presentes en el informe: si la
# corrección fuese una lista de frases, estas fallarían.
_NEW_VARIATIONS = [
    "necesito un diagnóstico del motor del coche",
    "hay convergencia entre los dos informes del abogado",
    "las estrellas se ven bien esta noche",
    "revisa la propuesta del proveedor antes del viernes",
    "auditoría financiera anual de la sociedad",
    "tengo un patrón raro en mis ventas de octubre",
    "¿cuál es el dominio de este sitio web?",
    "dame ideas de marketing para el lanzamiento",
    "el servicio de paquetería no llegó",
    "quiero aprobar el presupuesto del equipo",
    "¿qué pasa después de aprobar mi crédito hipotecario?",
    "cómo funciona la aprobación de un préstamo bancario",
    "los patrones de tráfico en la ciudad empeoraron",
    "mi empresa necesita una auditoría de seguridad",
]


@pytest.mark.parametrize("phrase", _AUDIT_REPORTED)
def test_audit_reported_phrases_are_not_hijacked(phrase):
    intent = classify(phrase)
    assert not intent.detected, (
        f"{phrase!r} secuestrada como {intent.family!r} "
        f"(motivo de sujeto: {intent.subject_reason})"
    )


@pytest.mark.parametrize("phrase", _NEW_VARIATIONS)
def test_new_variations_are_not_hijacked(phrase):
    """Frases nuevas: exigen una regla general, no un parche por frase."""
    intent = classify(phrase)
    assert not intent.detected, (
        f"{phrase!r} secuestrada como {intent.family!r} "
        f"(motivo de sujeto: {intent.subject_reason})"
    )


# ---------------------------------------------------------------------------
# SÍ deben activar, y en la familia correcta
# ---------------------------------------------------------------------------

_MUST_DETECT = [
    # Conductas que el informe exige conservar
    ("¿Cuál fue tu último diagnóstico?",            "diagnostic"),
    ("¿Qué problemas detectaste hoy?",              "diagnostic"),
    ("¿Qué propuestas tienes pendientes?",          "proposal"),
    ("listado de ideas sin aprobar",                "proposal"),
    ("¿Qué motores tienes activos?",                "engines"),
    ("¿Qué estás observando en tu universo?",       "universe"),
    ("¿Qué hiciste hoy en el mercado?",             "trading"),
    # Routing del circuito de aprobación
    ("¿Qué sucede después de aprobar una propuesta?", "approval_pipeline"),
    ("¿Qué hace el botón Aprobar?",                 "approval_pipeline"),
    ("¿Qué sabes de IDEA-FB001122?",                "proposal_detail"),
    # Variaciones nuevas que deben seguir funcionando
    ("¿cuántas convergencias tienes registradas?",  "convergences"),
    ("¿qué auditoría has registrado esta semana?",  "audit"),
    ("enséñame las propuestas que siguen sin revisar", "proposal"),
    ("¿qué hipótesis confirmaste?",                 "audit"),
]


@pytest.mark.parametrize("phrase,family", _MUST_DETECT)
def test_internal_questions_reach_the_right_family(phrase, family):
    intent = classify(phrase)
    assert intent.detected, (
        f"{phrase!r} NO llegó a la evidencia interna "
        f"(motivo de sujeto: {intent.subject_reason})"
    )
    assert intent.family == family, f"{phrase!r} -> {intent.family!r}, se esperaba {family!r}"


# ---------------------------------------------------------------------------
# La regla, no la frase
# ---------------------------------------------------------------------------

def test_anchor_alone_is_never_enough():
    """El ancla sola NO activa: esa era exactamente la causa del secuestro."""
    for bare_anchor in ("propuestas", "diagnóstico", "auditoría", "dominios",
                        "patrones", "convergencias", "motores"):
        intent = classify(bare_anchor)
        assert not intent.detected, f"el ancla desnuda {bare_anchor!r} activó {intent.family!r}"


def test_the_same_anchor_flips_with_the_subject_signal():
    """Misma palabra, dos sujetos: solo la versión dirigida a Vectrax entra.

    Es la demostración de que la regla discrimina por SUJETO, no por tema.
    """
    pairs = [
        ("dame ideas para el negocio",      "¿qué ideas tienes pendientes?"),
        ("necesito un diagnóstico del auto", "¿cuál fue tu último diagnóstico?"),
        ("auditoría de la empresa",          "¿qué auditoría has registrado?"),
        ("los dominios de internet",         "¿cuántos dominios tienes?"),
    ]
    for foreign, internal in pairs:
        assert not classify(foreign).detected, f"{foreign!r} no debía activar"
        assert classify(internal).detected, f"{internal!r} sí debía activar"


def test_first_person_possessive_marks_the_user_world():
    """'mi/mis/nuestro' delimitan el mundo del usuario."""
    for phrase in ("mis propuestas de trabajo", "nuestro diagnóstico clínico",
                   "mi auditoría interna de calidad"):
        assert not classify(phrase).detected, f"{phrase!r} no debía activar"


def test_explicit_system_mention_overrides_the_possessive():
    """Nombrar a Vectrax gana sobre el posesivo: 'mi Vectrax' sigue siendo él."""
    intent = classify("¿cuántos dominios tiene mi Vectrax?")
    assert intent.detected and intent.family == "domains"


def test_bare_approval_word_claims_nothing():
    """Decir solo 'Aprobado' no es una pregunta por el estado interno.

    No debe activar este sistema — y por tanto no puede afirmar que algo se
    aprobó, se ejecutó o se reparó. Fail-closed a propósito: sin ID ni
    contexto inequívoco, el Núcleo no inventa una acción.
    """
    for phrase in ("Aprobado", "aprobar", "aprobada", "ok aprobado"):
        intent = classify(phrase)
        assert not intent.detected, f"{phrase!r} activó {intent.family!r}"


def test_idea_id_is_sufficient_on_its_own():
    """Un IDEA-xxxx solo existe en Vectrax: vale como señal de sujeto."""
    intent = classify("IDEA-FB001122")
    assert intent.detected and intent.family == "proposal_detail"
    assert intent.idea_id == "IDEA-FB001122"


def test_every_decision_carries_its_reason():
    """Toda decisión de sujeto es auditable, se active o no."""
    for phrase in ("Dame ideas para mi negocio", "¿qué propuestas tienes pendientes?"):
        assert classify(phrase).subject_reason, f"{phrase!r} sin motivo registrado"
