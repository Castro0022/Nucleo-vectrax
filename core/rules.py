from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class RuleResult:
    intent: str
    text: str
    action: Optional[Dict[str, Any]] = None
    tags: str = ""


def apply(text: str, source="user", metadata=None):

    t = text.lower()

    # --- TRADING ---
    if "btc" in t or "bitcoin" in t:
        return RuleResult(
            intent="trading",
            text="Detectado trading en BTC. Dime timeframe y nivel.",
            action={"type": "ask"},
            tags="trading"
        )

    # --- RELATIONSHIP ---
    if "joy" in t or "relación" in t:
        return RuleResult(
            intent="relationship",
            text="Te escucho. ¿Qué quieres cuidar ahora mismo?",
            action={"type": "support"},
            tags="relationship"
        )

    # --- GENERAL ---
    return RuleResult(
        intent="general",
        text="Entendido.",
        action=None,
        tags="general"
    )
