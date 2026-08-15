# Deuda técnica: aislamiento incompleto de `~/.vectrax` en la suite de tests

## Resumen

La suite hermética (`pytest tests/ -m "not live"`) se cuelga de forma
reproducible (2/2 corridas, siempre en el mismo punto, ~17% de avance) sin
lanzar ninguna excepción. La causa no es un defecto de lógica: un test
escribe, sin aislamiento, sobre el mismo archivo `~/.vectrax/domain_library/
freight_logistics.json` que el **Vectrax en vivo** (supervisor,
`pipeline_worker`, `telegram_gateway`, API en `:8900`) lee/escribe en tiempo
real desde su propio ciclo de aprendizaje freight. En esta máquina, el
checkout de trabajo (`/Users/mariobravo/Vectrax`) y el árbol que corren los
procesos en vivo (`/Users/mariobravo/vectrax`) son el **mismo directorio en
disco** (macOS/APFS es case-insensitive por defecto), así que no hace falta
ni siquiera un mount de red: el propio filesystem local los une.

Este documento describe exactamente qué toca ese estado compartido, por qué
existe (y por qué el fixture hermético ya existente no lo cubre), y la
solución propuesta. **No se aplicó ningún fix aquí** — es deuda técnica
separada, fuera del alcance de la Fase 2 (SSOT de intent), documentada a
pedido explícito para no detener el Vectrax en vivo ni tocar temporales del
sistema real mientras se investigaba.

## Evidencia del cuelgue

`python -m pytest tests/ -m "not live" -q` se interrumpió dos veces (Ctrl-C
tras ~30 min) en el mismo punto (~630/~3300 tests, 17%). Con `pytest-timeout`
instalado (`--timeout=15 --timeout-method=thread`) se aisló el stack exacto:

```
tests/test_advanced_architecture.py::TestFreightLearningCycle::test_cycle_calls_provider_once
  → connectors/freight/learning_cycle.py:135  run_learning_cycle() → ingest_event(...)
  → core/domain_ingester.py:260               try_elevate_from_gravity(domain, tenant_id)
  → core/domain_knowledge.py:357              elevate_pattern(...)
  → core/domain_knowledge.py:247              _save_library(domain, lib)
  → core/domain_knowledge.py:134              json.dump(..., f, ...)   ← bloqueado en fp.write()
```

`ps aux` confirmó que el Vectrax en vivo estaba corriendo durante la corrida
(`vectrax_supervisor.py`, `core.transport.pipeline_worker`,
`vectrax.telegram_gateway`, `uvicorn services.core.app:app --port 8900`,
`observability.audit_cron`), todos con PID desde antes de iniciar los tests.

## Qué archivos comparte el test con producción

`connectors/freight/learning_cycle.py::run_learning_cycle()` (dominio
hardcodeado `_DOMAIN = "freight_logistics"`) golpea **dos** stores globales
sin `path`/directorio configurable por test:

1. **`~/.vectrax/domain_library/freight_logistics.json`**
   — `core/domain_knowledge.py`: `_LIBRARY_DIR = os.path.join(os.path.expanduser("~"), ".vectrax", "domain_library")` (línea 40).
   Escrito por `_save_library()` (línea 120), invocado desde
   `try_elevate_from_gravity()` → `elevate_pattern()`.
2. **`~/.vectrax/gravity_index.json`**
   — `core/learn/gravity_engine.py`: `GRAVITY_INDEX_PATH = os.path.join(RUNTIME_DIR, "gravity_index.json")` con `RUNTIME_DIR = os.path.expanduser("~/.vectrax")` (`core/learn/__init__.py:18`).
   Escrito por `GravityIndex._save()` (línea 95), instanciado sin `path=`
   custom por `get_gravity_index()` dentro de `core/domain_ingester.py::ingest_event()` (línea 179-180).

