"""
core/llm_call.py — Llamada LLM directa, síncrona y context-agnostic.

Punto único de invocación al proveedor (OpenAI directo, vía httpx de un solo
tiro). Funciona tanto en contexto síncrono (worker de Telegram) como llamado
síncronamente dentro de un `async def` (endpoint FastAPI), a diferencia del
Intelligence Bridge, que es sync-only (rehúsa correr dentro de un event loop).

Objetivo: centralizar la lógica de "OpenAI directo" que hoy está duplicada
(`external_gateway._generate_openai_direct`, `self_context`) y hacerla
consumible por CUALQUIER módulo (p. ej. el Motor de Criterio) sin depender del
bridge ni de un event loop.

Diseño:
  - Defensivo: NUNCA lanza; siempre devuelve un `LLMResult`.
  - No-circular: es una llamada a proveedor, no un evaluador. El grounding /
    preservación / polaridad del criterio siguen viviendo en su propio módulo.
  - Preserva las salvaguardas existentes: identidad (`effective_system_prompt`
    → `VECTRAX_SYSTEM_PROMPT`), `api_gate` (backoff 429) y `external_call_guard`
    (circuit breaker). No loguea texto crudo (solo status/latencia).
  - Import de bajo nivel: NO importa `external_gateway` ni `criterion` (evita
    ciclos); esos módulos importan a este.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger("vectrax.llm_call")

_DEFAULT_MODEL = "gpt-4o-mini"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Estados posibles de una llamada. Permiten al caller distinguir "no disponible"
# (no se intentó/atribuible al entorno) de "se llamó pero salió mal".
LLMStatus = Literal[
    "ok",            # respuesta con contenido
    "no_key",        # OPENAI_API_KEY ausente (no disponible)
    "gate_closed",   # api_gate cerrado por backoff 429 (no disponible)
    "circuit_open",  # circuit breaker abierto (no disponible)
    "empty",         # HTTP 200 pero contenido vacío (intentado, sin texto)
    "http_error",    # 429/4xx/5xx (intentado, falló)
    "exception",     # red/parseo/timeout (intentado, falló)
]

# Estados en los que el LLM NO estaba disponible (no cuenta como intento fallido).
UNAVAILABLE: frozenset = frozenset({"no_key", "gate_closed", "circuit_open"})


@dataclass(frozen=True)
class LLMResult:
    """Resultado de una llamada LLM. `text` solo es no vacío cuando `ok`."""
    ok: bool
    text: str
    status: LLMStatus
    provider: str = "openai"
    model: str = _DEFAULT_MODEL
    error: Optional[str] = None

    @property
    def available(self) -> bool:
        """False si el proveedor no estaba disponible (no un fallo de render)."""
        return self.status not in UNAVAILABLE


def _model() -> str:
    return os.environ.get("VX_LLM_MODEL", "").strip() or _DEFAULT_MODEL


def _resolve_timeout(default: float) -> float:
    raw = os.environ.get("VX_LLM_TIMEOUT", "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return default


def _guard_success(latency_ms: float) -> None:
    try:
        from core.external_call_guard import _record_success
        _record_success("openai", latency_ms)
    except Exception:
        pass


def _guard_failure(error: str, is_timeout: bool = False) -> None:
    try:
        from core.external_call_guard import _record_failure
        _record_failure("openai", error, is_timeout=is_timeout)
    except Exception:
        pass


def complete(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    max_tokens: int = 500,
    temperature: float = 0.7,
    timeout: float = 30.0,
) -> LLMResult:
    """Llamada LLM directa (OpenAI, httpx sync). Defensiva; NUNCA lanza.

    Args:
        prompt: contenido del turno de usuario.
        system_prompt: si es None se usa `effective_system_prompt()` (identidad
            soberana VECTRAX_SYSTEM_PROMPT). Un valor explícito lo sobreescribe.
        max_tokens / temperature / timeout: parámetros de generación.

    Returns:
        LLMResult(ok, text, status, provider, model, error). `text` no vacío solo
        cuando `status == "ok"`.
    """
    model = _model()

    # 1) API key
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return LLMResult(False, "", "no_key", model=model)

    # 2) API gate (backoff 429) — best-effort
    try:
        from core.api_gate import check_gate
        if not check_gate("openai"):
            logger.debug("llm_call: gate closed (openai)")
            return LLMResult(False, "", "gate_closed", model=model)
    except Exception:
        pass

    # 3) Circuit breaker — best-effort
    try:
        from core.external_call_guard import _check_circuit
        if not _check_circuit("openai"):
            logger.debug("llm_call: circuit open (openai)")
            return LLMResult(False, "", "circuit_open", model=model)
    except Exception:
        pass

    # 4) System prompt — identidad soberana por defecto
    try:
        from vectrax.core_identity import effective_system_prompt
        system = effective_system_prompt(system_prompt)
    except Exception:
        system = (system_prompt or "").strip()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # 5) Llamada
    t0 = time.perf_counter()
    try:
        import httpx
        resp = httpx.post(
            _OPENAI_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_resolve_timeout(timeout),
        )
    except Exception as exc:
        is_timeout = "Timeout" in type(exc).__name__
        _guard_failure(f"{type(exc).__name__}: {exc}", is_timeout=is_timeout)
        logger.debug("llm_call: request exception: %s", exc)
        return LLMResult(False, "", "exception", model=model, error=str(exc)[:200])

    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 429 — cerrar gate y registrar fallo
    if resp.status_code == 429:
        try:
            from core.api_gate import record_429
            record_429("openai")
        except Exception:
            pass
        _guard_failure("HTTP 429")
        return LLMResult(False, "", "http_error", model=model, error="429")

    if resp.status_code >= 400:
        _guard_failure(f"HTTP {resp.status_code}")
        return LLMResult(
            False, "", "http_error", model=model, error=f"HTTP {resp.status_code}",
        )

    # HTTP 200 → éxito de llamada (resetea backoff y circuit), aun si vacío
    try:
        from core.api_gate import record_success
        record_success("openai")
    except Exception:
        pass
    _guard_success(latency_ms)

    try:
        data = resp.json()
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        logger.debug("llm_call: parse error: %s", exc)
        return LLMResult(False, "", "exception", model=model, error=str(exc)[:200])

    if not content:
        return LLMResult(False, "", "empty", model=model)

    logger.debug("llm_call: ok | provider=openai | model=%s | %.0fms", model, latency_ms)
    return LLMResult(True, content, "ok", model=model)
