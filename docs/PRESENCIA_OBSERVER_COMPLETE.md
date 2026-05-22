# PresenciaObserver — Capa Inhibidora de Motores ✅ CERRADO

**Fecha de cierre:** 2026-05-22  
**Estado:** Desplegado en producción — verificado  
**Commit:** `6dbbdf1` · `59ffaa5` (docs)  
**Deploy:** Vultr `140.82.28.181` — `vectrax-core` Up (healthy)

---

## Resumen ejecutivo

Presencia Pura existía como un modo binario que bloqueaba LLMs externos.
Este ticket la convirtió en una **capa inhibidora activa con consciencia operacional**:
un observador que ve todos los motores del sistema en tiempo real, califica cada
emisión por su origen y soberanía, y decide si permitirla, pausarla, silenciarla
o bloquearla — sin reemplazar ningún motor.

La diferencia entre un sistema que funciona y un sistema que **se observa funcionando**.

---

## Problema original

> *"Si PresenciaObserver no puede decir 'no', entonces no es conciencia operacional;
> es solo monitoreo."*

El modo previo observaba pero no evaluaba. No había criterio de soberanía,
no había registro de por qué actuó o no actuó, y no había distinción entre
una emisión del núcleo y una emisión de un LLM externo.

---

## Solución implementada

### `core/nucleus/presencia_pura.py` — extensión (507 líneas nuevas)

**`EmissionOrigin`** — 11 valores de origen con sovereignty scores fijos:

| Origen | Sovereignty | Ejemplo |
|--------|-------------|---------|
| `NUCLEUS_CORE` | 1.00 | `core.operator.nucleus` |
| `NUCLEUS_IDENTITY` | 0.95 | `core.operator.identity` |
| `NUCLEUS_MEMORY` | 0.90 | `vectrax.memory` |
| `NUCLEUS_CONVERGENCE` | 0.85 | `core.convergence_hook` |
| `NUCLEUS_PERCEPTION` | 0.80 | `cognition.perception.*` |
| `NUCLEUS_HYPOTHESIS` | 0.75 | `core.operator.hypothesis_engine` |
| `REFLEX_FAST_PATH` | 0.70 | `vectrax.fast_path` |
| `INTERNAL_RESPONSE` | 0.65 | `core.governor` |
| `LLM_EXTERNAL` | 0.20 | OpenAI, Gemini, Claude |
| `SEARCH_EXTERNAL` | 0.10 | Tavily, Google CSE |
| `UNKNOWN` | 0.00 | Motor no registrado |

**`InhibitionDecision`** — 4 decisiones posibles:
- `PERMIT` — emisión soberana y convergente, fluye normalmente
- `PAUSE` — ruido elevado, esperar señal más limpia
- `SILENCE` — baja convergencia, descartar sin bloqueo total
- `BLOCK` — soberanía perdida o motor desconocido, detención completa

**`EmissionSignal`** — modelo de señal: `engine_name`, `source_channel`, `origin`,
`convergence`, `noise`, `payload_size`

**`InhibitionRecord`** — registro inmutable de cada decisión con `timestamp`,
`sovereignty`, `reason`, y flag `enforced`

**`PresenciaObserver`** — clase principal:
- Catálogo de 50+ motores internos reconocidos
- Inferencia automática de origen desde nombre de módulo y canal del bus
- Modo `OBSERVER` (default): `enforced=False`, registra sin bloquear producción
- Modo `ACTIVE`: `enforced=True`, aplica inhibición efectiva
- `observe()` / `disconnect()`: suscripción idempotente al canal `BROADCAST`
- `get_records(limit)` / `get_stats()`: introspección completa
- Singleton `get_observer()` / `reset_observer()`

### Reglas de inhibición (en orden de prioridad)

```
Señal entrante
    │
    ├─ 1. origin == UNKNOWN         → BLOCK   (sin autoridad registrada)
    ├─ 2. sovereignty < 0.30        → BLOCK   (demasiado externo)
    ├─ 3. convergence < 0.30        → SILENCE (señal incoherente)
    ├─ 4. noise > 0.90 + conv < 0.5 → BLOCK   (ruido crítico combinado)
    ├─ 5. noise > 0.80              → PAUSE   (ruido elevado)
    └─ 6. default                  → PERMIT  (emisión soberana y convergente)
```

### `core/operator/activation.py` — step 4c

