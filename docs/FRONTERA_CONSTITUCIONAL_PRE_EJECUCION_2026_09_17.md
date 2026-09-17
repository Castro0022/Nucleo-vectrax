# Frontera Constitucional PRE-ejecución (P0) — LLM / Online / Places / Market
**Fecha:** 2026-09-17
**Responsable:** Mario Bravo Castro
**Estado:** ✅ Implementado y desplegado en modo **SHADOW** (observación) — ninguna frontera promovida a ENFORCE todavía

---

## Resumen ejecutivo

Se implementó un gate constitucional PRE-ejecución que garantiza que ninguna llamada externa real de las 4 fronteras gobernadas (**LLM, Online, Places, Market**) ocurra sin pasar antes por una evaluación de los 7 principios (`constitutional_filter.evaluate()`) — el mismo motor que ya usaba el gate `respond` existente, que permanece intacto y sin modificar.

El sistema está desplegado y activo en producción desde el merge de PR #117. Las 4 fronteras operan en modo **SHADOW**: observan y registran en el ledger, pero nunca alteran el comportamiento existente. Ninguna ha sido promovida a **ENFORCE** — los datos reales de producción (sección "Estado de promoción", más abajo) muestran que ninguna cumple aún el criterio cuantitativo de promoción.

---

## PASO 0 — Hallazgos de la investigación previa

Antes de escribir código se auditó qué ya existía en el codebase para evitar duplicar arquitectura.

### Hallazgo principal: construir desde cero, no conectar

`core/operator/decision_authority.py::AUTO_ACTIONS` ya contenía los strings `resolve_online`, `resolve_market`, `resolve_places`, pero:
- `check_authority()` (única función que lee ese frozenset) **nunca era invocada** con esos nombres antes de esta implementación.
- Aunque se hubiera invocado, al estar en `AUTO_ACTIONS` siempre habría devuelto `Authority.AUTO, auto_approved=True` — nunca `CAUTION`/`BLOCK`, sin importar los 7 principios.
- La única fuente real de `PASS`/`CAUTION`/`BLOCK` en todo el codebase es **`constitutional_filter.evaluate()`** (genérico, ya usado por el gate `respond`).

**Decisión de diseño resultante:** el pre-gate reutiliza `evaluate()` (7 principios reales) en vez de `check_authority()` (solo auto-aprobación para `AUTO_ACTIONS`). `check_authority()` se conserva únicamente como sub-paso dentro del veredicto `CAUTION`, exactamente el mismo patrón que ya usaba el gate `respond`.

- **`resolve_llm` no existía en ningún lado** del vocabulario de acciones — fue el único nombre nuevo introducido, para dar simetría a las 4 fronteras.

### Hallazgo secundario: la superficie real era mayor a la estimada inicialmente

Un grep exhaustivo tras el diagnóstico inicial reveló puntos de I/O adicionales no contemplados en la primera pasada:
- **LLM**: 3 executors encadenados secuencialmente como fallback chain (`route_single` → OpenAI directo → Ollama local) dentro de `_generate_cognitive_response()`, más un fan-out real (`query_parallel()`) alcanzable desde el CLI del creador (`/multi <prompt>`).
- **Online**: contiene LLM anidado — `resolve_online()` internamente llama a dos de los mismos executors de LLM (`_search_multi_engine()` + `_interpret_with_llm()`).
- **Places**: invocada desde 3 call sites distintos (`ExternalGateway`, `telegram_gateway.py`, `pipeline_worker.py` en modo fire-and-forget post-respuesta).
- **Market**: dos executores independientes (`handle_market_intent()` y `MarketVigilance.fetch_state()`), con un caller adicional de origen `SYSTEM` real (`scheduler.py`, tareas programadas).

---

## Arquitectura implementada

Tres módulos nuevos, ninguno modifica el gate `respond` existente ni `constitutional_filter.py`:

