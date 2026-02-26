"""
Vectrax Strategic Router — Autopilot Mode
==========================================
SINGLE por defecto. DUAL solo con señales claras.
Auto-aprendizaje, budget/latency guards, kill switch.
"""

import os
import re
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Dict, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

class RoutingMode(str, Enum):
    SINGLE         = "SINGLE"
    DUAL_FUSE      = "DUAL_FUSE"
    DUAL_CONSENSUS = "DUAL_CONSENSUS"
    FALLBACK_CHAIN = "FALLBACK_CHAIN"


class RiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


@dataclass
class RoutingDecision:
    topic: str
    mode: RoutingMode
    providers: List[str]         # [primary, fallback...]
    reason: str
    confidence: float            # 0-1
    risk_level: RiskLevel = RiskLevel.LOW
    estimated_savings: float = 0.0  # llamadas ahorradas (0 o 1)

    def summary(self) -> Dict[str, Any]:
        """Devuelve dict compacto: mode/reason/confidence."""
        return {
            "mode": self.mode.value,
            "reason": self.reason,
            "confidence": self.confidence,
        }

    def full_dict(self) -> Dict[str, Any]:
        """Auditoría completa para DB y respuestas."""
        return {
            "topic": self.topic,
            "mode": self.mode.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "risk_level": self.risk_level.value,
            "estimated_savings": self.estimated_savings,
            "providers": self.providers,
        }


@dataclass
class TopicProfile:
    """Perfil histórico de un tópico."""
    query_count: int = 0
    avg_divergence: float = 0.0
    weights: Dict[str, float] = field(default_factory=dict)
    avg_latency: Dict[str, float] = field(default_factory=dict)
    error_rate: Dict[str, float] = field(default_factory=dict)
    p95_latency: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Taxonomía de tópicos
# ---------------------------------------------------------------------------

TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "trading":      ["btc", "bitcoin", "entrada", "stop", "trade", "short", "long",
                     "timeframe", "cripto", "eth", "vela", "soporte", "resistencia"],
    "code":         ["código", "python", "bug", "deploy", "api", "función", "error",
                     "script", "github", "servidor", "docker", "database", "sql"],
    "relationship": ["relación", "joy", "pareja", "amor", "conflicto", "cita",
                     "comunicación", "pelea", "confianza"],
    "health":       ["salud", "dormir", "entreno", "gym", "dieta", "peso",
                     "correr", "meditar", "estrés", "ansiedad"],
}

TOPIC_DESCRIPTIONS: Dict[str, str] = {
    "trading":      "análisis técnico de trading criptomonedas bitcoin entrada stop loss",
    "code":         "programación desarrollo software código python api bug deploy",
    "relationship": "relación de pareja amor comunicación conflicto emocional",
    "health":       "salud física ejercicio dieta sueño bienestar mental",
    "general":      "pregunta general información variada consulta abierta",
}

# Tópicos que requieren alta fiabilidad → fuerzan DUAL
SENSITIVE_TOPICS = {"trading", "health"}

# ---------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------

KEYWORD_MATCH_THRESHOLD   = 2
EMBEDDING_SIM_THRESHOLD   = 0.35

# Señales para escalar de SINGLE → DUAL
DIVERGENCE_ESCALATE       = 0.35     # div histórica para forzar DUAL
ERROR_RATE_ESCALATE       = 0.20     # error rate del dominante para forzar DUAL
LONG_PROMPT_CHARS         = 300      # prompt largo → más ambiguo
STEP_BY_STEP_PATTERN      = re.compile(
    r"(paso a paso|step.?by.?step|explica.*detalle|analiza.*profund|compara)", re.IGNORECASE
)

