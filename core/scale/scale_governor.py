"""
core/scale/scale_governor.py — Orchestrator middleware ANTES de Cognition.

Selecciona el pipeline mínimo según intent + tier + carga + coste.
Coordina:
  - clasificación de complejidad (classify_complexity)
  - políticas (PolicyConfig por tier+profile)
  - rate limiting
  - load shedding
  - semantic cache
  - prevención de cascada de fallos

Flujo recomendado por el llamador:

    sg = ScaleGovernor()
    decision = sg.select_minimal_pipeline(user_id, tier, intent, message)
    if decision.cache_hit:
        return decision.cached_response
    if decision.rate_limited:
        return decision.rate_limit_message
    # ... ejecutar el pipeline limitado por decision.policy ...
    sg.semantic_cache_set(decision.cache_key, response)

Diseño:
  - NO mantiene estado per-request más allá del cache (que es global).
  - thread-safe.
  - never raises: ante cualquier fallo interno, devuelve un fallback
    seguro (perfil MINIMAL).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from core.scale.pipeline_policy import (
    PipelineProfile,
    Tier,
    PolicyConfig,
    get_policy_for,
)
from core.scale.rate_limiter import RateLimiter, get_default_rate_limiter
from core.scale.load_shedder import LoadShedder, LoadLevel

logger = logging.getLogger("vectrax.scale.scale_governor")


# ===========================================================================
# Mapa intent → PipelineProfile
# ===========================================================================

# Categorías declarativas (siguen el spec del creador)
_PROFILE_BY_INTENT: Dict[str, PipelineProfile] = {
    # MINIMAL
    "greeting": PipelineProfile.MINIMAL,
    "smalltalk": PipelineProfile.MINIMAL,
    "casual": PipelineProfile.MINIMAL,
    "thanks": PipelineProfile.MINIMAL,
    "farewell": PipelineProfile.MINIMAL,

    # STANDARD
    "query": PipelineProfile.STANDARD,
    "memory_lookup": PipelineProfile.STANDARD,
    "memory_recall": PipelineProfile.STANDARD,
    "memory_save": PipelineProfile.STANDARD,
    "normal_chat": PipelineProfile.STANDARD,
    "identity": PipelineProfile.STANDARD,
    "reminder": PipelineProfile.STANDARD,
    "pragmatic": PipelineProfile.STANDARD,

    # ADVANCED
    "image": PipelineProfile.ADVANCED,
    "audio": PipelineProfile.ADVANCED,
    "analysis": PipelineProfile.ADVANCED,
    "technical": PipelineProfile.ADVANCED,
    "planning": PipelineProfile.ADVANCED,
    "creative": PipelineProfile.ADVANCED,

    # MISSION_CRITICAL
    "legal": PipelineProfile.MISSION_CRITICAL,
    "contract": PipelineProfile.MISSION_CRITICAL,
    "financial": PipelineProfile.MISSION_CRITICAL,
    "medical": PipelineProfile.MISSION_CRITICAL,
    "deploy": PipelineProfile.MISSION_CRITICAL,
    "privileged_action": PipelineProfile.MISSION_CRITICAL,
    "irreversible_action": PipelineProfile.MISSION_CRITICAL,
    "crisis": PipelineProfile.MISSION_CRITICAL,
}

# Heurísticas adicionales sobre el contenido del mensaje (cuando intent
# es genérico o desconocido). Buscan palabras clave de alto riesgo.
_MISSION_CRITICAL_HINTS = re.compile(
    r"\b(?:contrato|contract|legal|lawsuit|demanda|tribunal|abogado|"
    r"medic[ao]|m[eé]dico|diagn[oó]stico|cirug[ií]a|prescripci[oó]n|"
    r"financ(?:iero|ial)|invertir\s+\$?\d+|transferir|transferencia\s+grand|"
    r"deploy|production|producci[oó]n|delete|drop\s+table|"
    r"irrevers|deploy\s+to\s+prod)\b",
    re.IGNORECASE,
)
_ADVANCED_HINTS = re.compile(
    r"\b(?:analiza|compara|expl[ií]ca|describe|planea|brainstorm|"
    r"analyze|compare|explain|describe|plan|debug|architectur|"
    r"foto|imagen|audio|voice|image)\b",
    re.IGNORECASE,
)


# ===========================================================================
# Resultado del select_minimal_pipeline
# ===========================================================================

@dataclass
class PipelineDecision:
    user_id: str
    tier: Tier
    intent: str
    profile: PipelineProfile
    policy: PolicyConfig
    load_level: LoadLevel
    chosen_model: str
    rate_limited: bool = False
    rate_limit_message: str = ""
    cache_hit: bool = False
    cached_response: Optional[str] = None
    cache_key: str = ""
    upgrade_hint: Optional[str] = None       # texto opcional para FREE en MISSION_CRITICAL
    reason_chain: list = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.value,
            "intent": self.intent,
            "profile": self.profile.value,
            "load_level": self.load_level.value,
            "model": self.chosen_model,
            "max_engines": self.policy.max_engines,
            "rate_limited": self.rate_limited,
            "cache_hit": self.cache_hit,
            "reason": " | ".join(self.reason_chain),
        }


# ===========================================================================
# Semantic cache (in-memory LRU + TTL)
# ===========================================================================

@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


# ===========================================================================
# ScaleGovernor
# ===========================================================================

class ScaleGovernor:

    DEFAULT_CACHE_SIZE = 1024
    # TTL del mensaje de upgrade cacheado por usuario. Bajo ráfaga, el
    # rate-limit dispara en CADA mensaje tras agotar el burst; sin cache eso
    # llamaría a la API de Stripe una vez por mensaje. 30 min << 24h de
    # validez del Checkout Session, así que el enlace sigue siendo válido.
    UPGRADE_MSG_TTL = 1800.0

    def __init__(
        self,
        rate_limiter: Optional[RateLimiter] = None,
        load_shedder: Optional[LoadShedder] = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        self._rate_limiter = rate_limiter or get_default_rate_limiter()
        self._load_shedder = load_shedder or LoadShedder()
        self._cache: "OrderedDict[str, _CacheEntry]" = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_size = cache_size
        # Cache de mensajes de upgrade: user_id → (mensaje, expires_at).
        # Solo se cachea cuando el enlace de Stripe se generó correctamente,
        # de modo que un fallo transitorio de Stripe se auto-recupere en el
        # siguiente throttle en lugar de quedar fijado sin enlace.
        self._upgrade_msg_cache: Dict[str, Tuple[str, float]] = {}
        self._upgrade_msg_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 1. Classification
    # ------------------------------------------------------------------

    def classify_complexity(self, intent: str, message: str) -> PipelineProfile:
        """Mapea (intent, message) → PipelineProfile.

        1) Mira el mapa intent → profile.
        2) Si el message contiene hints MISSION_CRITICAL, eleva a ese.
        3) Si tiene hints ADVANCED y el profile actual era ≤ STANDARD,
           sube a ADVANCED.
        4) Default: STANDARD si nada matchea.
        """
        intent_norm = (intent or "").strip().lower()
        msg = message or ""

        # Override duro: hints de mission_critical en el mensaje
        if _MISSION_CRITICAL_HINTS.search(msg):
            return PipelineProfile.MISSION_CRITICAL

        profile = _PROFILE_BY_INTENT.get(intent_norm)
        if profile is None:
            # intent desconocido → inferir por contenido
            if _ADVANCED_HINTS.search(msg):
                profile = PipelineProfile.ADVANCED
            else:
                profile = PipelineProfile.STANDARD

        # Promote STANDARD → ADVANCED si hay hints
        if profile in (PipelineProfile.MINIMAL, PipelineProfile.STANDARD):
            if _ADVANCED_HINTS.search(msg):
                profile = PipelineProfile.ADVANCED

        return profile

    # ------------------------------------------------------------------
    # 2. Pipeline selection (entry point)
    # ------------------------------------------------------------------

    def select_minimal_pipeline(
        self,
        user_id: str,
        tier: Tier,
        intent: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PipelineDecision:
        """Selecciona el pipeline mínimo viable para esta request.

        Pipeline interno:
          1. Rate limit check (no consume aún).
          2. Cache lookup.
          3. Classify complexity → profile.
          4. Get policy by (tier, profile).
          5. Load level → degrade policy.
          6. Choose model.
          7. Consume token from rate limiter.
        """
        reasons = []
        try:
            tier = self._normalize_tier(tier)

            # 1. Rate limit
            if not self._rate_limiter.allow(user_id, tier):
                limits = self._rate_limiter.get_limits_for_tier(tier)
                reasons.append(f"rate_limited(tier={tier.value})")
                return PipelineDecision(
                    user_id=user_id,
                    tier=tier,
                    intent=intent,
                    profile=PipelineProfile.MINIMAL,
                    policy=get_policy_for(tier, PipelineProfile.MINIMAL),
                    load_level=LoadLevel.NORMAL,
                    chosen_model="cheap",
                    rate_limited=True,
                    rate_limit_message=self._build_rate_limit_message(
                        user_id, limits.daily_limit,
                    ),
                    reason_chain=reasons,
                )

            # 2. Cache lookup
            cache_key = self.build_cache_key(user_id, intent, message)
            cached = self.semantic_cache_get(cache_key)
            if cached is not None:
                reasons.append("cache_hit")
                # Cache hit → minimal profile, no consume rate limit
                return PipelineDecision(
                    user_id=user_id,
                    tier=tier,
                    intent=intent,
                    profile=PipelineProfile.MINIMAL,
                    policy=get_policy_for(tier, PipelineProfile.MINIMAL),
                    load_level=LoadLevel.NORMAL,
                    chosen_model="cheap",
                    cache_hit=True,
                    cached_response=cached,
                    cache_key=cache_key,
                    reason_chain=reasons,
                )

            # 3. Classify complexity
            profile = self.classify_complexity(intent, message)
            reasons.append(f"profile={profile.value}")

            # 4. Get policy
            policy = get_policy_for(tier, profile)

            # Upgrade hint si FREE en MISSION_CRITICAL
            upgrade_hint = None
            if (
                tier == Tier.FREE
                and profile == PipelineProfile.MISSION_CRITICAL
            ):
                upgrade_hint = (
                    "Esta consulta es de alta complejidad. La versi\u00f3n "
                    "completa requiere PRO. Te respondo con la versi\u00f3n "
                    "limitada gratuita."
                )
                reasons.append("free_downgrade")

            # 5. Load shedder
            load_level = self._load_shedder.get_load_level()
            if load_level != LoadLevel.NORMAL:
                policy = self._load_shedder.degrade_pipeline(policy, load_level)
                reasons.append(f"load={load_level.value}")

            # 6. Model
            chosen_model = self._load_shedder.choose_model(profile, load_level)

            # 7. Consume token
            self._rate_limiter.consume(user_id, tier, tokens=1)

            return PipelineDecision(
                user_id=user_id,
                tier=tier,
                intent=intent,
                profile=profile,
                policy=policy,
                load_level=load_level,
                chosen_model=chosen_model,
                cache_key=cache_key,
                upgrade_hint=upgrade_hint,
                reason_chain=reasons,
            )

        except Exception as exc:
            logger.warning("select_minimal_pipeline fallback: %s", exc)
            # Safe fallback: minimal everything
            safe_tier = tier if isinstance(tier, Tier) else Tier.FREE
            return PipelineDecision(
                user_id=user_id or "anon",
                tier=safe_tier,
                intent=intent or "casual",
                profile=PipelineProfile.MINIMAL,
                policy=get_policy_for(safe_tier, PipelineProfile.MINIMAL),
                load_level=LoadLevel.NORMAL,
                chosen_model="cheap",
                reason_chain=["fallback_due_to_error"],
            )

    # ------------------------------------------------------------------
    # 3. Cascade prevention
    # ------------------------------------------------------------------

    def prevent_engine_cascade(
        self,
        failed_engine: str,
        pipeline_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evita que un fallo encadene reintentos de todo el pipeline.

        Args:
          failed_engine: nombre del motor que fall\u00f3.
          pipeline_state: estado actual (qu\u00e9 motores corrieron, qu\u00e9
            resultados parciales se tienen).

        Returns:
          Plan de recuperaci\u00f3n con:
            - 'use_fallback': bool (True = usar fallback inmediato)
            - 'fallback_reason': str
            - 'remaining_engines': list[str] (los que NO se deben re-ejecutar)
            - 'partial_response': str|None (lo que ya se ten\u00eda v\u00e1lido)
        """
        partial = (pipeline_state or {}).get("partial_response")
        completed = list((pipeline_state or {}).get("completed_engines", []))

        # Regla 1: si hay respuesta parcial v\u00e1lida, devolverla.
        if partial and isinstance(partial, str) and len(partial.strip()) > 0:
            return {
                "use_fallback": False,
                "fallback_reason": (
                    f"engine '{failed_engine}' failed but partial "
                    f"response from {completed} is usable"
                ),
                "remaining_engines": [],
                "partial_response": partial,
            }

        # Regla 2: NO reintentar todo el pipeline. Pedir al caller que use
        # un fallback inmediato (constructed reply o mensaje corto humano).
        return {
            "use_fallback": True,
            "fallback_reason": (
                f"engine '{failed_engine}' failed; using immediate fallback "
                f"(no cascade retry)"
            ),
            "remaining_engines": [],
            "partial_response": None,
        }

    # ------------------------------------------------------------------
    # 4. Semantic cache
    # ------------------------------------------------------------------

    def build_cache_key(
        self,
        user_id: str,
        intent: str,
        message: str,
    ) -> str:
        """Cache key estable: SHA256(user|intent|normalized_message)[:32]."""
        norm = (message or "").strip().lower()
        raw = f"{user_id}|{(intent or '').lower()}|{norm}"
        return "sc:" + hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:32]

    def semantic_cache_get(self, cache_key: str) -> Optional[Any]:
        """Returns cached value if present and not expired, else None."""
        if not cache_key:
            return None
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry is None:
                return None
            if entry.expires_at < time.time():
                # Expired
                try:
                    del self._cache[cache_key]
                except KeyError:
                    pass
                return None
            # LRU touch
            self._cache.move_to_end(cache_key)
            return entry.value

    def semantic_cache_set(
        self,
        cache_key: str,
        value: Any,
        ttl: int = 300,
    ) -> None:
        """Stores value with TTL (seconds). Bounded LRU."""
        if not cache_key or value is None:
            return
        with self._cache_lock:
            self._cache[cache_key] = _CacheEntry(
                value=value,
                expires_at=time.time() + max(1, ttl),
            )
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def cache_clear(self) -> None:
        """Test/admin helper."""
        with self._cache_lock:
            self._cache.clear()
        with self._upgrade_msg_lock:
            self._upgrade_msg_cache.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_tier(t: Any) -> Tier:
        if isinstance(t, Tier):
            return t
        try:
            return Tier(str(t).upper())
        except Exception:
            return Tier.FREE

    def _build_rate_limit_message(self, user_id: str, daily_limit: int) -> str:
        """Mensaje de rate-limit que incluye el enlace de upgrade.

        Reutiliza core.operator.user_tiers._build_limit_message (la misma
        fuente que el límite por tier) para no duplicar la lógica de Stripe:
        genera un Checkout Session y, si Stripe no está disponible, cae a la
        instrucción /upgrade.

        Memoiza por usuario (UPGRADE_MSG_TTL) para no golpear la API de Stripe
        en cada mensaje throttled. Solo cachea cuando el enlace real se generó
        (contiene URL), de modo que un fallo transitorio de Stripe se reintente
        en el siguiente throttle. Nunca lanza.
        """
        now = time.time()
        # 1) Servir desde cache si sigue vigente.
        with self._upgrade_msg_lock:
            cached = self._upgrade_msg_cache.get(user_id)
            if cached is not None and cached[1] > now:
                return cached[0]

        # 2) Construir (fuera del lock: la llamada a Stripe puede tardar).
        try:
            from core.operator.user_tiers import _build_limit_message
            message = _build_limit_message(user_id, daily_limit, daily_limit)
        except Exception as exc:
            logger.debug("rate_limit upgrade message fallback: %s", exc)
            return (
                f"Has alcanzado tu l\u00edmite diario "
                f"({daily_limit} mensajes/d\u00eda). "
                f"Escribe /upgrade para activar acceso extendido."
            )

        # 3) Cachear solo si se generó el enlace real (auto-recuperación ante
        #    fallo transitorio de Stripe).
        if "http" in message:
            with self._upgrade_msg_lock:
                self._upgrade_msg_cache[user_id] = (
                    message, now + self.UPGRADE_MSG_TTL,
                )
        return message