`ingest_event()` (línea 149) es la función real que el loop de
`run_learning_cycle()` llama por cada evento, y **ella misma** vuelve a
invocar `try_elevate_from_gravity()` al final (línea 258-264, "fire-and-forget")
— es decir, la elevación al `domain_library` ocurre tanto por-evento (dentro
de `ingest_event`) como una vez más al cierre del ciclo (`learning_cycle.py`
línea 150-155). Con eventos repetidos (`_SAMPLE_EVENTS * 10` en el test), esto
multiplica los `_save_library()`/`GravityIndex._save()` sobre el MISMO
archivo que el proceso en vivo también está reescribiendo en paralelo.

## Qué test(s) están afectados

`tests/test_advanced_architecture.py::TestFreightLearningCycle` (clase
completa, 5 tests) — su docstring dice *"All tests inject a mock provider;
no production data is touched"*, pero eso solo cubre la fuente de eventos
(`_MockProvider`), no el destino de la escritura:

- `test_cycle_calls_provider_once` — **confirmado**: es el que colgó la suite.
- `test_cycle_returns_structured_summary` — mismo camino (`run_learning_cycle`
  con eventos reales), mismo riesgo.
- `test_elevation_called_once_per_cycle_not_per_event` — intenta parchear
  `try_elevate_from_gravity`, pero `learning_cycle.py` lo importa con un
  `from core.domain_knowledge import try_elevate_from_gravity` **local**
  dentro de la función (línea 151), no como atributo de módulo estable — el
  patch sobre `connectors.freight.learning_cycle.try_elevate_from_gravity`
  no necesariamente intercepta esa llamada. Sin verificar más a fondo si el
  parche efectivamente no-opera, de cualquier forma no aísla `GRAVITY_INDEX_PATH`
  (el ingest de `core.domain_ingester` sigue escribiendo el gravity index real).
- `test_cycle_unhealthy_provider_exits_early` y `test_disabled_cycle_returns_immediately`
  — revisados: ambos retornan antes de llegar al loop de ingest (provider
  unhealthy / `FREIGHT_LEARN_ENABLED` off), por lo que **no** tocan estos dos
  archivos en la práctica. Se listan por completitud de la clase, no como
  hallazgo activo.

Para contraste — el patrón correcto **ya existe** en el propio repo y debería
ser el modelo a replicar:

- `tests/test_domain_knowledge.py::DomainKnowledgeTestBase` (líneas 43-56)
  redirige `core.domain_knowledge._LIBRARY_DIR` a un `tempfile.mkdtemp()` en
  `setUp`/`tearDown`.
- `tests/test_freight_pipeline.py::FreightPipelineTestBase` (líneas 44-60)
  hace lo mismo **y además** instancia `GravityIndex(path=...)` con un path
  temporal explícito — exactamente los dos stores que `test_advanced_architecture.py`
  deja sin aislar.
- `tests/test_criterion.py` y `tests/test_universal_pattern_library.py` no
  tienen este problema porque solo parchean funciones de **lectura**
  (`list_domains`, `get_domain_priors`, `get_domain_summary`) vía
  `unittest.mock.patch`, nunca llaman a `elevate_pattern`/`_save_library` real.
- `tests/test_cybersecurity_domain.py` no está afectado: su
  `learning_cycle.py` (cybersecurity) no importa `core.domain_knowledge` en
  absoluto.

Se revisaron también (sin hallazgos, solo lectura mockeada o sin relación con
`domain_knowledge`/`gravity_engine`): `tests/test_etoro_circuit_breaker.py`,
`tests/test_system_report.py`, `tests/test_freight_intent_retrieval.py`.

## Por qué el fixture hermético existente no lo cubre

`tests/conftest.py::_hermetic_base` (autouse) ya redirige:
- `VECTRAX_VAULT_DIR` (observation ledger),
- `vectrax.user_memory._MEMORY_DB_PATH` (memoria por-usuario),
- neutraliza credenciales externas,
- fuerza `VECTRAX_ACTIVATE_ENGINES=off`.

