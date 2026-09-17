# Regresión de evidencia por dominio (freight/cybersecurity/real_estate) — 2026-09-13

Cierre archivado. Cubre el reporte de regresión de producción, la investigación que descartó
los últimos dos commits de la sesión como causa, la causa raíz real (`known_domains()`), el
fix desplegado, la investigación separada del vacío de `florida_real_estate` en
`verification_ledger`, y la regresión de estabilidad final ejecutada dos veces en producción
(2026-09-13 y 2026-09-17).

## Reporte original

Tras desplegar la integración de ReasoningEngine (`900336c`) y el gate activo de capacidades
(`ade756a`), se reportó:

- `freight_logistics`, `cybersecurity` y `florida_real_estate` dejaron de aparecer
  correctamente entre los dominios de Gravity/Universo.
- `freight_logistics` seguía recuperando evidencia; los otros dos no.
- La pregunta explícita "¿Qué has observado en el dominio de ciberseguridad?" se respondía
  con evidencia de `market` (BTC/META).

Instrucción: identificar el cambio exacto, restablecer el contrato anterior sin deshacer las
mejoras nuevas, y no cerrar hasta demostrar los 5 puntos de regresión contra el pipeline real
tras el reinicio.

## Investigación — descartando la causa aparente

Evidencia recolectada antes de tocar código:

1. `git diff` de `900336c` y `ade756a` solo modifica `core/nucleus/total_convergence.py`,
   `core/operator/external_gateway.py` y `core/self_observation/capability_context.py`.
   Ninguno de los archivos que deciden dominio/evidencia (`core/learn/criterion.py`,
   `core/learn/gravity_engine.py`, `core/domain_knowledge.py`,
   `core/learn/verification_ledger.py`) fue tocado.
2. `git log --follow` sobre `criterion.py::known_domains()` muestra que esa función no cambia
   desde `a5a046f` (2026-07-15) — más de un mes antes de la sesión.
3. La sobrecarga real medida de `ReasoningEngine` (`_run_reasoning()`) es de 30-190ms por
   ciclo — descarta cualquier cascada de timeout contra `CONVERGENCE_TIMEOUT=10.0s`.

Conclusión: los commits de la sesión no causaron la regresión. La premisa de "regresión
introducida por el último cambio" era incorrecta; el defecto era preexistente y se hizo
visible por otra razón (ver más abajo, "por qué ahora se notó").

## Causa raíz real

`core.learn.criterion.known_domains()` solo unía dos fuentes:

- `gravity_engine.domain_stats().keys()` — `cybersecurity` nunca tuvo registros ahí.
- `domain_knowledge.list_domains()` — solo `freight_logistics.json` existía en
  `~/.vectrax/domain_library/` en el momento del incidente.

`detect_domain()` itera exclusivamente sobre `known_domains()`, así que **no podía devolver
`"cybersecurity"` para ninguna pregunta**, sin importar el vocabulario definido en
`_DOMAIN_VOCAB`. Sin dominio detectado, el Domain Criterion Gate (STEP 4.2a3 de
`external_gateway.py`) nunca se activaba para esa pregunta, y esta caía al self-aware/LLM, que
sin ese anclaje podía mezclarse con contexto de otro dominio (`market`, cuyas estrellas tienen
la mayor masa gravitacional del sistema).

La evidencia de `cybersecurity` sí existía y era recuperable todo el tiempo:
`vault/domain_verification/cybersecurity.jsonl` (168MB) y
`build_criterion_result("cybersecurity", ...)` la recuperaba correctamente cuando se le pasaba
el dominio de forma explícita — el defecto era exclusivamente de **detección**, no de
**evidencia**.

## Fix desplegado (commit `878286c`)

- `core/learn/verification_ledger.py`: nueva función `list_domains()` — lee los nombres de
  archivo en `vault/domain_verification/*.jsonl` (excluye backups `.bak.*`).
- `core/learn/criterion.py`: `known_domains()` agrega esa tercera fuente, fail-safe si falla.
- `tests/test_known_domains_verification_ledger.py` — 8 casos nuevos, vault aislado por test.

### Evidencia antes/después

| | Antes | Después |
|---|---|---|
| `known_domains()` | sin `cybersecurity` | incluye `cybersecurity` |
| `detect_domain("...ciberseguridad?")` | `None` | `"cybersecurity"` |
| Pregunta de ciberseguridad (pipeline real) | `resolve_mode=self_aware`, riesgo de mezcla con market | `resolve_mode=criterion:deterministic`, evidencia propia (`family:ui:unifi_os_server`) |