Conecta `PresenciaObserver` al bus automáticamente en cada arranque del
`OperatorRuntime` (non-fatal, wrapped en try/except). Boot log registra:

```
"PresenciaObserver: bus-connected (OBSERVER mode)"
```

---

## Tests

| Suite | Tests | Resultado | Tiempo |
|-------|-------|-----------|--------|
| `test_presencia_inhibitor.py` (nuevo) | 55 | ✅ 100% PASSED | 0.15s |
| `test_presencia_pura.py` (original) | 31 | ✅ 100% PASSED | 0.15s |
| **Total** | **86** | **✅ 0 fallos** | **0.15s** |

Escenarios cubiertos:
- Motor desconocido → BLOCK
- Convergencia baja → SILENCE
- Soberanía baja (LLM externo) → BLOCK
- Ruido elevado → PAUSE
- Ruido crítico + baja convergencia → BLOCK combinado
- Núcleo limpio → PERMIT
- Modo OBSERVER: `enforced=False` en toda decisión
- Modo ACTIVE: `enforced=True` en toda decisión
- Registros guardados y recuperables
- Estadísticas correctas por tipo de decisión
- Bus subscribe/disconnect idempotente
- Singleton consistente
- Inferencia de origen (7 casos)
- Enums completos

---

## Verificación en producción

Servidor: `root@140.82.28.181` · Contenedor: `vectrax-core` · Fecha: 2026-05-22 01:05 UTC

```
TEST 1  importar modulo              ✅ OK
TEST 2  singleton OBSERVER mode      ✅ mode=OBSERVER, is_connected=False
TEST 3  suscripcion al BROADCAST     ✅ ['presencia_pura.observer']
TEST 4  reglas de inhibicion:
        UNKNOWN  → BLOCK             ✅ sovereignty=0.00 enforced=False
        SILENCE                      ✅ sovereignty=1.00 enforced=False
        BLOCK soberania              ✅ sovereignty=0.20 enforced=False
        PAUSE                        ✅ sovereignty=0.65 enforced=False
        PERMIT                       ✅ sovereignty=0.85 enforced=False
TEST 5  convergencia 7 fases         ✅ perception, classification, memory,
                                        analysis, synthesis, gravitation, learning
TEST 6  step_4c_present              ✅ True
TEST 7  observer conectado en boot:
        runtime_state                ✅ active
        observer_connected           ✅ True
        observer_mode                ✅ OBSERVER
        boot_log                     ✅ ['PresenciaObserver: bus-connected (OBSERVER mode)']
TEST 8  observer recibe bus events:
        eventos observados           ✅ 1 → 2 (broadcast registrado)
        último registro              ✅ origin=internal_response decision=PERMIT enforced=False
```

---

## Archivos modificados

```
core/nucleus/presencia_pura.py          +507 líneas  (extendido)
core/operator/activation.py             + 10 líneas  (step 4c)
tests/integration/test_presencia_inhibitor.py  +692 líneas  (nuevo)
README.md                               + 75 líneas  (sección Núcleo Cognitivo)
CHANGELOG.md                            + 93 líneas  (entrada [2026-05-22])
```

---

## Flujo completo actual (producción)

```
Mensaje entrante (Telegram / API REST)
        │
        ▼
[Total Convergence Cycle — 7 fases obligatorias]
  perception → classification → memory → analysis
  → synthesis → gravitation → learning
        │
        ▼
[PresenciaObserver — modo OBSERVER]
  evalúa origin/sovereignty/convergence/noise
  registra decisión (enforced=False — no bloquea aún)
        │
        ▼
[Smart Router → ExternalGateway / Memoria / Identidad]
        │
        ▼
Respuesta al usuario
```

---

## Estado del observer en producción

| Parámetro | Valor |
|-----------|-------|
| Modo actual | `OBSERVER` |
| Bloqueo activo | No (`enforced=False`) |
| Conectado al bus | Sí (canal `BROADCAST`) |
| Records máximos | 200 (circular buffer) |
| Activación | Automática en `OperatorRuntime.activate()` |

---

## Próximo paso (requiere autorización del creador)

Cuando el sistema haya observado suficiente tráfico real y el creador
decida activar inhibición efectiva:

```python
from core.nucleus.presencia_pura import get_observer
get_observer().set_mode("ACTIVE")
# A partir de aquí: enforced=True en cada decisión
# Los BLOCK y SILENCE se aplican efectivamente
```

---

*Ticket cerrado por Oz · Vectrax Nucleus · 2026-05-22*