Pero **no** conoce `core.domain_knowledge._LIBRARY_DIR` ni
`core.learn.gravity_engine.GRAVITY_INDEX_PATH` — ambos se calculan con
`os.path.expanduser("~")` directo al importar el módulo, sin pasar por
`VECTRAX_VAULT_DIR` ni por ninguna variable de entorno interceptable
globalmente. Cada módulo que necesita aislamiento tiene que redirigirlo a
mano (como hacen `DomainKnowledgeTestBase`/`FreightPipelineTestBase`); si un
test nuevo no lo hace, cae directo al `~/.vectrax` real sin ningún error ni
warning.

## Solución propuesta (no aplicada — deuda para una tarea aparte)

1. **Corto plazo, dirigido**: dar a `tests/test_advanced_architecture.py::TestFreightLearningCycle`
   el mismo tratamiento que `FreightPipelineTestBase` — un `setUp`/fixture que
   redirija `core.domain_knowledge._LIBRARY_DIR` y construya/inyecte un
   `GravityIndex(path=tmp)` (vía `monkeypatch.setattr("core.learn.gravity_engine.get_gravity_index", ...)`
   o parcheando `GRAVITY_INDEX_PATH` antes de que el singleton se cree) a un
   directorio temporal por test.
2. **Mediano plazo, sistémico**: introducir una variable de entorno única
   (p. ej. `VX_STATE_DIR`, con default `~/.vectrax` para producción) que
   `core/learn/__init__.py::RUNTIME_DIR` y `core/domain_knowledge.py::_LIBRARY_DIR`
   (y el resto de los ~35 módulos bajo `core/` que hoy hacen
   `os.path.expanduser("~/.vectrax/...")` directo — ver lista no exhaustiva
   más abajo) lean en vez de hardcodear `~/.vectrax`. Esto permite que
   `tests/conftest.py::_hermetic_base` fije `VX_STATE_DIR` a un
   `tmp_path`/directorio temporal **por ejecución de test** en un solo lugar,
   igual que ya hace hoy con `VECTRAX_VAULT_DIR`, sin tener que tocar cada
   módulo individualmente cada vez que aparece uno nuevo.
3. **Red de seguridad adicional**: un chequeo de arranque de la suite (p. ej.
   un fixture `session`-scoped en `conftest.py`) que falle rápido y con un
   mensaje claro si detecta que el PID del supervisor/worker en vivo está
   activo Y la suite no tiene `VX_STATE_DIR` (o equivalente) apuntando a un
   directorio temporal — para convertir este tipo de cuelgue silencioso en un
   error inmediato y explicable la próxima vez.

Otros módulos bajo `core/` que hoy resuelven rutas de estado bajo
`~/.vectrax` de forma directa (candidatos a la migración del punto 2, no
confirmados individualmente como causa de cuelgues — listados para
dimensionar el alcance real de la migración futura): `core/scalability_guard.py`,
`core/operator/constitutional_mode.py`, `core/self_observation/*.py`,
`core/sovereignty/{ledger,consent}.py`, `core/recovery/ledger.py`,
`core/continuity/*.py`, `core/voice/anti_repetition.py`,
`core/universal_pattern_library.py`, `core/transport/pipeline_worker.py`
(heartbeat/log).

## Alcance de este documento

Esto es puramente diagnóstico — no se detuvo el Vectrax en vivo, no se
borraron los archivos `.tmp.*` huérfanos encontrados en
`~/.vectrax/domain_library/` (quedan como evidencia y porque borrarlos no
resuelve la causa raíz), y no se modificó ningún test ni módulo de
`core/domain_knowledge.py`/`core/learn/gravity_engine.py` como parte de la
Fase 2 (SSOT de intent). La Fase 2 en sí (pasos 1-3: `intent_ssot.py`,
migración de `external_gateway._resolve_query_domain()` y
`pipeline_worker._quick_intent()`) se validó exclusivamente con suites
dirigidas (`tests/test_intent_ssot.py`, `tests/test_external_gateway.py`,
`tests/test_worker_hardening.py`), que no tocan `~/.vectrax/domain_library`
ni `~/.vectrax/gravity_index.json`.
