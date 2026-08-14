# Filtro Constitucional — Fase 1 (Shadow Mode)

## Estado

**Aceptado funcionalmente en shadow mode.** El filtro evalúa cada propuesta
de acción contra los 7 Principios Fundamentales (`core/operator/identity.py::FUNDAMENTAL_LAWS`)
y registra el veredicto en el ledger (`EventCategory.CONSTITUTIONAL`), pero
**nunca altera el comportamiento del pipeline**. `enforce` mode no se ha
activado en ningún momento.

## Cobertura real (lo que SÍ está garantizado)

El **pipeline normal de mensajes de Telegram** — y de cualquier canal que
entre por `ExternalGateway.receive_message()` (web, api, webhook) — pasa
por el choke point constitucional en el 100% de los casos, sin importar
cuál de los 6 `return GatewayResult(...)` internos de `_do_receive_message`
produjo la respuesta (greeting, intake filter, self-aware, domain
criterion, memory, SmartRouter/pipeline_v2). Esto está verificado con
tests deterministas en `tests/test_constitutional_filter.py`
(`TestAllEarlyReturnsReachChokePoint`), no solo por inspección de código.

También están cubiertos:
- **Aprendizaje**: `core/learning_cycle/learning_integrator.py::_try_activate_rule`
  (choke point 3).
- **Generación de ideas**: `core/idea_store.py::IdeaStore.add()` (choke point 4).

## Deuda de integración — 3 caminos NO cubiertos todavía

Esto **no es una afirmación de "ninguna ruta evita el filtro"**. Son
límites reales y conocidos que deben cerrarse (o aceptarse explícitamente
como fuera de alcance) ANTES de considerar activar `enforce` mode.

### 1. Estrategia interna de SmartRouter (choke point 2, no instrumentado)

`core/smart_router.py::SmartRouter._resolve_via_pipeline_v2` decide y
EJECUTA una estrategia (places / online / market / llm) antes de que el
texto de respuesta llegue al choke point 1. Hoy el filtro solo observa el
**resultado final** de esa ejecución — no evalúa la propuesta *antes* de
que la búsqueda/llamada externa se dispare.

En shadow mode esto es inofensivo (no hay nada que bloquear todavía). En
`enforce` mode sería una laguna real: un BLOCK constitucional llegaría
*después* de que, por ejemplo, ya se hizo una llamada a un motor externo
(online search, market data), no antes. Cerrar esto requiere un choke
point adicional dentro de `_resolve_via_pipeline_v2`, antes de invocar
cada estrategia.

### 2. Ejecución de ideas (choke point 5, no existe el mecanismo)

El choke point 5 (evaluar la propuesta antes de checkpoint/tests/deploy de
una idea aprobada) está planeado para la Fase 4, junto con
`core/idea_executor.py`. Hoy ese módulo **no existe** — aprobar una idea
(`IdeaStore.approve()`) sigue siendo solo un cambio de estado, sin
ejecución real. No hay nada que instrumentar todavía; esto es una
dependencia de fase, no un bypass activo, pero se documenta aquí para que
no se asuma cobertura donde no la hay.

### 3. Llamadas directas a `_do_receive_message()` (convención, no garantía técnica)

El choke point 1 está en el método público `receive_message()`, que
envuelve `_do_receive_message()`. Todo caller de producción confirmado usa
`receive_message()`:
- `core/transport/pipeline_worker.py` (Telegram, fork y en-proceso)
- `vectrax/telegram_gateway.py`
- `services/core/routes/gateway.py`
- `services/core/routes/presence.py`

Pero esto es una **convención de nombre** (`_do_...` como señal de "no
llamar directamente"), no un mecanismo técnico que lo impida. Cualquier
código nuevo (o futuro) que invoque `ExternalGateway()._do_receive_message()`
directamente evitaría el choke point sin que nada lo detecte
automáticamente. No se ha encontrado ningún caller así hoy, pero no hay
una guardia en tiempo de ejecución que lo prevenga.

## Requisito antes de enforcement

Los 3 puntos anteriores deben quedar resueltos (instrumentados) o
aceptados explícitamente como fuera de alcance por el creador antes de
mover el flag global (`~/.vectrax/constitutional_mode.json`) a `enforce`.
Esto es adicional al checklist de `constitutional_readiness` ya descrito
en el plan de la Fase 1 (cobertura logueada, sin bypass, suite en verde,
reporte comparativo, Telegram/trading PAPER verificados, bandera de
reversión inmediata).
