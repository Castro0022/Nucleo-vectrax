# ConvergenceLearner — Cierra el Ciclo de Conciencia Operacional ✅ CERRADO

**Fecha de cierre:** 2026-05-22  
**Estado:** Desplegado en producción — verificado  
**Commit:** `de49443`  
**Deploy:** Vultr `140.82.28.181` — `vectrax-core` Up (healthy)

---

## Resumen ejecutivo

> *"PresenciaObserver no debe ser un motor más. Debe tener poder de inhibición.
> Pero si no aprende, solo monitorea."*

ConvergenceLearner cierra el ciclo. No reemplaza PresenciaPura: la **entrena**.
Observa cada decisión, registra el resultado real, detecta qué motores degradan
el sistema, y propone ajustes de umbrales con evidencia — sin nunca aplicarlos
sin autorización del creador.

La conciencia operacional completa:

```
Vectrax actúa
    → PresenciaObserver evalúa
    → ConvergenceLearner observa
    → ConvergenceLearner aprende
    → ConvergenceLearner recomienda
    → El creador decide
    → PresenciaPura se afina
    → Vectrax actúa mejor
```

---

## Principio central (cableado en el código)

> Si hay convergencia clara, soberanía suficiente y bajo ruido — **Vectrax ejecuta**.
> ConvergenceLearner optimiza los umbrales para que esto siempre se cumpla.

PresenciaPura no debe volver a Vectrax tímido. Interviene solo cuando hay riesgo real.
El learner aprende de cada resultado para calibrar exactamente cuándo hay riesgo real.

---

## Módulo: `core/nucleus/convergence_learner.py`

### Tipos

| Tipo | Descripción |
|------|-------------|
| `LearnerPhase` | `OBSERVE` / `LEARN` / `RECOMMEND` / `APPLY` |
| `OutcomeQuality` | `IMPROVED` / `NEUTRAL` / `DEGRADED` / `UNKNOWN` |
| `DecisionOutcome` | Registro de decisión con validación de scores 0.0–1.0 |
| `MotorPattern` | Patrón detectado: motor + condición + degradation_rate + confidence |
| `ThresholdRecommendation` | Propuesta: umbral + valor + reasoning + status |

### Ciclo de 4 fases

```
OBSERVE
  record_decision(motor, origin, convergence, sovereignty, noise, decision)
  record_outcome(outcome_id, IMPROVED | NEUTRAL | DEGRADED)
      ↓  (mín. 10 outcomes conocidos)
LEARN
  analyze() → detecta patrones (mín. 5 muestras, 40% degradación)
      ↓  (mín. 1 patrón)
RECOMMEND
  generate_recommendations(current_thresholds)
  → ThresholdRecommendation con reasoning + evidence_pattern_id
      ↓  (mín. 1 recomendación pendiente)
APPLY
  approve_recommendation(rec_id, approved_by="creator")
  → dict {threshold_name, new_value, approved_by, applied_at}
  reject_recommendation(rec_id, reason)
  → El creador aplica el ajuste a PresenciaObserver
```

Regla absoluta: **ningún umbral se modifica sin autorización del creador.**

### Condiciones de detección de patrones

| Condición | Descripción |
|-----------|-------------|
| A | `sovereignty < 0.50` y `noise > 0.40` |
| B | `convergence < 0.50` en general |
| C | `origin=external` y `sovereignty < 0.70` |

Umbral de degradación mínimo para generar patrón: **40%**.

### Recomendaciones generadas automáticamente

| Condición del patrón | Recomendación |
|----------------------|---------------|
| `avg_sovereignty < 0.60` y `degradation > 0.50` | Subir `THRESHOLD_SOVEREIGNTY_BLOCK` (+0.08) |
| `avg_noise > 0.45` y `degradation > 0.40` | Bajar `THRESHOLD_NOISE_PAUSE` (−0.05) |

---

## Integración con PresenciaObserver

### `InhibitionRecord.learner_outcome_id`

Cada decisión de PresenciaObserver genera un `learner_outcome_id` que permite
registrar el resultado posterior cuando se conoce:

```python
# El observer genera el record con outcome_id
record = observer.evaluate(signal)

# Más tarde, cuando se conoce el resultado:
from core.nucleus.convergence_learner import get_learner, OutcomeQuality
get_learner().record_outcome(
    record.learner_outcome_id,
    OutcomeQuality.IMPROVED,   # o NEUTRAL, DEGRADED
    post_coherence_score=0.92,
    post_latency_ms=145.0,
)
```

### Auto-registro en `PresenciaObserver.evaluate()`

Cada llamada a `evaluate()` auto-registra en el learner (non-fatal):
- Si el learner falla → el observer continúa sin interrupción
- El `learner_outcome_id` queda en el record para registro posterior del outcome

---

## Conexión en arranque: `activation.py` step 4d

Al activar `OperatorRuntime`:
```
step 4c → PresenciaObserver: bus-connected (OBSERVER mode)
step 4d → ConvergenceLearner: initialized (OBSERVE phase)
```

Boot log confirma: `"ConvergenceLearner: initialized (OBSERVE phase)"`

---

## Tests: 135/135 pasando

| Suite | Tests | Estado | Tiempo |
|-------|-------|--------|--------|
| `test_convergence_learner.py` (nuevo) | 49 | ✅ 100% | 0.23s |
| `test_presencia_inhibitor.py` | 55 | ✅ 100% | 0.15s |
| `test_presencia_pura.py` | 31 | ✅ 100% | 0.15s |
| **Total** | **135** | **✅ 0 fallos** | **0.23s** |

Cobertura:
- Todas las 4 fases del ciclo
- Validación de bounds en `DecisionOutcome`
- Detección de patrones con umbral de degradación
- Generación de recomendaciones sin duplicados
- Approve / reject con estado correcto
- `advance_phase()` con gating de requisitos
- Singleton y reset
- Integración observer→learner: `learner_outcome_id` poblado
- Principio central: señales del núcleo siempre reciben PERMIT

---

## Verificación en producción (2026-05-22 01:34 UTC)

```
OK: learner.phase=observe
OK: evaluadas=13 decisiones
OK: known_outcomes=13
OK: nucleo PERMIT=7/7     (núcleo limpio siempre ejecuta — principio verificado)
OK: externo BLOCK=6/6     (LLM externos siempre evaluados — soberanía vigente)
OK: step_4d=True          (inicializado en activation.py)
```

---

## Archivos del ticket

```
core/nucleus/convergence_learner.py          +667 líneas  (nuevo)
core/nucleus/presencia_pura.py               + 25 líneas  (learner_outcome_id + auto-registro)
core/operator/activation.py                  + 10 líneas  (step 4d)
tests/integration/test_convergence_learner.py +697 líneas  (nuevo)
README.md                                     + 45 líneas  (sección ConvergenceLearner)
CHANGELOG.md                                  + 86 líneas  (entrada [2026-05-22b])
```

---

## Estado actual del núcleo cognitivo

```
Mensaje entrante (Telegram / API REST)
        │
        ▼
[Total Convergence — 7 fases obligatorias]
        │
        ▼
[PresenciaObserver — OBSERVER mode]
  evalúa y decide: PERMIT/PAUSE/SILENCE/BLOCK
  auto-registra en ConvergenceLearner (non-fatal)
        │
        ▼
[Smart Router → respuesta]
```

Paralelamente (acumulación en background):
```
ConvergenceLearner.outcomes[] ← crece con cada evaluate()
    ↓ (cuando hay suficientes datos + autorización)
ConvergenceLearner.patterns[]
ConvergenceLearner.recommendations[]
    ↓ (el creador aprueba)
PresenciaObserver.THRESHOLD_* se afina
```

---

## Próximos pasos disponibles

**Para activar inhibición real** (cuando haya datos y autorización):
```python
from core.nucleus.presencia_pura import get_observer
get_observer().set_mode("ACTIVE")
```

**Para avanzar el learner a LEARN** (cuando haya ≥10 outcomes conocidos):
```python
from core.nucleus.convergence_learner import get_learner
get_learner().advance_phase()
```

**Para consultar recomendaciones pendientes**:
```python
print(get_learner().report())
```

---

*Ticket cerrado por Oz · Vectrax Nucleus · 2026-05-22*
