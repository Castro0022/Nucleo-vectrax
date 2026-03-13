"""
Vectrax Task Classifier
========================
Infers task_type from prompt + context using rules-first approach.
Optional LLM-based classification behind VX_LLM_CLASSIFIER=1 feature flag.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from .capabilities import Capability


# ---------------------------------------------------------------------------
# Extended TaskType enum
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    CODE = "code"
    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    CREATIVE = "creative"
    VISION = "vision"
    STRUCTURED_EXTRACTION = "structured_extraction"
    LONG_CONTEXT = "long_context"
    TOOL_USE = "tool_use"
    CHAT = "chat"


# Map TaskType → primary Capability required
TASK_CAPABILITY_MAP: Dict[TaskType, Capability] = {
    TaskType.CODE: Capability.CODING,
    TaskType.REASONING: Capability.REASONING,
    TaskType.SUMMARIZATION: Capability.SUMMARIZATION,
    TaskType.TRANSLATION: Capability.TRANSLATION,
    TaskType.VISION: Capability.VISION,
    TaskType.STRUCTURED_EXTRACTION: Capability.STRUCTURED_EXTRACTION,
    TaskType.LONG_CONTEXT: Capability.LONG_CONTEXT,
    TaskType.TOOL_USE: Capability.TOOL_USE,
    TaskType.CREATIVE: Capability.REASONING,
    TaskType.CHAT: Capability.REASONING,
}


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass
class TaskClassification:
    task_type: TaskType
    confidence: float           # 0.0 - 1.0
    features: Dict[str, any] = field(default_factory=dict)
    method: str = "rules"       # "rules" or "llm"


# ---------------------------------------------------------------------------
# Regex patterns (extended from SmartRouter)
# ---------------------------------------------------------------------------

PATTERNS: Dict[TaskType, re.Pattern] = {
    TaskType.CODE: re.compile(
        r'\b(code|function|class|implement|program|script|algorithm|'
        r'debug|fix|refactor|python|javascript|java|go|rust|typescript|'
        r'código|función|programar|compilar|deploy|api|bug|error|'
        r'commit|pull\s*request|merge|lint|test\s+suite|unit\s+test|'
        r'def\s+\w+|import\s+\w+|npm|pip|cargo|make)\b',
        re.IGNORECASE,
    ),
    TaskType.REASONING: re.compile(
        r'\b(solve|calculate|analyze|figure\s+out|reason|logic|'
        r'problem|equation|proof|deduce|explain\s+why|compare|'
        r'evaluate|assess|think\s+through|step\s+by\s+step|'
        r'analiza|resuelve|calcula|razona)\b',
        re.IGNORECASE,
    ),
    TaskType.SUMMARIZATION: re.compile(
        r'\b(summarize|summary|brief|condense|tldr|key\s+points|'
        r'main\s+ideas|overview|abstract|digest|recap|'
        r'resume|resumen|resumir|sintetiza)\b',
        re.IGNORECASE,
    ),
    TaskType.TRANSLATION: re.compile(
        r'\b(translate|translation|convert\s+to\s+\w+\s*language|'
        r'in\s+spanish|in\s+french|in\s+german|in\s+chinese|in\s+japanese|'
        r'traduce|traducir|traducción|al\s+inglés|al\s+español)\b',
        re.IGNORECASE,
    ),
    TaskType.CREATIVE: re.compile(
        r'\b(write\s+a?\s*story|poem|creative|imagine|invent|'
        r'brainstorm|novel|fiction|compose|cuento|poema|imagina)\b',
        re.IGNORECASE,
    ),
    TaskType.STRUCTURED_EXTRACTION: re.compile(
        r'\b(extract|parse|json|schema|csv|structured|'
        r'fields?|table|format\s+as|output\s+as|'
        r'extrae|parsea|formato\s+json)\b',
        re.IGNORECASE,
    ),
    TaskType.TOOL_USE: re.compile(
        r'\b(use\s+tool|call\s+function|api\s+call|execute|run\s+command|'
        r'search\s+the\s+web|look\s+up|fetch|query\s+database|'
        r'usa\s+herramienta|ejecuta|consulta)\b',
        re.IGNORECASE,
    ),
    TaskType.VISION: re.compile(
        r'\b(image|picture|photo|screenshot|diagram|chart|'
        r'what\s+is\s+in\s+this|describe\s+this\s+image|OCR|'
        r'imagen|foto|captura|diagrama)\b',
        re.IGNORECASE,
    ),
}

# Thresholds
LONG_CONTEXT_CHARS = 6000          # prompt > 6K chars → LONG_CONTEXT
LONG_CONTEXT_CONFIDENCE = 0.7


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_task(
    prompt: str,
    context: Optional[str] = None,
) -> TaskClassification:
    """
    Classify a prompt into a TaskType using rules-first approach.

    1. Regex pattern matching (highest confidence)
    2. Length heuristic for LONG_CONTEXT
    3. Structural heuristics for STRUCTURED_EXTRACTION
    4. Optional LLM fallback (VX_LLM_CLASSIFIER=1)
    5. Default: CHAT

    Args:
        prompt: The user's prompt text.
        context: Optional additional context (e.g., file contents).

    Returns:
        TaskClassification with task_type, confidence, and features.
    """
    full_text = prompt
    if context:
        full_text = f"{prompt}\n{context}"

    features: Dict[str, any] = {
        "prompt_length": len(prompt),
        "has_context": context is not None,
    }

    # --- Phase 1: Regex pattern matching ---
    scores: Dict[TaskType, int] = {}
    for task_type, pattern in PATTERNS.items():
        matches = pattern.findall(full_text)
        if matches:
            scores[task_type] = len(matches)

    if scores:
        best = max(scores, key=scores.get)
        hit_count = scores[best]
        confidence = min(1.0, 0.5 + hit_count * 0.15)
        features["matched_patterns"] = hit_count
        features["method_detail"] = f"regex:{best.value}"

        # If multiple types matched with similar scores, lower confidence
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) > 1 and sorted_scores[1] >= sorted_scores[0] * 0.8:
            confidence *= 0.8
            features["ambiguous"] = True

        return TaskClassification(
            task_type=best,
            confidence=round(confidence, 3),
            features=features,
            method="rules",
        )

    # --- Phase 2: Length heuristic ---
    total_len = len(full_text)
    if total_len > LONG_CONTEXT_CHARS:
        features["method_detail"] = f"length_heuristic:{total_len}"
        return TaskClassification(
            task_type=TaskType.LONG_CONTEXT,
            confidence=LONG_CONTEXT_CONFIDENCE,
            features=features,
            method="rules",
        )

    # --- Phase 3: Structural heuristics ---
    # Check for JSON-like structure in prompt
    if re.search(r'[{}\[\]].*[{}\[\]]', prompt) or '```json' in prompt.lower():
        features["method_detail"] = "structural:json_detected"
        return TaskClassification(
            task_type=TaskType.STRUCTURED_EXTRACTION,
            confidence=0.6,
            features=features,
            method="rules",
        )

    # --- Phase 4: LLM fallback (behind feature flag) ---
    if os.environ.get("VX_LLM_CLASSIFIER") == "1":
        llm_result = _llm_classify(prompt)
        if llm_result is not None:
            features["method_detail"] = "llm_fallback"
            return TaskClassification(
                task_type=llm_result,
                confidence=0.5,
                features=features,
                method="llm",
            )

    # --- Phase 5: Default ---
    features["method_detail"] = "default"
    return TaskClassification(
        task_type=TaskType.CHAT,
        confidence=0.4,
        features=features,
        method="rules",
    )


def _llm_classify(prompt: str) -> Optional[TaskType]:
    """
    LLM-based classification fallback.
    Only called when VX_LLM_CLASSIFIER=1.
    Returns None if classification fails.
    """
    # Placeholder — would call local Ollama with a classification prompt
    # For now, returns None (rules-only mode)
    return None