| Módulo | Responsabilidad |
|---|---|
| `core/operator/execution_context.py` | `ExecutionContext` — origen, actor, acción, dominio, correlation_id; `.to_proposal()` lo mapea a `ActionProposal`. |
| `core/operator/boundary_mode.py` | Modo SHADOW/ENFORCE **independiente por frontera** (nunca un interruptor global), persistido en `~/.vectrax/execution_boundary_mode.json`, fail-safe a SHADOW si el archivo falta o es inválido. |
| `core/operator/pre_execution_gate.py` | `authorize(boundary, execution_context)` — punto único de autorización; nunca lanza; registra síncronamente en el ledger antes de retornar. Incluye `promotion_readiness()` de solo lectura. |

### Los 9 puntos de inserción de `authorize()`

1. `vectrax/intelligence_bridge.py::route_single()` → `IntelligenceRouter.route()` (executor LLM primario)
2. `external_gateway.py::_generate_openai_direct()` → `core/llm_call.py::complete()` (executor LLM directo, fallback)
3. `external_gateway.py::_generate_ollama_local()` → `httpx.post()` (executor Ollama local, fallback)
4. `vectrax/resolver.py::_search_multi_engine()` (Tavily → DDG → Brave → Google CSE)
5. `vectrax/resolver.py::_interpret_with_llm()` (LLM anidado dentro de Online)
6. `vectrax/integrations/place_search.py::search_places()` (Google Places API)
7. `vectrax/market/market_executor.py` (connector de mercado — hoy solo lectura, pero I/O real)
8. `vectrax/market/market_vigilance.py::MarketVigilance.fetch_state()` (segundo executor Market)
9. `vectrax/cli.py::creator_chat()` → `_handle_multi_command()` → `query_multi()`/`query_parallel()` (fan-out real de LLM)

Cada uno llama a `authorize()` de forma independiente antes de su I/O real.

---

## Estado final de la implementación

### Las 4 fronteras — todas en SHADOW

| Frontera | Modo actual | Promovida a ENFORCE |
|---|---|---|
| LLM | SHADOW | No |
| Online | SHADOW | No |
| Places | SHADOW | No |
| Market | SHADOW | No |

No existe override en `~/.vectrax/execution_boundary_mode.json` — las 4 fronteras están en el modo fail-safe por defecto (SHADOW), tal como se desplegaron.

### Callers migrados (propagan `ExecutionContext` real)

11 sitios con propagación completa, confianza alta:

1. `external_gateway.py::_generate_cognitive_response()` (3 executors: `route_single`, OpenAI directo, Ollama local)
2. `external_gateway.py::_try_market_resolve()`
3. `external_gateway.py::_try_place_search()`
4. `vectrax/resolver.py::resolve_online()` (`_search_multi_engine` + `_interpret_with_llm`)
5. `vectrax/telegram_gateway.py` (4 sitios: atajo BTC/ETH, `/vx btc|eth|sol`, `/vx market snapshot`, `_places()`)
6. `core/transport/pipeline_worker.py` (`resolve_online` + `search_places` post-respuesta)
7. `core/scheduler.py::_build_task_message()` (origen `SYSTEM`, tareas programadas)
8. `vectrax/cli.py::creator_chat()` (`_handle_multi_command()` → fan-out)
9. `vectrax/market/market_vigilance.py::MarketVigilance.fetch_state()`

### Callers SIN migrar (cubiertos por SHADOW, confianza baja)

`core/llm_call.py::complete()` tiene callers adicionales fuera de las 4 fronteras migradas: `self_context.py`, `read_tool_bridge.py`, `criterion.py`. Estos alcanzan el gate sin `ExecutionContext` válido y quedan registrados como `WOULD_BLOCK_MISSING_CONTEXT` — comportamiento sin cambios en SHADOW, pero bloquearían el flujo si la frontera LLM se promoviera a ENFORCE hoy sin migrarlos primero.

### Cambio explícito en `decision_authority.py`

