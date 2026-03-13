# Vectrax — Filosofía Operacional: Política de Autonomía Progresiva

**Versión:** 1.0
**Fecha:** 2026-03-02
**Estado:** Producción Interna

---

## 1. Propósito del Sistema

Vectrax es un motor de cognición autónoma local-first diseñado para operar como asistente inteligente con capacidad de auto-evolución controlada. El sistema puede analizar, proponer y, bajo condiciones estrictas, aplicar cambios a su propia base de código.

**Principio fundamental:** Vectrax opera bajo **autonomía supervisada** — nunca toma decisiones destructivas sin confirmación humana, y toda acción es trazable y reversible.

---

## 2. Definición de Autonomía Supervisada

La autonomía supervisada es un modelo operativo donde el sistema:

1. **Observa** su propio estado de salud continuamente (Governor, Risk Engine)
2. **Propone** cambios basados en análisis de código (Proposal Engine)
3. **Evalúa** el riesgo de cada cambio (Risk Engine — 6 señales probabilísticas)
4. **Clasifica** cada archivo afectado por zona de seguridad
5. **Solicita confirmación humana** por defecto para toda operación
6. **Permite auto-apply** solo cuando TODAS las condiciones de seguridad se cumplen

El switch global `auto_apply_enabled` está **apagado por defecto**. Incluso activado, las protecciones de zona y hard-limits prevalecen siempre.

---

## 3. Zonas del Sistema

### 3.1 Core Sagrado (SACRED_CORE) — 🔴 Nunca Auto-Apply

Archivos críticos para la integridad, seguridad y gobernanza del sistema. **Ningún cambio automático está permitido bajo ninguna circunstancia.**

**Rutas:**
- `core/governor.py` — Motor de gobernanza
- `core/risk_engine.py` — Motor de riesgo probabilístico
- `core/state_manager.py` — Gestor de estado persistente
- `core/smoke.py` — Verificador de salud
- `core/autonomy_policy.py` — Política de autonomía (este módulo)
- `core/resilience/` — Módulos de resiliencia (retry, rate limiter, validación)
- `core/routing/` — Router inteligente y circuit breaker
- `core/abstraction/` — Capa de abstracción de proveedores
- `schema.sql` — Esquema de base de datos
- `.env` — Variables de entorno y secretos
- `vault/` — Almacén de datos sensibles
- `config/config.yaml` — Configuración principal del sistema
- `config/autonomy.json` — Configuración de autonomía

### 3.2 Zona Semi-Segura (SEMI_SAFE) — 🟡 Siempre Requiere Confirmación

Componentes funcionales importantes que requieren revisión humana antes de cualquier modificación.

**Rutas:**
- `core/proposal_engine.py` — Motor de propuestas
- `core/autopatch.py` — Motor de auto-reparación
- `core/meta_loop.py` — Loop cognitivo principal
- `core/shadow_mode.py` — Modo sombra de observación
- `core/ingest.py` — Pipeline de ingesta
- `core/memory_sqlite.py` — Memoria persistente
- `core/providers/` — Proveedores LLM
- `core/workflows/` — Orquestador de flujos
- `cli/` — Interfaz de línea de comandos
- `scripts/` — Scripts del sistema
- `app.py`, `nucleo.py`, `vectrax_engine.py`, `vectrax_unified.py` — Puntos de entrada (legacy)
- `setup.py`, `requirements.txt` — Configuración de dependencias

### 3.3 Zona Flexible (FLEXIBLE) — 🟢 Auto-Apply Posible (bajo condiciones)

Archivos de bajo riesgo donde cambios automáticos son posibles si se cumplen todos los umbrales.

**Rutas:**
- `core/daily_report.py` — Reportes diarios
- `core/reporter.py` — Motor de reportes
- `core/observability/` — Logging, métricas, tracing
- `core/rules.py` — Reglas de procesamiento
- `docs/` — Documentación
- `reports/` — Reportes generados
- `logs/` — Archivos de log
- `test_*.py` — Tests del sistema

---

## 4. Umbrales y Condiciones de Auto-Apply

Para que un cambio sea candidato a auto-apply, **TODAS** las siguientes condiciones deben cumplirse simultáneamente:

| Condición | Umbral | Razón |
|-----------|--------|-------|
| Switch global | `auto_apply_enabled = true` | Desactivado por defecto — control explícito del operador |
| Zona del archivo | `FLEXIBLE` únicamente | Core Sagrado y Semi-Segura siempre requieren confirmación |
| Risk Score | `< 0.15` | Solo cambios de riesgo muy bajo (el engine mide 6 señales) |
| Confidence Score | `> 0.90` | El sistema debe estar altamente seguro de su evaluación |
| Tamaño del diff | `≤ 50 líneas` | Cambios pequeños y contenidos |
| Hard limits | No aplica a secretos/credenciales | Bloqueo absoluto sin excepciones |