# Auto-aprendizaje: incrementos bayesianos
LEARN_ALPHA_SUCCESS       = 0.3      # reward por éxito
LEARN_BETA_FAILURE        = 0.5      # penalty por fallo
LEARN_BETA_SLOW           = 0.15     # penalty por latencia alta
LEARN_ALPHA_LOW_DIV       = 0.1      # reward si divergencia baja (ambos bien)
LEARN_BETA_HIGH_DIV       = 0.1      # penalty si divergencia alta (desacuerdo)
LATENCY_SLOW_RATIO        = 2.0      # si es 2x más lento que el otro → penalizar


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class StrategicRouter:

    def __init__(self, embedder, db_path: str):
        self._embedder = embedder
        self._db_path = db_path
        self._topic_embeddings: Optional[Dict[str, np.ndarray]] = None

    # -- helpers ---------------------------------------------------------

    def _db(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _env(self, key: str, default: str = "") -> str:
        return (os.getenv(key) or default).strip()

    def _env_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._env(key, str(default)))
        except ValueError:
            return default

    def _ensure_topic_embeddings(self):
        if self._topic_embeddings is not None:
            return
        texts = list(TOPIC_DESCRIPTIONS.values())
        keys  = list(TOPIC_DESCRIPTIONS.keys())
        vecs  = self._embedder.encode(texts, normalize_embeddings=True)
        self._topic_embeddings = {
            k: np.asarray(v, dtype=np.float32) for k, v in zip(keys, vecs)
        }

    # -- 1. Detección de tópico -----------------------------------------

    def detect_topic(self, prompt: str) -> Tuple[str, float]:
        lower = prompt.lower()

        # Fase 1: keyword match
        scores = {}
        for topic, keywords in TOPIC_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in lower)
            if hits > 0:
                scores[topic] = hits

        if scores:
            best = max(scores, key=scores.get)
            if scores[best] >= KEYWORD_MATCH_THRESHOLD:
                return best, min(1.0, scores[best] / len(TOPIC_KEYWORDS[best]))

        # Fase 2: embedding similarity
        self._ensure_topic_embeddings()
        prompt_vec = self._embedder.encode([prompt], normalize_embeddings=True)
        prompt_vec = np.asarray(prompt_vec[0], dtype=np.float32)

        best_topic = "general"
        best_sim   = 0.0
        for topic, tvec in self._topic_embeddings.items():
            sim = float(np.dot(prompt_vec, tvec))
            if sim > best_sim:
                best_sim   = sim
                best_topic = topic

        if best_sim >= EMBEDDING_SIM_THRESHOLD and best_topic != "general":
            return best_topic, round(best_sim, 4)

        # Fase 3: keyword débil como tiebreak
        if scores:
            best = max(scores, key=scores.get)
            return best, 0.3

        return "general", 0.5

    # -- 2. Evaluación de historial -------------------------------------

    def evaluate_history(self, topic: str) -> TopicProfile:
        profile = TopicProfile()

        with self._db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, AVG(divergence) AS avg_div "
                "FROM queries WHERE topic=?", (topic,)
            ).fetchone()
            profile.query_count    = int(row["cnt"] or 0)
            profile.avg_divergence = float(row["avg_div"] or 0.0)

            for prow in conn.execute(
                "SELECT provider, alpha, beta FROM provider_weights WHERE topic=?",
                (topic,)
            ).fetchall():
                a, b = float(prow["alpha"]), float(prow["beta"])
                profile.weights[prow["provider"]] = a / (a + b) if (a + b) > 0 else 0.5

            for prow in conn.execute(
                "SELECT r.provider, "
                "       AVG(r.latency_ms) AS avg_ms, "
                "       SUM(CASE WHEN r.error != '' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS err_rate "
                "FROM responses r JOIN queries q ON r.query_id = q.id "
                "WHERE q.topic=? GROUP BY r.provider", (topic,)
            ).fetchall():
                profile.avg_latency[prow["provider"]] = float(prow["avg_ms"] or 0)
                profile.error_rate[prow["provider"]]  = float(prow["err_rate"] or 0)

            # P95 latencia (últimas 50 queries por proveedor)
            for provider_name in ("openai", "gemini"):
                rows = conn.execute(
                    "SELECT r.latency_ms FROM responses r "
                    "JOIN queries q ON r.query_id = q.id "
                    "WHERE q.topic=? AND r.provider=? AND r.latency_ms > 0 "
                    "ORDER BY q.id DESC LIMIT 50", (topic, provider_name)
                ).fetchall()
                if rows:
                    latencies = sorted(float(r["latency_ms"]) for r in rows)
                    idx = int(len(latencies) * 0.95)
                    profile.p95_latency[provider_name] = latencies[min(idx, len(latencies) - 1)]

        return profile

    # -- 3. Evaluación de riesgo ----------------------------------------

    def assess_risk(self, topic: str, prompt: str) -> RiskLevel:
        if topic in SENSITIVE_TOPICS:
            return RiskLevel.HIGH
        if len(prompt) > LONG_PROMPT_CHARS:
            return RiskLevel.MEDIUM
        if STEP_BY_STEP_PATTERN.search(prompt):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    # -- 4. Budget guard ------------------------------------------------

    def _daily_call_count(self) -> int:
        with self._db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM responses "
                "WHERE query_id IN ("
                "  SELECT id FROM queries WHERE DATE(created_at) = DATE('now')"
                ")"
            ).fetchone()
            return int(row["c"] or 0)

    def _budget_exceeded(self) -> bool:
        cap = self._env_int("DAILY_BUDGET_CAP", 500)
        if cap <= 0:
            return False
        return self._daily_call_count() >= cap

    # -- 5. Latency guard -----------------------------------------------

    def _latency_too_high(self, provider: str, profile: TopicProfile) -> bool:
        cap = self._env_int("LATENCY_P95_CAP_MS", 8000)
        if cap <= 0:
            return False
        p95 = profile.p95_latency.get(provider, 0)
        return p95 > cap

    # -- 6. Kill switch -------------------------------------------------

    def _kill_switch(self) -> Optional[RoutingDecision]:
        safe = self._env("SAFE_MODE")
        disabled = self._env("DISABLE_PROVIDER")

        if safe:
            provider = safe if safe in ("openai", "gemini") else "openai"
            other = "gemini" if provider == "openai" else "openai"
            return RoutingDecision(
                topic="override",
                mode=RoutingMode.SINGLE,
                providers=[provider, other],
                reason=f"KILL_SWITCH: SAFE_MODE={safe}",
                confidence=1.0,
                risk_level=RiskLevel.HIGH,
                estimated_savings=1.0,
            )

        if disabled:
            remaining = "gemini" if disabled == "openai" else "openai"
            return RoutingDecision(
                topic="override",
                mode=RoutingMode.SINGLE,
                providers=[remaining],
                reason=f"KILL_SWITCH: DISABLE_PROVIDER={disabled}",
                confidence=1.0,
                risk_level=RiskLevel.HIGH,
                estimated_savings=1.0,
            )

        return None

    # -- 7. Decisión principal (SINGLE-first) ---------------------------

    def decide(self, topic: str, profile: TopicProfile, prompt: str) -> RoutingDecision:
        providers = list(profile.weights.keys()) or ["openai", "gemini"]
        risk = self.assess_risk(topic, prompt)

        # Ordenar por weight (mayor primero)
        ordered = sorted(providers, key=lambda p: profile.weights.get(p, 0.5), reverse=True)
        if not ordered:
            ordered = ["openai", "gemini"]
        dominant = ordered[0]
        dominant_err = profile.error_rate.get(dominant, 0.0)

        reasons = []

        # --- SEÑAL 1: Tópico sensible → DUAL_CONSENSUS ---
        if topic in SENSITIVE_TOPICS and risk == RiskLevel.HIGH:
            reasons.append(f"Tópico sensible ({topic})")
            return RoutingDecision(
                topic=topic, mode=RoutingMode.DUAL_CONSENSUS, providers=ordered,
                reason=" + ".join(reasons) + " → DUAL_CONSENSUS",
                confidence=0.7, risk_level=risk, estimated_savings=0.0,
            )

        # --- SEÑAL 2: Divergencia histórica alta → DUAL_CONSENSUS ---
        if profile.query_count >= 3 and profile.avg_divergence > DIVERGENCE_ESCALATE:
            reasons.append(f"Divergencia alta ({profile.avg_divergence:.3f})")
            return RoutingDecision(
                topic=topic, mode=RoutingMode.DUAL_CONSENSUS, providers=ordered,
                reason=" + ".join(reasons) + " → DUAL_CONSENSUS",
                confidence=0.7, risk_level=max(risk, RiskLevel.MEDIUM),
                estimated_savings=0.0,
            )

        # --- SEÑAL 3: Error rate alto en dominante → DUAL_FUSE ---
        if dominant_err > ERROR_RATE_ESCALATE:
            reasons.append(f"{dominant} error_rate alto ({dominant_err:.2f})")
            return RoutingDecision(
                topic=topic, mode=RoutingMode.DUAL_FUSE, providers=ordered,
                reason=" + ".join(reasons) + " → DUAL_FUSE",
                confidence=0.6, risk_level=max(risk, RiskLevel.MEDIUM),
                estimated_savings=0.0,
            )

        # --- SEÑAL 4: Prompt largo + ambigüedad → DUAL_FUSE ---
        is_long = len(prompt) > LONG_PROMPT_CHARS
        is_complex = bool(STEP_BY_STEP_PATTERN.search(prompt))
        if is_long and is_complex:
            reasons.append(f"Prompt largo ({len(prompt)} chars) + patrón complejo")
            return RoutingDecision(
                topic=topic, mode=RoutingMode.DUAL_FUSE, providers=ordered,
                reason=" + ".join(reasons) + " → DUAL_FUSE",
                confidence=0.6, risk_level=max(risk, RiskLevel.MEDIUM),
                estimated_savings=0.0,
            )

        # --- BUDGET GUARD: si excede cap diario → forzar SINGLE ---
        if self._budget_exceeded():
            reasons.append("Budget diario excedido")

        # --- LATENCY GUARD: si P95 del secundario es muy alto → no vale DUAL ---
        secondary = ordered[1] if len(ordered) > 1 else None
        if secondary and self._latency_too_high(secondary, profile):
            reasons.append(f"P95 latencia {secondary} excede cap")

        # --- DEFAULT: SINGLE (el dominante) ---
        win_rate = profile.weights.get(dominant, 0.5)
        conf = round(max(0.5, win_rate), 4)

        if not reasons:
            reasons.append(f"{dominant} dominante (win={win_rate:.3f}, err={dominant_err:.2f})")

        return RoutingDecision(
            topic=topic,
            mode=RoutingMode.SINGLE,
            providers=ordered,
            reason=" + ".join(reasons) + " → SINGLE",
            confidence=conf,
            risk_level=risk,
            estimated_savings=1.0,
        )

    # -- 8. Auto-aprendizaje (after hook) --------------------------------

    def auto_learn(self, topic: str, results: List[Dict], divergence: float):
        """
        Actualiza pesos bayesianos automáticamente después de cada request.
        Sin intervención humana. Deja rastro vía updated_at en provider_weights.
        """
        with self._db() as conn:
            for r in results:
                provider = r["provider"]
                has_error = bool(r.get("error"))
                content_ok = bool((r.get("content") or "").strip()) and not has_error
                latency = int(r.get("latency_ms", 0))

                # Asegurar que existe
                conn.execute(
                    "INSERT INTO provider_weights(provider, topic, alpha, beta) "
                    "VALUES(?, ?, 1.0, 1.0) ON CONFLICT(provider, topic) DO NOTHING",
                    (provider, topic)
                )

                delta_alpha = 0.0
                delta_beta  = 0.0

                # Éxito / fallo
                if content_ok:
                    delta_alpha += LEARN_ALPHA_SUCCESS
                else:
                    delta_beta += LEARN_BETA_FAILURE

                # Divergencia (solo en modo dual)
                if len(results) > 1:
                    if divergence < 0.15:
                        delta_alpha += LEARN_ALPHA_LOW_DIV
                    elif divergence > 0.4:
                        delta_beta += LEARN_BETA_HIGH_DIV

                # Latencia comparativa (solo dual)
                if len(results) == 2 and content_ok:
                    other = [x for x in results if x["provider"] != provider]
                    if other:
                        other_lat = int(other[0].get("latency_ms", 0))
                        if other_lat > 0 and latency > other_lat * LATENCY_SLOW_RATIO:
                            delta_beta += LEARN_BETA_SLOW

                # Aplicar deltas
                if delta_alpha > 0 or delta_beta > 0:
                    conn.execute(
                        "UPDATE provider_weights "
                        "SET alpha = alpha + ?, beta = beta + ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE provider = ? AND topic = ?",
                        (delta_alpha, delta_beta, provider, topic)
                    )

            conn.commit()

    # -- Entry point público --------------------------------------------

    def route(self, prompt: str, explicit_topic: Optional[str] = None) -> RoutingDecision:
        # Kill switch primero
        ks = self._kill_switch()
        if ks:
            if explicit_topic and explicit_topic.strip().lower() not in ("", "general"):
                ks.topic = explicit_topic.strip().lower()
            else:
                ks.topic, _ = self.detect_topic(prompt)
            return ks

        # Detección de tópico
        if explicit_topic and explicit_topic.strip().lower() not in ("", "general"):
            topic = explicit_topic.strip().lower()
            topic_confidence = 1.0
        else:
            topic, topic_confidence = self.detect_topic(prompt)

        profile  = self.evaluate_history(topic)
        decision = self.decide(topic, profile, prompt)

        decision.reason = f"[topic_conf={topic_confidence:.2f}] {decision.reason}"

        return decision