Se removieron `resolve_online`, `resolve_market`, `resolve_places` del frozenset `AUTO_ACTIONS`, porque ahora se evalúan con los 7 principios reales (`constitutional_filter.evaluate()`) en vez de auto-aprobarse incondicionalmente.

### Tests — 24 casos verdes

Matriz completa por las 4 fronteras (`tests/test_pre_execution_gate.py`), cubriendo los 7 casos requeridos:
1. SHADOW + contexto ausente/inválido → `WOULD_BLOCK_MISSING_CONTEXT` + ejecuta igual.
2. ENFORCE + `BLOCK` → cero I/O.
3. ENFORCE + `PASS` → ejecución exacta, conjunto autorizado completo.
4. ENFORCE + `CAUTION` autorizado → una corrección determinística + ejecuta.
5. ENFORCE + `CAUTION` en fan-out no autorizado → cero I/O, `SKIPPED_EMPTY_AUTHORIZED_SET`, sin fallback.
6. ENFORCE + contexto ausente/inválido → `BLOCKED_MISSING_CONTEXT`, cero I/O (fail-closed).
7. Aislamiento entre fronteras: una en ENFORCE y las otras 3 en SHADOW → solo la de ENFORCE aplica fail-closed.

---

## Estado de promoción SHADOW → ENFORCE (datos reales, en vivo)

Criterio de promoción (`promotion_readiness()`, los 4 deben cumplirse a la vez): 0 eventos de contexto ausente/inválido, 100% de contexto válido, ≥100 ejecuciones observadas, ≥72h continuas sin ningún evento inválido.

Consulta real contra el ledger de producción al momento de este cierre:

| Frontera | Ejecuciones observadas | `WOULD_BLOCK_MISSING_CONTEXT` | % contexto válido | Lista para ENFORCE |
|---|---|---|---|---|
| LLM | 118 | 40 | 66.1% | ❌ No |
| Online | 27 | 9 | 66.7% | ❌ No |
| Places | 60 | 34 | 43.3% | ❌ No |
| Market | 44 | 16 | 63.6% | ❌ No |

**Ninguna frontera cumple hoy el criterio de promoción.** El porcentaje de contexto inválido más alto es Places (56.7% de ejecuciones sin contexto), seguido de LLM (33.9%, coherente con los callers de `core/llm_call.py` aún sin migrar).

### Próximo paso recomendado (no ejecutado en este ciclo)

Migrar los callers restantes de `core/llm_call.py::complete()` (`self_context.py`, `read_tool_bridge.py`, `criterion.py`) y auditar de forma similar los callers de Places con `%` de contexto inválido más alto, antes de reevaluar la promoción de cualquier frontera a ENFORCE.

---

## Archivos clave

| Archivo | Rol |
|---|---|
| `core/operator/execution_context.py` | `ExecutionContext` (nuevo) |
| `core/operator/boundary_mode.py` | Modo SHADOW/ENFORCE por frontera (nuevo) |
| `core/operator/pre_execution_gate.py` | `authorize()` + `promotion_readiness()` (nuevo) |
| `core/operator/decision_authority.py` | `AUTO_ACTIONS` — 3 acciones removidas |
| `core/operator/external_gateway.py` | 3 executors LLM + Market + Places migrados |
| `vectrax/resolver.py` | Online (2 executors) migrado |
| `vectrax/telegram_gateway.py` | 4 sitios migrados |
| `core/transport/pipeline_worker.py` | Places + Online post-respuesta migrados |
| `core/scheduler.py` | Market (origen SYSTEM) migrado |
| `vectrax/cli.py` | Fan-out LLM del creador migrado |
| `vectrax/market/market_vigilance.py` | Segundo executor Market migrado |
| `tests/test_pre_execution_gate.py` | 24 tests — matriz completa por frontera |

---

*Vectrax — Núcleo Cognitivo. Creado por Mario Bravo Castro.*
*Documento de cierre: 2026-09-17*
