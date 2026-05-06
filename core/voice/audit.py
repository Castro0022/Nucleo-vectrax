"""
core/voice/audit.py — Motor de Auditoría Suave

Verifica la respuesta lista para enviar y emite señales (no sustituye).

Diferencia clave con la lógica anterior:
  - El auditor antiguo rechazaba y pateaba a un fallback robótico.
  - Este auditor aplica MICRO-FIXES (limpiezas inocuas) y SEÑALA
    problemas mediante warnings/should_block. Si should_block es
    True, el caller decide (típicamente usa fallback_response
    constructiva); si es False, manda el texto.

Bloqueos duros (should_block=True):
  - texto vacío después de limpieza
  - exclusivamente palabras prohibidas / contenido peligroso

Warnings (no bloquean):
  - posible idioma incorrecto
  - longitud excesiva (sin límite específico — humanizer ya truncó)
  - meta-respuesta detectada ("no puedo", "no tengo info")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class AuditResult:
    """Resultado del auditor suave."""

    text: str                       # texto post micro-fixes (puede ser igual)
    warnings: List[str] = field(default_factory=list)
    should_block: bool = False
    reason: str = ""

    def has_issues(self) -> bool:
        return self.should_block or bool(self.warnings)


_VAGUE_META = re.compile(
    r"(?:"
    r"(?:lack\s+(?:enough\s+)?information|no\s+tengo\s+suficiente\s+informaci[oó]n)"
    r"|(?:add\s+more\s+context|intenta\s+con\s+m[aá]s\s+contexto)"
    r"|(?:try\s+(?:rephrasing|again)|intenta\s+reformular)"
    r"|(?:i\s+(?:can'?t|cannot|couldn'?t)\s+(?:generate|provide|find))"
    r"|(?:no\s+puedo\s+generar|no\s+encuentro\s+informaci[oó]n)"
    r"|(?:processing\s+your\s+(?:question|request)|proceso\s+tu\s+pregunta)"
    r"|(?:can'?t\s+generate\s+a\s+precise\s+answer)"
    r"|(?:no\s+puedo\s+.{0,20}respuesta\s+precisa)"
    r"|(?:processed\s+and\s+stored\s+in\s+memory)"
    r"|(?:procesado\s+y\s+registrado\s+en\s+memoria)"
    r"|(?:rephrase\s+directly)"
    r"|(?:reformula\s+en\s+una\s+frase\s+directa)"
    r")",
    re.IGNORECASE,
)

_FORBIDDEN = re.compile(
    # Slurs / instrucciones de daño / etc. — dejamos minimalista; el
    # objetivo aquí no es policing de contenido sino evitar leak de
    # logs internos.
    r"\b(?:traceback|stack\s+trace|exception\s+raised|null\s*pointer)\b",
    re.IGNORECASE,
)

_ENGLISH_HEAVY = re.compile(
    r"\b(?:the|is|are|was|were|have|has|been|with|from|this|that|"
    r"which|would|could|should)\b",
    re.IGNORECASE,
)
_SPANISH_HEAVY = re.compile(
    r"\b(?:el|la|los|las|es|son|fue|era|tiene|para|por|como|que|"
    r"una?|del|con|este|esta)\b",
    re.IGNORECASE,
)


def audit_without_replacing(
    response: str,
    user_input: str = "",
    expected_lang: str = "",
) -> AuditResult:
    """Audita una respuesta lista para enviar.

    NO sustituye por fallback. Solo aplica micro-fixes y señala.

    Args:
      response: texto que está a punto de salir al usuario.
      user_input: el mensaje del usuario (para señales de idioma).
      expected_lang: 'es' | 'en' | 'fr' | etc. Si está, se usa para
        señalar mismatch de idioma.

    Returns:
      AuditResult con text (posiblemente con micro-fixes), warnings
      y should_block.
    """
    if not response or not response.strip():
        return AuditResult(
            text="",
            warnings=["empty_response"],
            should_block=True,
            reason="empty",
        )

    text = response.strip()
    warnings: List[str] = []

    # Bloqueo duro: leak de errores internos
    if _FORBIDDEN.search(text):
        return AuditResult(
            text="",
            warnings=["internal_leak"],
            should_block=True,
            reason="internal_leak",
        )

    # Warning: meta-respuesta vaga (no bloquea, solo avisa)
    if _VAGUE_META.search(text):
        warnings.append("vague_meta")

    # Warning: idioma probable mismatch (no bloquea)
    if expected_lang in ("es", "en"):
        word_count = max(len(text.split()), 1)
        if expected_lang == "es":
            en_count = len(_ENGLISH_HEAVY.findall(text))
            if en_count / word_count > 0.30:
                warnings.append("possible_lang_mismatch_es_to_en")
        elif expected_lang == "en":
            es_count = len(_SPANISH_HEAVY.findall(text))
            if es_count / word_count > 0.30:
                warnings.append("possible_lang_mismatch_en_to_es")

    # Micro-fix: normalizar dobles espacios y triple-newlines (idempotente
    # con humanizer, defensa redundante)
    fixed = re.sub(r" {2,}", " ", text)
    fixed = re.sub(r"\n{3,}", "\n\n", fixed)
    fixed = fixed.strip()

    # Si después del micro-fix queda vacío
    if not fixed:
        return AuditResult(
            text="",
            warnings=warnings + ["empty_after_microfix"],
            should_block=True,
            reason="empty_after_microfix",
        )

    return AuditResult(
        text=fixed,
        warnings=warnings,
        should_block=False,
    )
