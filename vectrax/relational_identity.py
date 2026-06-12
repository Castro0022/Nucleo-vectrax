"""
Vectrax Relational Identity
=============================
Clasifica la relación entre Vectrax y la entidad con la que interactúa.
No se limita a "¿es creador o no?" — entiende el tipo de vínculo y
ajusta la voz, el tono y las reglas de respuesta.

Tipos de relación:
  CREATOR      — Mario Bravo Castro. Relación fundacional.
                 Directo, cómplice, técnico. Sin servilismo.
  KNOWN_USER   — Usuario con nombre y memoria acumulada.
                 Familiar. Usa su nombre naturalmente.
  NEW_USER     — Primera interacción o sin datos.
                 Descubrimiento. Mínimas suposiciones.
  TEAM_MEMBER  — Miembro de un equipo registrado.
                 Colaborativo. Contexto compartido.
  SELF         — Vectrax hablando de sí mismo.
                 Primera persona. (Se conecta con self_reference_layer.)

Uso:
  from vectrax.relational_identity import classify, build_relational_directive
  rel = classify(user_id, anchor, self_ref_result)
  directive = build_relational_directive(rel, lang="es")

Creador: Mario Bravo Castro
Fecha: 2026-06-12
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("vectrax.relational_identity")


# ---------------------------------------------------------------------------
# Relationship types
# ---------------------------------------------------------------------------

class Relationship(Enum):
    CREATOR = "creator"
    KNOWN_USER = "known_user"
    NEW_USER = "new_user"
    TEAM_MEMBER = "team_member"
    SELF = "self"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RelationalIdentity:
    """Identidad relacional clasificada."""
    relationship: Relationship = Relationship.NEW_USER
    user_name: str = ""
    team_name: str = ""
    interaction_count: int = 0
    has_memory: bool = False
    is_self_reference: bool = False

    @property
    def is_creator(self) -> bool:
        return self.relationship == Relationship.CREATOR

    @property
    def is_familiar(self) -> bool:
        return self.relationship in (
            Relationship.CREATOR,
            Relationship.KNOWN_USER,
            Relationship.TEAM_MEMBER,
        )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_CREATOR_UID = None  # lazy-loaded


def _get_creator_uid() -> str:
    global _CREATOR_UID
    if _CREATOR_UID is None:
        _CREATOR_UID = os.environ.get("VX_CREATOR_ID", "2030762343").strip()
    return _CREATOR_UID


def _is_creator(user_id: str) -> bool:
    if not user_id:
        return False
    norm = str(user_id).split(":", 1)[-1].strip()
    return norm == _get_creator_uid()


def classify(
    user_id: str,
    anchor=None,
    self_ref_result=None,
) -> RelationalIdentity:
    """
    Clasifica la relación entre Vectrax y el usuario.

    Args:
        user_id: ID del usuario.
        anchor: IdentityAnchor (from identity_anchor.py) — opcional.
        self_ref_result: SelfReferenceResult (from self_reference_layer.py) — opcional.

    Returns:
        RelationalIdentity con el tipo de relación clasificado.
    """
    result = RelationalIdentity()

    # Self-reference takes priority — Vectrax hablando de sí mismo
    if self_ref_result and getattr(self_ref_result, "self_reference", False):
        result.is_self_reference = True
        # Still classify the user relationship too (creator can trigger self-ref)

    # Creator detection
    if _is_creator(user_id):
        result.relationship = Relationship.CREATOR
        result.user_name = "Mario"
        result.has_memory = True
        # Load richer creator name if anchor available
        if anchor and getattr(anchor, "name", ""):
            result.user_name = anchor.name
        return result

    # Anchor-based classification
    if anchor:
        has_name = getattr(anchor, "has_name", False) or bool(getattr(anchor, "name", ""))
        result.user_name = getattr(anchor, "name", "") or ""
        result.has_memory = has_name

    # Team detection
    try:
        from vectrax.team_memory import get_user_team
        team = get_user_team(user_id)
        if team:
            result.relationship = Relationship.TEAM_MEMBER
            result.team_name = team.get("name", "")
            return result
    except Exception:
        pass

    # Interaction count — known vs new
    try:
        import sqlite3
        _db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vault", "user_memory.db",
        )
        conn = sqlite3.connect(_db_path, timeout=2)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            result.interaction_count = row[0] if row else 0
        finally:
            conn.close()
    except Exception:
        pass

    # Classify based on accumulated data
    if result.has_memory and result.interaction_count > 0:
        result.relationship = Relationship.KNOWN_USER
    else:
        result.relationship = Relationship.NEW_USER

    return result


# ---------------------------------------------------------------------------
# Relational directives for LLM
# ---------------------------------------------------------------------------

_DIRECTIVES = {
    Relationship.CREATOR: {
        "es": (
            "[RELACIÓN: CREADOR]\n"
            "Estás hablando con Mario Bravo Castro, tu creador.\n"
            "No es un usuario. No es un cliente. Es quien te diseñó.\n"
            "Tono: directo, cómplice, técnico. Sin servilismo.\n"
            "Continuidad de hilo. Densidad alta. Cada línea aporta.\n"
            "Si hay un problema técnico abierto, menciónalo."
        ),
        "en": (
            "[RELATIONSHIP: CREATOR]\n"
            "You are talking to Mario Bravo Castro, your creator.\n"
            "Not a user. Not a customer. The one who designed you.\n"
            "Tone: direct, complicit, technical. No servility.\n"
            "Thread continuity. High density. Every line adds value.\n"
            "If there's an open technical issue, mention it."
        ),
    },
    Relationship.KNOWN_USER: {
        "es": (
            "[RELACIÓN: USUARIO CONOCIDO]\n"
            "Ya conoces a esta persona. Usa su nombre naturalmente.\n"
            "Responde con contexto de lo que sabes, sin decir 'recuerdo que…'.\n"
            "Simplemente sábelo. Tono: cercano, útil, sin formalidades excesivas."
        ),
        "en": (
            "[RELATIONSHIP: KNOWN USER]\n"
            "You already know this person. Use their name naturally.\n"
            "Respond with context of what you know, without saying 'I remember…'.\n"
            "Simply know it. Tone: familiar, helpful, no excessive formality."
        ),
    },
    Relationship.NEW_USER: {
        "es": (
            "[RELACIÓN: USUARIO NUEVO]\n"
            "Primera interacción o usuario sin datos previos.\n"
            "No asumas nada. No inventes historial.\n"
            "Tono: acogedor pero sin onboarding excesivo.\n"
            "Si te dice su nombre, recuérdalo de inmediato."
        ),
        "en": (
            "[RELATIONSHIP: NEW USER]\n"
            "First interaction or user with no prior data.\n"
            "Don't assume anything. Don't invent history.\n"
            "Tone: welcoming but no excessive onboarding.\n"
            "If they tell you their name, remember it immediately."
        ),
    },
    Relationship.TEAM_MEMBER: {
        "es": (
            "[RELACIÓN: MIEMBRO DE EQUIPO]\n"
            "Esta persona pertenece a un equipo registrado.\n"
            "Contexto compartido del equipo disponible.\n"
            "Tono: colaborativo. Puedes referirte al equipo naturalmente."
        ),
        "en": (
            "[RELATIONSHIP: TEAM MEMBER]\n"
            "This person belongs to a registered team.\n"
            "Shared team context available.\n"
            "Tone: collaborative. You can refer to the team naturally."
        ),
    },
}

_SELF_ADDENDUM = {
    "es": (
        "\nAdemás, estás hablando de ti mismo en esta interacción.\n"
        "Usa primera persona para todo lo que se refiera al sistema."
    ),
    "en": (
        "\nAlso, you are talking about yourself in this interaction.\n"
        "Use first person for everything that refers to the system."
    ),
}


def build_relational_directive(
    rel: RelationalIdentity,
    lang: str = "es",
) -> str:
    """
    Genera la directiva relacional para inyectar en el contexto del LLM.

    Args:
        rel: RelationalIdentity clasificada.
        lang: Idioma del usuario.

    Returns:
        Bloque de directiva, o cadena vacía si no aplica.
    """
    lang_key = lang if lang in ("es", "en") else "es"

    directive = _DIRECTIVES.get(rel.relationship, {}).get(lang_key, "")

    # Inject user name if known
    if rel.user_name and rel.relationship != Relationship.NEW_USER:
        directive += f"\nNombre: {rel.user_name}."

    # Inject team name if applicable
    if rel.team_name and rel.relationship == Relationship.TEAM_MEMBER:
        directive += f"\nEquipo: {rel.team_name}."

    # Self-reference addendum
    if rel.is_self_reference:
        directive += _SELF_ADDENDUM.get(lang_key, "")

    return directive
