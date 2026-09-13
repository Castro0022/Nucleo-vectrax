# Incidente de latencia self_aware_context_build (2026-09-13)

Reporte de cierre. Cubre el diagnóstico con dos cronómetros, la causa raíz
confirmada en `convergence_registry`, la Fase 1 (índices + query acotada) y
la Fase 2 Paso 1 (deduplicación de lifecycle events), con evidencia
antes/después medida en producción (este Mac). **En ningún paso se hizo
DELETE, VACUUM, poda ni migración de los 178.5M+ registros históricos**, ni
se tocó routing, Smart Router, proveedores o timeouts.

## Síntoma original

Preguntas auto-referenciales (`self_context`) tardaban ~120–173s en
responder. `GATEWAY_TIMEOUT=30.0` en `core/transport/pipeline_worker.py`
abandona la espera del gateway a los 30s — con esta latencia, el worker
descartaba la respuesta y **el usuario nunca la recibía en Telegram**
(`GW_TIMEOUT correlation_id=... exceeded 30s`).

## Paso 1 — Dos cronómetros (commit `17435b1`)

`vectrax/self_context.py::resolve_self_aware()` ganó un parámetro opcional
`act_log` que registra, vía `core.observability.router_activation`, dos
mediciones separadas en `data/router_activation.jsonl`:

- `self_aware_context_build`: `build_self_aware_prompt()` /
  `build_self_context()` completo.
- `self_aware_provider_call`: espera a Intelligence Bridge / OpenAI directo.

`act_log=None` preserva el comportamiento anterior exacto (no-op).

**Resultado real** (`correlation_id=3aa60101ba34`, pregunta real vía el
pipeline completo, canal `telegram`):

| Cronómetro | Latencia |
|---|---:|
| `self_aware_context_build` | 130,056 ms |
| `self_aware_provider_call` | 1.1 ms |

Veredicto: el proveedor LLM no era el problema — casi el 100% del tiempo se
consumía construyendo el auto-contexto.

## Paso 2 — 12 subllamadas instrumentadas (commit `4b31b02`)

`build_self_context()` ganó el helper `_sub_stage()` (mismo patrón
`act_log`/no-op) para medir cada una de sus subllamadas por separado:
`self_ctx_stats`, `self_ctx_universe_state`, `self_ctx_evolution`,
`self_ctx_deploy_memory`, `self_ctx_codebase_structure`,
`self_ctx_convergence_registry`, `self_ctx_market_context`,
`self_ctx_recent_observations`, `self_ctx_engines_state`,
`self_ctx_domain_observations`, `self_ctx_duration_digest`,
`self_ctx_self_knowledge`.

**Resultado real** (`correlation_id=5906371a0561`): `self_ctx_convergence_registry`
= 108,982 ms — **93% de los 118s de `self_aware_context_build`**. El resto
de las 11 subllamadas sumaban menos de 1 segundo en conjunto.
`deployment_memory.deploy_summary()` (306ms) y
`market_context.get_watchlist_summary()` (25ms) — los sospechosos
originales — quedaron descartados.

## Causa raíz confirmada

`core/learn/convergence_registry.py::build_context()` (llamado desde
`self_ctx_convergence_registry`) ejecutaba 3 queries contra
`vault/convergence_history.db`, un archivo de **17.4 GB**:

- `get_recent_lifecycle_events()`: `ORDER BY timestamp DESC LIMIT 5` sin
  índice en `timestamp` → full scan + sort de toda la tabla.
- `count_lifecycle_events()`: `GROUP BY event` sin índice en `event` → full
  scan.
- `get_canonical_convergences(status="active")`: sin `LIMIT`, traía las
  66,059 filas activas solo para usar las primeras 3.

## Fase 1 — Índices + query acotada (commit `75b940f`)

1. `CREATE INDEX IF NOT EXISTS idx_lifecycle_timestamp ON convergence_lifecycle_events(timestamp)`
2. `CREATE INDEX IF NOT EXISTS idx_lifecycle_event ON convergence_lifecycle_events(event)`
3. `build_context()`: el total (`Activas ahora: N`) ahora viene de
   `count_canonical_convergences(status="active")` (usa
   `idx_convergences_status`, ya existente); el top mostrado usa
   `get_canonical_convergences(status="active", limit=3)`. Mismo texto
   visible, sin traer filas de más.

Verificado con `EXPLAIN QUERY PLAN`: las 4 consultas pasaron de
`SCAN`/`TEMP B-TREE` sobre la tabla completa a `SEARCH`/`SCAN USING (COVERING)
INDEX`, con early-termination en la de `timestamp`.

