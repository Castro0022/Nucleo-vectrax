"""
core/identity_context.py — Contexto de identidad persistente.

Un objeto que nace al inicio del pipeline y viaja con el mensaje
a través de todas las capas. Contiene todo lo que Vectrax sabe
sobre quién está hablando y cómo debe responder.

Se construye UNA VEZ por mensaje. No se persiste — es efímero
por diseño. La memoria gravitacional almacena el resultado,
no el contexto.

API pública:
    build_identity_context(user_id, content) -> IdentityContext
    IdentityContext.to_prompt_block() -> str (for LLM injection)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("vectrax.identity_context")


@dataclass
class IdentityContext:
    """Context object that travels through the entire pipeline."""
    user_id: str
    user_name: str = ""
    user_lang: str = "es"
    mode: str = "conversacional"
    is_creator: bool = False
    memory_summary: str = ""
    tone: str = ""
    relationship: str = ""       # relational identity type (creator/known_user/new_user/team_member)
    relational_directive: str = ""  # LLM directive block from relational_identity
    timestamp: float = field(default_factory=time.time)

    def to_prompt_block(self) -> str:
        """Generate a context block to inject into LLM prompts.

        This block tells the LLM WHO is talking, WHAT mode to use,
        and HOW to respond — before the LLM sees the user's message.
        """
        # If we have a relational directive, use it as the primary
        # identity block — it's richer than the generic format.
        if self.relational_directive:
            block = self.relational_directive
            if self.tone:
                block += f"\nModo: {self.mode} | Tono: {self.tone}"
            if self.memory_summary:
                block += f"\nContexto de memoria: {self.memory_summary[:200]}"
            return block

        # Fallback: generic format (if relational_identity unavailable)
        lines = ["[IDENTIDAD ACTIVA]"]

        if self.user_name:
            lines.append(f"Usuario: {self.user_name}")
        if self.is_creator:
            lines.append("Relación: CREADOR del sistema")
        lines.append(f"Modo: {self.mode}")

        if self.tone:
            lines.append(f"Tono: {self.tone}")

        if self.memory_summary:
            lines.append(f"Contexto de memoria: {self.memory_summary[:200]}")

        return "\n".join(lines)


def build_identity_context(user_id: str, content: str) -> IdentityContext:
    """Build the identity context for a message.

    Called ONCE at the start of the pipeline. Gathers:
    - User name from memory
    - Language detection
    - Mode detection
    - Creator status
    - Brief memory summary
    """
    ctx = IdentityContext(user_id=user_id)

    # Creator detection
    creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
    ctx.is_creator = user_id.replace("tg:", "") == creator_uid

    # User name from memory
    try:
        from vectrax.user_memory import get_user_profile
        profile = get_user_profile(user_id)
        if profile:
            ctx.user_name = profile.get("name", "") or ""
    except Exception:
        pass

    # Language detection
    try:
        from core.language_gate import get_user_language
        ctx.user_lang = get_user_language(user_id, content)
    except Exception:
        ctx.user_lang = "es"

    # Mode detection
    try:
        from core.mode_selector import detect_mode, get_tone
        ctx.mode = detect_mode(content)
        ctx.tone = get_tone(ctx.mode)
    except Exception:
        ctx.mode = "conversacional"
        ctx.tone = ""

    # Brief memory summary (what does Vectrax know about this user?)
    try:
        from vectrax.fact_memory import _get_all_facts
        facts = _get_all_facts(user_id)
        if facts:
            summaries = [f"{f['subject']}: {f['value']}" for f in facts[:5]]
            ctx.memory_summary = "; ".join(summaries)
    except Exception:
        pass

    # Relational identity — classify relationship type and build directive
    try:
        from vectrax.relational_identity import classify, build_relational_directive
        rel = classify(user_id, anchor=None)  # anchor loaded above if available
        ctx.relationship = rel.relationship.value
        ctx.relational_directive = build_relational_directive(rel, lang=ctx.user_lang)
    except Exception as _rel_exc:
        logger.debug("Relational identity failed (passthrough): %s", _rel_exc)

    logger.info(
        "IDENTITY | user=%s name=%s mode=%s creator=%s lang=%s rel=%s",
        user_id[:20], ctx.user_name or "?", ctx.mode,
        ctx.is_creator, ctx.user_lang, ctx.relationship or "?",
    )

    return ctx