**Si cualquier condición no se cumple, se requiere confirmación humana.**

---

## 5. Reglas de Confirmación Humana

1. **Por defecto, TODO requiere confirmación.** El switch `auto_apply_enabled` está en `false`.
2. **Core Sagrado NUNCA puede auto-apply**, incluso con el switch activado.
3. **Zona Semi-Segura SIEMPRE requiere confirmación**, incluso con el switch activado.
4. **Zona Flexible** puede ser candidata a auto-apply SOLO si:
   - El switch global está activado
   - El risk_score, confidence_score y tamaño del diff cumplen los umbrales
   - Ningún archivo afectado es un hard-limit
5. **Las propuestas que afectan múltiples zonas** se rigen por la zona más restrictiva.
6. **`vx propose` siempre muestra** la clasificación de zona, la decisión y la justificación antes de cualquier acción.

---

## 6. Hard Limits (Protecciones Absolutas)

Los siguientes archivos/patrones están **BLOQUEADOS PERMANENTEMENTE** de cualquier cambio automático. Estos límites no pueden ser sobrepasados por ningún modo, switch o configuración:

### Patrones de ruta bloqueados:
- `.env` — Variables de entorno y secretos
- `vault/` — Almacén seguro
- `.git/` — Control de versiones
- `keys/` — Llaves criptográficas
- `secrets/` — Secretos del sistema

### Extensiones bloqueadas:
- `.db`, `.db-shm`, `.db-wal` — Bases de datos
- `.pem`, `.key` — Certificados y llaves

### Palabras clave en nombre de archivo:
- `secret`, `credential`, `token`, `api_key`, `apikey`, `password`, `private_key`

---

## 7. Principios de Seguridad y No-Autodestrucción

1. **No-autodestrucción:** Vectrax nunca puede modificar automáticamente los archivos que controlan su propia gobernanza (governor, risk_engine, autonomy_policy).

2. **Trazabilidad total:** Cada evaluación de riesgo registra las 6 señales, sus pesos y contribuciones. Cada propuesta incluye clasificación de zona y justificación.

3. **Reversibilidad:** El sistema autopatch crea checkpoints git antes de cualquier cambio y puede hacer rollback automático si smoke tests fallan.

4. **Fail-safe:** Si el Risk Engine, Governor o Autonomy Policy fallan en evaluar, la decisión por defecto es **bloquear** el cambio.

5. **Escalamiento conservador:** Rutas desconocidas se clasifican como Zona Semi-Segura (requiere confirmación). Nunca se asume que un path desconocido es seguro.

6. **Separación de poderes:** El Governor controla los modos operativos, el Risk Engine evalúa probabilísticamente, y la Autonomy Policy clasifica zonas — ninguno puede unilateralmente autorizar cambios en Core Sagrado.

7. **Auditoría:** Todos los umbrales, zonas y decisiones están definidos en código (`core/autonomy_policy.py`) y configuración (`config/autonomy.json`), no en lógica implícita.

---

## 8. Integración con `vx propose`

Cuando se ejecuta `vx propose "descripción"`, el sistema:

1. Analiza qué archivos necesitan cambiar (vía LLM)
2. Genera el código propuesto para cada archivo
3. Clasifica cada archivo por zona de autonomía
4. Calcula `risk_score` (Risk Engine — 6 señales) y `confidence_score`
5. Evalúa si auto-apply es posible para cada archivo
6. Muestra al operador:
   - Resumen de la propuesta
   - Clasificación por zona (🔴/🟡/🟢) de cada archivo
   - Decisión de auto-apply y justificación
   - Risk breakdown completo
   - Diff completo de todos los cambios
7. **Solicita confirmación humana** (siempre, en la implementación actual)

---

## 9. Configuración

La política se configura en `config/autonomy.json` bajo la sección `progressive_autonomy`:

```json
{
  "progressive_autonomy": {
    "auto_apply_enabled": false,
    "max_risk_score": 0.15,
    "min_confidence_score": 0.90,
    "max_diff_lines": 50
  }
}
```

Para activar auto-apply (solo en Zona Flexible):
```json
"auto_apply_enabled": true
```

---

*Documento generado como parte de la Política de Autonomía Progresiva de Vectrax.*
*Implementado en `core/autonomy_policy.py`. Tests en `test_autonomy_policy.py`.*
