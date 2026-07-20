# Criterion LLM Path — `core/llm_call` — Validation & Closure Report

- **Task:** Investigar por qué el bridge LLM no se invocaba desde Telegram (el criterion siempre caía a determinista) e implementar un util compartido que habilite la presentación por LLM en producción, sin depender del bridge sync-only.
- **Delivered:** 2026-07-20 · **Status:** ✅ Closed (diagnosticado, implementado, testeado, desplegado, verificado en vivo, documentado)
- **Branches/merges:** `feat/llm-call-shared-util` → PR #45 → `main` (`1cb0d74`); `feat/self-context-llm-call-unify` → PR #47 → `main` (`7b78ef6`)
- **Deploy:** producción (Vultr, `vectrax-core`) en `1cb0d74`

## 1. Diagnóstico (causa raíz)
La presentación LLM del Motor de Criterio nunca corría en producción: el gate exponía `origin=deterministic | attempted=False`.
- El `intelligence_bridge` es **sync-only** (rehúsa correr dentro de un event loop; `initialize()` bail-ea con "running event loop detected").
- El único punto que inicializa el bridge es `external_gateway._generate_cognitive_response`, que es el **último recurso** del pipeline y casi nunca se alcanza (los resolvers upstream —memoria, criterion, self-aware, nucleus— resuelven antes y cortocircuitan).
- `criterion._call_llm` solo comprobaba `is_ready()` (nunca `initialize()`) y corría **antes** de ese punto → siempre `is_ready()=False` → determinista.
- Evidencia en logs vivos: `Intelligence Router initialized`=0, `LLM response via`=0; el único LLM que disparó fue `self_context — Self-aware response via OpenAI direct` (ruta propia, no el bridge).

## 2. Solución
Nuevo **`core/llm_call.py`** — `complete(prompt, *, system_prompt=None, max_tokens=500, temperature=0.7, timeout=30.0) -> LLMResult`:
- **Síncrono y context-agnostic** (httpx de un solo tiro): sirve en el worker sync de Telegram y dentro del endpoint `async` de FastAPI, sin depender del bridge.
- **Defensivo** (nunca lanza). `LLMResult(ok, text, status, provider, model, error)` con `status ∈ {ok, no_key, gate_closed, circuit_open, empty, http_error, exception}` y `available` (distingue "no disponible" de "intento fallido").
- Preserva salvaguardas: identidad `effective_system_prompt` → `VECTRAX_SYSTEM_PROMPT`, `api_gate` (backoff 429), `external_call_guard` (circuit breaker). No loguea texto crudo. Overrides `VX_LLM_MODEL` / `VX_LLM_TIMEOUT`.

Integración (reemplaza llamadas directas):
- **`criterion._call_llm`** usa `llm_call.complete` como camino de producción (deja el bridge). Mapea `available`/`status` → `(attempted, text, failure_reason)` del contrato `RenderAttempt`. La presentación LLM pasa igual por grounding + preservación + polaridad; el determinista sigue de respaldo.
- **`external_gateway._generate_openai_direct`** (worker de Telegram) pasa a ser wrapper delgado sobre `llm_call.complete` (misma firma y contrato `""`); centraliza identidad + api_gate + circuit.

## 3. Tests
- `tests/test_llm_call.py`: **11** (cada `LLMStatus`; system prompt soberano; override explícito; overrides de env; "nunca lanza").
- `tests/test_criterion.py`: fixture autouse **offline** (hermético sin red aunque haya `OPENAI_API_KEY`) + **4** de integración con `complete` (`llm_rendered` / unavailable→`attempted=False` / `empty` / `http_error`).
- **Suite completa: 3092 passed, 1 skipped, 0 failed.**

## 4. Validación en vivo (2026-07-20, prod)
Ejercido dentro de `vectrax-core` con un `user_id` `test:` (excluido de stats), mismo `receive_message` que usa `POST /v1/gateway/message`. Consulta: `¿Qué opinas del mercado según lo aprendido?`

- `PROCESSED: True`
- **Log del gate:** `Pipeline: DOMAIN-CRITERION gate | domain=market | origin=llm_rendered | attempted=True | grounding=True | preservation=True | reason=None | user=test:oz-llmcall`
- **Respuesta (grounded, LLM-rendered):** *"He observado 8 patrones sobre 1315 observaciones en el mercado. Mi posición se basa en AAPL, que muestra una expectativa de +16.03, WR 100% y LB 98% sobre 221 observaciones. TSLA y NVDA también presentan resultados similares, pero no las elijo por separado…"*

Antes: `origin=deterministic | attempted=False`. **Ahora: `origin=llm_rendered | attempted=True`** — la presentación LLM se dispara y pasa las tres verificaciones. Fix confirmado end-to-end.

## 5. Despliegue
- `/v1/health` **ok**, **48 motores**, governor `act` ("Nominal — all systems healthy"). Contenedor healthy, restart `unless-stopped`.

## 6. Límites conocidos / follow-ups
- El criterion ahora hace una llamada LLM real en prod para consultas de dominio/opinión (+tokens/latencia); el determinista es el respaldo ante cualquier fallo.
- `httpx.post` sync bloquea brevemente el event loop dentro del endpoint async (igual que antes).
- El `intelligence_bridge` queda como legado del último-recurso del gateway (no eliminado).
- Follow-up: **✅ Completado** — `self_context.resolve_self_aware` (narrador self-aware) unificado sobre `core.llm_call` (PR #47, deploy `7b78ef6`; validado en vivo: `Self-aware response via OpenAI direct`). Ya no quedan llamadas LLM directas duplicadas en el pipeline (criterion, gateway y self_context van todas por `core/llm_call`).

## 7. Estado
- **Cerrado.** Causa raíz identificada y resuelta; util compartido implementado, testeado (suite 3095 passed), desplegado y verificado en vivo (`origin=llm_rendered`). Unificación de `self_context` completada (PR #47). Documentado en `README.md` (“🔌 Capa de llamada LLM compartida”). **Sin ítems bloqueantes ni follow-ups abiertos.**

## Referencias
- Código: `core/llm_call.py`, `core/learn/criterion.py`, `core/operator/external_gateway.py`.
- Tests: `tests/test_llm_call.py`, `tests/test_criterion.py`.
- Docs: `README.md` → "📋 Changelog" (`### 2026-07-20`); contrato en `docs/CRITERION_CONTRACT_VALIDATION_2026_07_20.md`.
- PR: #45. Deploy: `1cb0d74`.
