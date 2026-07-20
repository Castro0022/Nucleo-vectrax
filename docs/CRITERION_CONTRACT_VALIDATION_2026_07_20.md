# Motor de Criterio — Contrato + Polaridad/Origin — Validation & Closure Report

- **Task:** Cerrar el ticket de validación del endurecimiento del Motor de Criterio: contrato de datos con origen trazable (`CriterionResult`), jerarquía determinista-primero, guarda de polaridad/negación y exposición de `origin` en el gateway. Validar en producción tras el despliegue.
- **Delivered:** 2026-07-20 · **Status:** ✅ Closed (implementado, testeado, desplegado, monitoreado, ejercido en vivo, documentado)
- **Branches/merges:** `feat/criterion-result-contract` → PR #41 → `main`; `feat/criterion-polarity-and-origin` → PR #42 → `main` (`2ec4e08`); `docs/release-notes-2026-07-20` → PR #43 → `main` (`8fcc54a`)
- **Release:** `v2026.07.20-criterion-contract` (GitHub Release, no-draft, sobre `main`)

## 1. Alcance de la validación
Confirmar en producción que:
1. La formación del criterio es **determinista y previa al LLM**; la presentación por LLM es opcional y **sin autoridad decisional**.
2. El contrato `CriterionResult` / `RenderAttempt` expone un **origen trazable** (`deterministic` / `llm_rendered` / `insufficient_evidence`) con invariantes estrictas.
3. La guarda de polaridad/negación cierra el gap de "negación que conserva ancla y cifras".
4. El gate del gateway (STEP 4.2a3) expone `origin` en logs con **solo campos abstractos** (nunca texto crudo).
5. El despliegue no introduce regresiones ni anomalías.

## 2. Cambios entregados
- **PR #41 — contrato + determinista-primero:** `CriterionResult(origin, deterministic_conclusion, rendered_text, render_attempt, timestamp)` con propiedad `text` e invariantes (`origin="llm_rendered"` **iff** intento + grounding + preservación con texto no vacío). `RenderAttempt(attempted, grounding_passed, preservation_passed, failure_reason, rejected_text)` con tabla de verdad `empty`/`llm_error`/`not_grounded`/`conclusion_altered` (`None` = no evaluado). `build_criterion_result(...) -> CriterionResult`; `build_criterion(...) -> str` se mantiene (delega en `.text`). Prompt abierto reemplazado por `_presentation_prompt` (decisión cerrada) + `_call_llm`.
- **PR #42 — polaridad + origin:** `_introduces_polarity_flip` (léxico, determinista, no-circular) + `_preserves_conclusion` fail-closed en el ancla; regex de recomendación cubre raíces `recomiend-`/`recomend-` y `sugier-`/`suger-`. Gateway expone `origin` vía `build_criterion_result` (`_domain_source="criterion:{origin}"` + log abstracto).

## 3. Tests
- `tests/test_criterion.py`: **33** (contrato; una rama por cada estado de `RenderAttempt.__post_init__`; invariantes de `CriterionResult`; 4 caminos de falla; gaps de regex `recomend-`/`suger-`; ancla fail-closed; guarda de polaridad con control afirmativo).
- `tests/test_external_gateway.py`: precedencia del gate (repunteada a `build_criterion_result`).
- **Suite completa: 3077 passed, 1 skipped, 0 failed.**

## 4. Despliegue (producción, Vultr)
- `vectrax-core` en `2ec4e08` (luego `8fcc54a` con las release notes): `/v1/health` **ok**, **48 motores**, governor `act` ("Nominal — all systems healthy"), restart `unless-stopped`.

## 5. Monitoreo post-deploy — sin anomalías
- Arranque limpio: supervisor **restart #0** en `pipeline_worker`/`core_api`/`meta_loop`/`audit_cron`; watchdog 60s + heartbeat 10s activos.
- **0** errores/tracebacks; **0** `domain criterion gate failed (passthrough)`.
- Ciclo de mercado eToro respondiendo **200 OK** (~200ms).
- Verificación en el intérprete desplegado: símbolos del contrato presentes (`build_criterion_result`, `_introduces_polarity_flip`, `CriterionResult`, `RenderAttempt`) y smoke `build_criterion_result("__nope__", "x")` → `origin=insufficient_evidence`, `attempted=False`.

## 6. Ejercicio en vivo del gate (2026-07-20)
Ejecutado dentro de `vectrax-core` con un `user_id` `test:` (excluido de stats por prefijo `test:`), mismo `receive_message` que usa `POST /v1/gateway/message`:

Consulta: `¿Qué opinas del mercado según lo que has aprendido?`

- `PROCESSED: True`
- **Log del gate (exposición de `origin`, solo campos abstractos):**
  `Pipeline: DOMAIN-CRITERION gate | domain=market | origin=deterministic | attempted=False | grounding=None | preservation=None | reason=None | user=test:oz-postdeploy`
- **Respuesta (grounded):** *"…de lo observado en market la posición que me emerge se apoya en «AAPL» (expectancy +16.03, WR 100%, LB 98% sobre 221 obs, confianza HIGH, masa 212 activaciones)…"*

Interpretación: caso `deterministic` con `attempted=False` (el bridge LLM no está listo en ese proceso → directo al determinista), **exactamente conforme al contrato**. El log no contiene texto crudo (privacidad/métricas abstractas). La opinión cita evidencia real sin fabricar.

## 7. Límites conocidos y nota menor
- **Preservación léxica, no semántica:** la guarda (blocklist de recomendación + polaridad/negación) es la **primera línea de defensa**, no una garantía semántica total. Ante cualquier duda, la salida autorizada es la conclusión determinista.
- **Cosmético (pre-existente, ajeno a este deploy):** `extract_topic_tokens` tomó `"has"` (de "has aprendido") como token de tema (*"Sobre «has» todavía no tengo evidencia directa…"*). La conjugación de *haber* no está en `_STOPWORDS`. El comportamiento de fondo es correcto (opina desde market, cita métricas, grounded). Follow-up opcional: añadir `has/he/ha/han/había/hemos/…` a `_STOPWORDS`.

## 8. Estado
- **Cerrado.** Contrato, jerarquía determinista-primero, guarda de polaridad y exposición de `origin` implementados, testeados (3077 passed), desplegados, monitoreados sin anomalías y ejercidos en vivo. Sin ítems bloqueantes abiertos; un follow-up cosmético opcional anotado.

## Referencias
- Código: `core/learn/criterion.py`, `core/operator/external_gateway.py`.
- Tests: `tests/test_criterion.py`, `tests/test_external_gateway.py`.
- Docs: `README.md` → "🧭 Motor de Criterio Aprendido" y "📋 Changelog" (`### 2026-07-20`).
- PRs: #41 (contrato), #42 (polaridad + origin), #43 (release notes).
- Release: `v2026.07.20-criterion-contract`.