**Antes/después** (pregunta real repetida, *"Dame un diagnóstico del sistema
Vectrax"*, `correlation_id=3d6a8682c9fb`):

| Métrica | Antes | Después | Mejora |
|---|---:|---:|---:|
| `self_aware_context_build` | 109,887–130,056 ms | 5,168 ms | −96% |
| `self_ctx_convergence_registry` | 108,982 ms | 4,299 ms | −96% |
| Latencia total del pipeline | ~120,200–144,056 ms | 22,539 ms (16.16s real) | −85% |
| ¿Bajo `GW_TIMEOUT=30s`? | ❌ No | ✅ Sí | — |

Regresión: 179/179 tests relevantes (`convergence_registry`,
`convergence_unification`, `system_report`, `trend_reader`,
`self_knowledge`, `self_reference_layer`, `cognition`) pasan.

## Auditoría `convergence_history` vs `convergence_registry` (solo lectura)

Ambos módulos escriben en el **mismo archivo físico**
`vault/convergence_history.db`, en tablas distintas:

| Tabla | Dueño | Filas | Peso del archivo |
|---|---|---:|---|
| `convergence_events` (legacy) | `convergence_history.py` | 5,598 | insignificante |
| `convergences` (canónico) | `convergence_registry.py` | 66,059 | insignificante |
| `convergence_lifecycle_events` (canónico) | `convergence_registry.py` | **178,543,654** | **~17.3 GB de 17.4 GB** |
| `convergence_event_map` (canónico) | `convergence_registry.py` | 5,598 (270 ambiguos) | insignificante |

Hallazgos: `convergence_history.py` no tiene ningún llamador vivo en
producción (legacy muerto para el circuito operativo, ya documentado como
tal en el código de sus antiguos consumidores: `universe_observer.py`,
`system_report.py`, `trend_reader.py`). El verdadero causante del tamaño y
la latencia es el lado **canónico**: `vectrax_unified.py`
(`POLL_SECONDS=2`) → `meta_loop.reflect()` → autonomous_observer
`_detect_convergence_changes()` → `record_convergence_snapshot()`, que
insertaba un evento `"confirmed"` por cada convergencia activa **en cada
ciclo de 2 segundos**, sin comparar contra el estado anterior.

Recomendación (sin ejecutar en este cierre): archivar formalmente el módulo
`convergence_history.py` (ya inerte), conservar su tabla sin poda
(evidencia forense de 270 eventos ambiguos no migrados), y enfocar
cualquier política de retención futura en `convergence_lifecycle_events`
—el problema real— no en el ledger legacy.

## Fase 2, Paso 1 — Detener la hiper-escritura (commit `8d6e504`)

`record_convergence_snapshot()` ganó `_lifecycle_state_changed()`: antes de
cada `INSERT` en `convergence_lifecycle_events`, compara `(event,
combined_cc, combined_hits)` contra el último evento ya registrado para esa
`convergence_id`. Comparación **exacta**, sin umbrales nuevos
(`combined_cc` ya viene redondeado a 4 decimales desde
`gravity_engine._match_pair`; estos mismos campos ya se usan como umbrales
de materialidad en `gravity_engine.ALERT_MIN_CC`/`ALERT_MIN_HITS`). Si la
etapa del ciclo de vida cambió (`created→confirmed`,
`active→dissolved`, `dissolved→reappeared`) o si la evidencia numérica
cambió, se inserta; si no, se omite. `confirmation_count`/`last_seen` en
`convergences` **siguen actualizándose siempre** — solo se filtra la fila
redundante del ledger. `POLL_SECONDS=2` sin cambios.

Tests nuevos (`TestLifecycleMaterialityDedup`, 4 casos): nueva
convergencia → 1 fila; mismo estado repetido → 0 filas nuevas; cambio real
de evidencia → 1 fila; transición `active→dissolved` → 1 fila. 33/33 tests
de convergencia + 150 tests de consumidores pasan.

**Medición de producción, ventanas limpias post-deploy** (proceso
`meta_loop` reiniciado, gobernador en modo `act`/nominal):

| Ventana | Filas nuevas en `convergence_lifecycle_events` |
|---|---:|
| Pre-fix (histórico, ~17 días) | 178,543,654 → 178,749,367 (crecimiento continuo) |
| Transición del restart (~35s, cola del proceso viejo) | ~1.23M (no representativo del código nuevo) |
| Post-fix, ventana limpia de 60s (~30 ciclos) | **0** |
| Post-fix, ventana limpia de 180s (~90 ciclos) | **0** |

`MAX(id)` quedó congelado en **179,980,961** durante ambas ventanas limpias.
No se realizó ninguna operación destructiva sobre los ~180M registros
existentes.

## Qué queda deliberadamente sin hacer

- Fase 2 (retención/archivado de los 178.5M+ registros históricos en
  `convergence_lifecycle_events`): diseño pendiente, sin ejecutar.
- Archivado formal del módulo `convergence_history.py`: recomendado, no
  ejecutado (solo auditoría de lectura).
- `self_ctx_convergence_registry` bajó de 109s a ~4.3s pero sigue siendo la
  subllamada más pesada dentro de `self_context` (el `TEMP B-TREE` al
  ordenar las ~66k filas activas por `confirmation_count`/`last_seen`
  podría acelerarse con un índice compuesto — no evaluado en este cierre).
- Ningún cambio a routing, Smart Router, proveedores LLM ni timeouts en
  ninguno de los pasos anteriores.