## Investigación separada — vacío de `florida_real_estate` en `verification_ledger`

`verification_cycle.py::verify_events()` solo acepta como "verdad objetiva" los eventos
`sale_closed`, `expired`, `withdrawn`, `cancelled`. `RentCastProvider.stream_events()`
(`connectors/real_estate/rentcast_provider.py`) solo produce `sale_closed` cuando llama al
endpoint `/properties` (que trae `lastSalePrice`) — y esa llamada está detrás de
`RENTCAST_INCLUDE_SOLD` (default `"0"`, documentado como "ahorra cuota por defecto"). Con el
flag apagado, el proveedor solo trae `/listings/sale` (`new_listing`/`status_change`), que
`verify_events()` filtra al 100%.

Verificado en vivo contra la API real: 20/20 eventos devueltos por `stream_events(20)` fueron
`new_listing`; cero `sale_closed`.

**Este vacío sigue existiendo** (`vault/domain_verification/florida_real_estate.jsonl` no
existe) y es un interruptor de cuota, no un bug. Fix disponible y no aplicado (decisión
pendiente del creador): `RENTCAST_INCLUDE_SOLD=1` en `.env` — costo estimado: 5 llamadas
adicionales a `/properties` por ciclo de 6h (una por ZIP configurado).

### Por qué el síntoma dejó de notarse sin ese fix

Entre el 13 y el 17 de septiembre, el ciclo de aprendizaje de `florida_real_estate`
(`_run_real_estate_learn`, cada 6h) acumuló suficiente actividad real `new_listing` con
uptime estable del worker para que:

- `gravity_engine.by_domain("florida_real_estate")` pasara de 0 a 101 registros reales.
- `domain_knowledge.try_elevate_from_gravity()` creara `domain_library/florida_real_estate.json`
  (2026-09-16).

`rank_domain_evidence()` lee gravity como fuente independiente de `verification_ledger`, así
que `build_criterion_result("florida_real_estate", ...)` ya tiene evidencia real (`new_listing`,
WR 100%, ~30 obs) que citar, aunque `verification_ledger` siga vacío para este dominio.

## Regresión de estabilidad — ejecutada dos veces en producción

Ambas corridas contra el pipeline real (`ExternalGateway.receive_message` /
`run_convergence_cycle`, mismo mecanismo que usa `pipeline_worker.py`), sin mocks:

| Prueba | 2026-09-13 (post-fix) | 2026-09-17 (confirmación de estabilidad) |
|---|---|---|
| `freight_logistics` | `criterion:llm_rendered`, `delivery_complete` +125.69/+124.87, WR 90% | igual, WR 90%, sin market |
| `cybersecurity` | `criterion:deterministic`, `family:ui:unifi_os_server` | igual, sin market |
| `florida_real_estate` | `self_aware`, honesto "sin datos" (aún no había acumulado gravity) | `criterion:deterministic`, `new_listing` real, sin market |
| `market` | `criterion:llm_rendered`, BTC real | `criterion:llm_rendered`/`deterministic`, BTC real |
| Puente A | `read_tool_bridge`, evidencia real de `ConnectionEngine.read()` | igual |
| Reasoning Engine | `reasoning_ran=True`, `recommendation=proceed` | igual |

`known_domains()` en la corrida de estabilidad:
`['cli', 'cognition', 'core', 'cybersecurity', 'florida_real_estate', 'freight_logistics',
'market', 'models', 'pipeline', 'services']` — los 4 dominios de negocio presentes y
recuperables en ambas corridas, sin contaminación cruzada con `market` en ningún caso.

Se evaluó `ai_provider` como posible "5º dominio" (tenía datos reales el 2026-09-13) — al
2026-09-17 tiene 0 registros en gravity y responde honestamente "sin datos", sin fabricar.
No se identificó un 5º dominio de negocio estable adicional a los 4 anteriores.

## Archivos y commits

- `900336c`, `ade756a` — commits investigados y descartados como causa.
- `878286c` — fix real (`known_domains()` + `verification_ledger.list_domains()`).
- `core/learn/criterion.py`, `core/learn/verification_ledger.py`,
  `tests/test_known_domains_verification_ledger.py`.

## Pendiente (fuera de alcance de este cierre)

- Decisión del creador sobre `RENTCAST_INCLUDE_SOLD=1` para poblar
  `verification_ledger/florida_real_estate.jsonl` con verdad de cierre objetiva.
- Confirmar si existe un 5º dominio de negocio activo distinto a
  `market`/`freight_logistics`/`cybersecurity`/`florida_real_estate`.
