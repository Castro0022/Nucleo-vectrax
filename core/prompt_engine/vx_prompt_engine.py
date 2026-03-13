"""
VX Prompt Engine — Motor Principal
====================================
Convierte instrucciones humanas simples, ambiguas o incompletas en prompts
estructurados, precisos y reutilizables.

Pipeline:
  Entrada usuario → detect_intent → refine_structure → build_prompt
  → route_model → PromptPackage listo

Modos:
  - manual: el usuario pide optimización explícitamente
  - auto: el sistema optimiza automáticamente antes de enviar
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load templates
# ---------------------------------------------------------------------------

_TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "vx_prompt_templates.json")

def _load_templates() -> Dict:
    """Carga templates desde JSON. Fail-safe."""
    try:
        with open(_TEMPLATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("intents", {})
    except Exception as e:
        logger.warning(f"Could not load prompt templates: {e}")
        return {}


TEMPLATES = _load_templates()


# ---------------------------------------------------------------------------
# Intent detection patterns (prompt-level, bilingual es/en)
# ---------------------------------------------------------------------------

INTENT_PATTERNS: Dict[str, re.Pattern] = {
    "codigo": re.compile(
        r'\b(código|codificar|programar|función|script|api|bug|deploy|'
        r'code|function|implement|debug|refactor|compile|test|module|'
        r'class|method|endpoint|pipeline|backend|frontend|database|query|'
        r'python|javascript|java|go|rust|sql|html|css)\b',
        re.IGNORECASE,
    ),
    "ventas": re.compile(
        r'\b(vender|ventas|cliente|propuesta comercial|pitch|'
        r'oferta|cotización|cierre|prospecto|lead|funnel|'
        r'sell|sales|deal|proposal|closing|revenue|conversion)\b',
        re.IGNORECASE,
    ),
    "analisis": re.compile(
        r'\b(analizar|análisis|evaluar|evaluación|comparar|comparativa|'
        r'métricas|indicadores|diagnóstico|tendencia|'
        r'analyze|analysis|evaluate|assess|benchmark|metrics|kpi|trend)\b',
        re.IGNORECASE,
    ),
    "investigacion": re.compile(
        r'\b(investigar|investigación|estudio|fuentes|evidencia|'
        r'hipótesis|metodología|literatura|paper|'
        r'research|study|evidence|hypothesis|literature|survey|findings)\b',
        re.IGNORECASE,
    ),
    "redaccion": re.compile(
        r'\b(redactar|redacción|escribir|texto|artículo|blog|correo|'
        r'comunicado|nota|carta|email|newsletter|'
        r'write|draft|article|copy|content|editorial|post)\b',
        re.IGNORECASE,
    ),
    "contratos": re.compile(
        r'\b(contrato|cláusula|acuerdo|términos|legal|'
        r'obligaciones|partes|firma|nda|sla|'
        r'contract|clause|agreement|terms|legal|binding|liability)\b',
        re.IGNORECASE,
    ),
    "automatizacion": re.compile(
        r'\b(automatizar|automatización|flujo|workflow|cron|'
        r'scheduler|trigger|bot|scraper|etl|'
        r'automate|automation|workflow|pipeline|batch|schedule)\b',
        re.IGNORECASE,
    ),
    "estrategia": re.compile(
        r'\b(estrategia|plan|roadmap|visión|misión|objetivo|'
        r'competencia|posicionamiento|mercado|diferenciación|'
        r'strategy|planning|roadmap|vision|mission|competitive|market)\b',
        re.IGNORECASE,
    ),
    "imagen": re.compile(
        r'\b(imagen|foto|ilustración|diseño|visual|logo|'
        r'banner|gráfico|mockup|render|'
        r'image|photo|illustration|design|visual|render|thumbnail)\b',
        re.IGNORECASE,
    ),
    "sintesis": re.compile(
        r'\b(resumir|resumen|síntesis|sintetizar|condensar|'
        r'puntos clave|extracto|brief|'
        r'summarize|summary|condense|digest|tldr|key\s+points|overview)\b',
        re.IGNORECASE,
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PromptSpec:
    """Especificación estructurada interna de un prompt."""
    intent: str
    role: str
    objective: str
    context: str
    constraints: List[str]
    tone: str
    language: str
    output_format: str
    quality_checks: List[str]
    recommended_model: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "role": self.role,
            "objective": self.objective,
            "context": self.context,
            "constraints": self.constraints,
            "tone": self.tone,
            "language": self.language,
            "output_format": self.output_format,
            "quality_checks": self.quality_checks,
            "recommended_model": self.recommended_model,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class PromptPackage:
    """Paquete completo de prompt optimizado, listo para enviar."""
    spec: PromptSpec
    prompt_short: str
    prompt_medium: str
    prompt_deep: str
    prompt_final: str       # el que se envía al LLM
    prompt_id: str
    model_provider: str
    model_name: str
    routing_reason: str
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "spec": self.spec.to_dict(),
            "prompt_short": self.prompt_short,
            "prompt_medium": self.prompt_medium,
            "prompt_deep": self.prompt_deep,
            "prompt_final": self.prompt_final,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "routing_reason": self.routing_reason,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# VXPromptEngine
# ---------------------------------------------------------------------------

class VXPromptEngine:
    """
    Motor principal de optimización de prompts.

    Usage::

        engine = VXPromptEngine()
        package = engine.optimize("Hazme un script que descargue datos de una API")
        print(package.prompt_final)
    """

    def __init__(self, mode: str = "manual"):
        """
        Args:
            mode: 'manual' o 'auto'. En auto, se optimiza cada tarea antes de enviar.
        """
        self.mode = mode
        self._templates = TEMPLATES
        self._memory = None
        self._router = None

    # -- Lazy dependencies --------------------------------------------------

    def _get_memory(self):
        if self._memory is None:
            try:
                from core.prompt_engine.vx_prompt_memory import get_prompt_memory
                self._memory = get_prompt_memory()
            except Exception as e:
                logger.debug(f"PromptMemory not available: {e}")
        return self._memory

    def _get_router(self):
        if self._router is None:
            try:
                from core.prompt_engine.vx_prompt_router import get_prompt_router
                self._router = get_prompt_router()
            except Exception as e:
                logger.debug(f"PromptRouter not available: {e}")
        return self._router

    # -- Main pipeline ------------------------------------------------------

    def optimize(
        self,
        user_input: str,
        context: str = "",
        language: str = "es",
        force_local: bool = False,
        variant: str = "medium",
    ) -> PromptPackage:
        """
        Pipeline completo de optimización.

        Args:
            user_input: Instrucción original del usuario.
            context: Contexto adicional (archivo, conversación, etc.).
            language: Idioma preferido de salida.
            force_local: Forzar modelo local (privacidad).
            variant: Variante por defecto para prompt_final ('short', 'medium', 'deep').

        Returns:
            PromptPackage listo para enviar al LLM.
        """
        # 1. Detectar intención
        intent, intent_confidence = self._detect_intent(user_input)

        # 2. Consultar memoria por patrones previos
        memory_hint = self._consult_memory(intent)

        # 3. Construir especificación estructurada
        spec = self._build_spec(
            user_input=user_input,
            intent=intent,
            confidence=intent_confidence,
            context=context,
            language=language,
            memory_hint=memory_hint,
        )

        # 4. Generar las 3 variantes de prompt
        prompt_short = self._build_prompt_short(spec)
        prompt_medium = self._build_prompt_medium(spec)
        prompt_deep = self._build_prompt_deep(spec)

        # Seleccionar variante final
        prompt_final = {
            "short": prompt_short,
            "medium": prompt_medium,
            "deep": prompt_deep,
        }.get(variant, prompt_medium)

        # 5. Enrutar a modelo
        model_provider, model_name, routing_reason = self._route(
            intent=intent,
            prompt_text=prompt_final,
            force_local=force_local,
        )

        # Actualizar spec con modelo recomendado
        spec.recommended_model = f"{model_provider}/{model_name}"

        # 6. Generar ID
        prompt_id = self._generate_id(intent, user_input)

        # 7. Registrar en memoria (solo categorías abstractas)
        self._record_to_memory(prompt_id, spec, model_provider, model_name)

        package = PromptPackage(
            spec=spec,
            prompt_short=prompt_short,
            prompt_medium=prompt_medium,
            prompt_deep=prompt_deep,
            prompt_final=prompt_final,
            prompt_id=prompt_id,
            model_provider=model_provider,
            model_name=model_name,
            routing_reason=routing_reason,
        )

        logger.info(
            f"[VXPromptEngine] optimized: intent={intent} "
            f"confidence={intent_confidence:.2f} model={model_provider}/{model_name}"
        )

        return package

    def optimize_auto(self, user_input: str, **kwargs) -> PromptPackage:
        """
        Optimización automática — atajo para modo auto.

        Usa variante 'medium' por defecto y detecta todo automáticamente.
        """
        return self.optimize(user_input, variant="medium", **kwargs)

    # -- Step 1: Intent detection -------------------------------------------

    def _detect_intent(self, text: str) -> tuple:
        """
        Detecta la intención del usuario desde texto libre.

        Returns:
            (intent: str, confidence: float)
        """
        text_lower = text.lower()
        scores: Dict[str, int] = {}

        for intent, pattern in INTENT_PATTERNS.items():
            matches = pattern.findall(text_lower)
            if matches:
                scores[intent] = len(matches)

        if not scores:
            return ("sintesis", 0.3)  # default safe fallback

        # Best match
        best = max(scores, key=scores.get)
        hit_count = scores[best]
        confidence = min(1.0, 0.4 + hit_count * 0.2)

        # Reduce confidence if ambiguous
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) > 1 and sorted_scores[1] >= sorted_scores[0] * 0.7:
            confidence *= 0.75

        return (best, round(confidence, 3))

    # -- Step 2: Memory consultation ----------------------------------------

    def _consult_memory(self, intent: str) -> Optional[Dict]:
        """Consulta patrones previos exitosos para este intent."""
        memory = self._get_memory()
        if memory is None:
            return None

        try:
            best = memory.get_best_pattern(intent)
            if best and best.avg_score >= 0.6:
                return {
                    "preferred_model": best.model,
                    "avg_score": best.avg_score,
                    "uses": best.total_uses,
                }
        except Exception as e:
            logger.debug(f"Memory consultation failed: {e}")

        return None

    # -- Step 3: Build spec -------------------------------------------------

    def _build_spec(
        self,
        user_input: str,
        intent: str,
        confidence: float,
        context: str,
        language: str,
        memory_hint: Optional[Dict],
    ) -> PromptSpec:
        """Construye la especificación estructurada del prompt."""
        template = self._templates.get(intent, {})

        # Extract objective from user input (the core instruction)
        objective = user_input.strip()

        # Infer implicit context
        inferred_context = context
        if not inferred_context:
            inferred_context = self._infer_context(user_input, intent)

        # Build spec from template + user input
        spec = PromptSpec(
            intent=intent,
            role=template.get("role", "Eres un asistente experto."),
            objective=objective,
            context=inferred_context,
            constraints=list(template.get("constraints", [])),
            tone=template.get("tone", "profesional, claro"),
            language=language or template.get("default_language", "es"),
            output_format=template.get("output_format", "texto estructurado"),
            quality_checks=list(template.get("quality_checks", [])),
            recommended_model=template.get("model_preference", "reasoning"),
            confidence=confidence,
        )

        # Enrich from memory if available
        if memory_hint:
            spec.recommended_model = memory_hint.get("preferred_model", spec.recommended_model)

        return spec

    def _infer_context(self, text: str, intent: str) -> str:
        """
        Infiere contexto implícito cuando el usuario no lo proporciona.

        Regla: completar contexto implícito si es inferible.
        """
        parts = []

        # Length-based hints
        if len(text) < 30:
            parts.append("Instrucción breve — completar con suposiciones razonables.")

        # Intent-specific context
        INTENT_CONTEXTS = {
            "codigo": "Contexto: desarrollo de software. Asumir buenas prácticas de ingeniería.",
            "ventas": "Contexto: comunicación comercial. Orientado a conversión y profesionalismo.",
            "analisis": "Contexto: evaluación objetiva. Priorizar datos sobre opiniones.",
            "investigacion": "Contexto: investigación formal. Rigor metodológico requerido.",
            "redaccion": "Contexto: comunicación escrita profesional.",
            "contratos": "Contexto: documentación legal. Precisión y protección de intereses.",
            "automatizacion": "Contexto: automatización de procesos. Eficiencia y confiabilidad.",
            "estrategia": "Contexto: planificación estratégica. Visión a largo plazo.",
            "imagen": "Contexto: generación de imagen por IA. Descripción visual detallada.",
            "sintesis": "Contexto: comprensión y resumen. Extraer lo esencial.",
        }

        if intent in INTENT_CONTEXTS:
            parts.append(INTENT_CONTEXTS[intent])

        return " ".join(parts) if parts else ""

    # -- Step 4: Build prompts (3 variants) ---------------------------------

    def _build_prompt_short(self, spec: PromptSpec) -> str:
        """Variante corta: directa al punto."""
        lines = [
            f"[{spec.role.split('.')[0].strip()}]",
            "",
            spec.objective,
        ]
        if spec.output_format:
            lines.append(f"\nFormato: {spec.output_format}")
        if spec.language != "en":
            lines.append(f"Idioma: {spec.language}")
        return "\n".join(lines)

    def _build_prompt_medium(self, spec: PromptSpec) -> str:
        """Variante media: equilibrio entre concisión y detalle."""
        lines = [
            f"# Rol\n{spec.role}",
            f"\n# Objetivo\n{spec.objective}",
        ]
        if spec.context:
            lines.append(f"\n# Contexto\n{spec.context}")
        if spec.constraints:
            constraints_text = "\n".join(f"- {c}" for c in spec.constraints)
            lines.append(f"\n# Restricciones\n{constraints_text}")
        lines.append(f"\n# Formato de salida\n{spec.output_format}")
        lines.append(f"\n# Tono\n{spec.tone}")
        if spec.language != "en":
            lines.append(f"\n# Idioma\n{spec.language}")
        return "\n".join(lines)

    def _build_prompt_deep(self, spec: PromptSpec) -> str:
        """Variante profunda: máximo detalle y estructura."""
        lines = [
            f"# Rol\n{spec.role}",
            f"\n# Objetivo\n{spec.objective}",
        ]
        if spec.context:
            lines.append(f"\n# Contexto\n{spec.context}")
        if spec.constraints:
            constraints_text = "\n".join(f"- {c}" for c in spec.constraints)
            lines.append(f"\n# Restricciones\n{constraints_text}")
        lines.append(f"\n# Formato de salida\n{spec.output_format}")
        lines.append(f"\n# Tono y estilo\n{spec.tone}")
        if spec.quality_checks:
            checks_text = "\n".join(f"- {c}" for c in spec.quality_checks)
            lines.append(f"\n# Criterios de calidad\n{checks_text}")
        if spec.language != "en":
            lines.append(f"\n# Idioma de respuesta\n{spec.language}")
        lines.append(
            "\n# Instrucciones adicionales\n"
            "- Sé preciso y completo.\n"
            "- No incluyas información irrelevante.\n"
            "- Si necesitas hacer suposiciones, indícalas explícitamente.\n"
            "- Prioriza claridad y utilidad operativa."
        )
        return "\n".join(lines)

    # -- Step 5: Routing ----------------------------------------------------

    def _route(
        self,
        intent: str,
        prompt_text: str,
        force_local: bool,
    ) -> tuple:
        """
        Enruta el prompt al modelo adecuado.

        Returns:
            (provider: str, model: str, reason: str)
        """
        router = self._get_router()
        if router is None:
            return ("ollama", "llama3", "no_router_available")

        try:
            result = router.select_model(
                intent=intent,
                prompt_text=prompt_text,
                force_local=force_local,
            )
            return (result.model_provider, result.model_name, result.reason)
        except Exception as e:
            logger.warning(f"Routing failed: {e}")
            return ("ollama", "llama3", f"routing_error:{e}")

    # -- Step 6: ID generation ----------------------------------------------

    def _generate_id(self, intent: str, user_input: str) -> str:
        """Genera un ID determinístico para el prompt."""
        ts = datetime.now().isoformat(timespec="milliseconds")
        raw = f"{intent}:{ts}:{len(user_input)}"
        return f"vxp_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    # -- Step 7: Memory recording -------------------------------------------

    def _record_to_memory(
        self,
        prompt_id: str,
        spec: PromptSpec,
        model_provider: str,
        model_name: str,
    ) -> None:
        """Registra en memoria solo categorías abstractas."""
        memory = self._get_memory()
        if memory is None:
            return

        try:
            from core.prompt_engine.vx_prompt_memory import PromptRecord

            record = PromptRecord(
                prompt_id=prompt_id,
                intent=spec.intent,
                model_used=f"{model_provider}/{model_name}",
                tone=spec.tone,
                output_format=spec.output_format,
                language=spec.language,
                confidence=spec.confidence,
            )
            memory.record(record)
        except Exception as e:
            logger.debug(f"Memory recording failed: {e}")

    # -- Evaluate result (post-execution) -----------------------------------

    def evaluate_result(self, prompt_id: str, score: float, execution_ms: Optional[int] = None) -> bool:
        """
        Registra la calidad del resultado de un prompt ejecutado.

        Esto alimenta el sistema de aprendizaje para mejorar futuros prompts.

        Args:
            prompt_id: ID del prompt (de PromptPackage.prompt_id).
            score: Calidad percibida (0.0 - 1.0).
            execution_ms: Tiempo de ejecución.

        Returns:
            True si se registró exitosamente.
        """
        memory = self._get_memory()
        if memory is None:
            return False

        try:
            return memory.record_result(prompt_id, score, execution_ms)
        except Exception as e:
            logger.warning(f"Evaluation recording failed: {e}")
            return False

    # -- Utilities ----------------------------------------------------------

    def get_supported_intents(self) -> List[str]:
        """Lista de intenciones soportadas."""
        return sorted(INTENT_PATTERNS.keys())

    def get_stats(self) -> Dict:
        """Estadísticas del motor y memoria."""
        memory = self._get_memory()
        stats = {
            "mode": self.mode,
            "supported_intents": self.get_supported_intents(),
            "templates_loaded": len(self._templates),
        }
        if memory:
            try:
                stats["memory"] = memory.get_stats()
            except Exception:
                stats["memory"] = None
        return stats


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_engine: Optional[VXPromptEngine] = None


def get_engine(mode: str = "manual") -> VXPromptEngine:
    """Retorna la instancia global del VXPromptEngine."""
    global _global_engine
    if _global_engine is None:
        _global_engine = VXPromptEngine(mode=mode)
    return _global_engine
