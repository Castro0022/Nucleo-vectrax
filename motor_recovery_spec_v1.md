# Motor por Error: Especificación v1

## Tesis en una línea
Motor de orquestación de fallos que detecta clases de error estructurales y ejecuta recuperación declarativa sin patches manuales, escalando a humanos solo cuando la recurrencia lo exige.

## Principios operacionales

1. **Detección de punta a punta, no superficial**: Cada detector observa síntomas sistémicos (silencio de webhooks, D-state, latencia en DB, certificados vencidos) con umbrales temporales objetivos, no regexes frágiles.

2. **Estrategias declarativas, no imperativas**: Cada clase especifica precondiciones, acciones tipadas y condiciones de escalada. Sin lógica ad-hoc; sin if-then-else en el executor.

3. **Un motor, una ledger, una verdad**: Toda acción recuperativa se registra en JSONL; sin estado disperso. El ledger es auditable y replicable.

4. **Escalación inteligente por recurrencia**: No restartear infinitamente. Si un evento reaparece N veces en M segundos, ⬆ a humano.

5. **No hay patch sin clase**: Cualquier bug que aparezca 2+ veces y requiera patch manual debe volverse una Clase del motor.

6. **Fases de deploy: deshabilitado → dry-run → real**: Fase 2 activa; clase A live; B–G en dry-run acumulando 529 probes sin falsos positivos.

## Arquitectura: 3 capas

### HealthOrchestrator
- **Rol**: Ejecutor de detectors en loop (intervalo configurable por clase).
- **Entrada**: spec declarativa de clases.
- **Salida**: eventos `(timestamp, class, evidence)`.
- **Ubicación**: `core/recovery/health_orchestrator.py`

### RecoveryStrategy
- **Rol**: Mapeo `class → (preconditions, actions[], escalation_rule)`.
- **Ubicación**: `core/recovery/strategies/` (un archivo por clase: `class_a.py`, `class_b.py`, …)
- **Interfaz**: `detect(evidence) → bool`, `execute(executor) → result`.

### RecoveryExecutor
- **Rol**: Único punto de ejecución de acciones tipadas. Registra en ledger. Implementa rollback y throttle.
- **Acciones permitidas**: RestartContainer, SetEnv, CallHttp, NotifyHuman, ReloadCaddy, RenewCert.
- **Prohibido**: ExecShell (sin escape hatches).
- **Ubicación**: `core/recovery/executor.py`
- **Ledger**: `/var/log/vectrax/recovery_ledger.jsonl`

**Árbol de archivos propuesto:**
```
core/recovery/
├── __init__.py
├── health_orchestrator.py
├── executor.py
├── ledger.py
├── strategies/
│   ├── __init__.py
│   ├── class_a.py    (gateway silent)
│   ├── class_b.py    (gateway stuck D-state)
│   ├── class_c.py    (telegram 5xx/rate limit)
│   ├── class_d.py    (db lock/disco lleno)
│   ├── class_e.py    (tls/cert vencido)
│   ├── class_f.py    (config drift)
│   └── class_g.py    (hardcoded handler)
└── tests/
    └── test_strategies.py
```

## Clases de error: detector + estrategia

### Clase A: Gateway silencioso (USE_WEBHOOK=1 sin webhook registrado)
- **Detector**: Verifica cada 60s si USE_WEBHOOK=1 pero `telegram.webhook_url` vacío o sin ACK en telemetría.
- **Precondiciones**: USE_WEBHOOK=1.
- **Acciones**: SetEnv(USE_WEBHOOK → 0), NotifyHuman("webhook no configurado").
- **Throttle**: 1 ejecución por ventana de 300s.
- **Escalación**: Ninguna; acción única.
- **Requiere aprobación humana**: No.
- **Estado**: **EN PRODUCCIÓN**.

### Clase B: Gateway atascado (D-state, crash recurrente ~3h)
- **Detector**: Suma `Died` events en ventana de 3600s. Umbral: N ≥ 3 → escalada.
- **Precondiciones**: `systemd-journalctl` accesible; proceso Telegram gateway tracked.
- **Acciones (por frecuencia)**:
  - 1er evento en ventana: RestartContainer("telegram_gateway"), registra timestamp.
  - 2do–3er evento: RestartContainer, pero NO reinicia automáticamente si ocurrió hace <120s (palliativo B2).
  - 4to evento (escalación): NotifyHuman("gateway crash recurrente: 3+ eventos en 1h; requiere diagnóstico"), **STOP auto-restart**.
- **Throttle**: 1 restart por 120s (palliativo).
- **Escalación**: Si recurrencia > 3 eventos/hora → humano (no loop de restarts).
- **Requiere aprobación humana**: Sí (en escalación).
- **Estado**: Dry-run (esperar 14h de observación; fase 2).

### Clase C: Telegram 5xx / rate limit (429)
- **Detector**: Scrape de logs Telegram; umbral 5xx ≥ 5 en 60s o 429 recibido.
- **Precondiciones**: Logs accesibles.
- **Acciones**: 
  - 1era ocurrencia: Esperar 30s, luego reintentar vía circuit breaker (backoff exponencial).
  - Sostenido >5min: SetEnv(TELEGRAM_BACKOFF → 60s), NotifyHuman("rate limit sostenido").
- **Throttle**: 1 acción por 120s.
- **Escalación**: Si >10 min de backoff → human approval para SetEnv.
- **Requiere aprobación humana**: Sí (SetEnv sostenido).
- **Estado**: Dry-run (fase 2).

### Clase D: DB lock / disco lleno
- **Detector**: Connectable a DB cada 30s; si timeout > 5s o du >= 95% en data disk → evento.
- **Precondiciones**: Acceso a df, psql.
- **Acciones**:
  - Lock: RetryWithBackoff + NotifyHuman.
  - Disco >= 95%: SetEnv(READ_ONLY_MODE → 1), NotifyHuman("disco casi lleno"), **bloquea writes**.
- **Throttle**: 1 acción por 300s.
- **Escalación**: Si disco >= 98% → fuerza human approval para limpieza.
- **Requiere aprobación humana**: Sí (entrada READ_ONLY).
- **Estado**: Dry-run.

### Clase E: TLS / certificado webhook vencido
- **Detector**: Valida cert cada 3600s. Si días_hasta_expiración < 7 → evento.
- **Precondiciones**: Caddy running; permisos de reescritura en /etc/caddy.
- **Acciones**: RenewCert(service="telegram_webhook"), ReloadCaddy(), NotifyHuman("cert renovado").
- **Throttle**: 1 renovación por 86400s (1 día).
- **Escalación**: Si renovación falla 2 veces → human + manual Caddy check.
- **Requiere aprobación humana**: No (cert auto-renovable vía RenewCert).
- **Estado**: Dry-run.

### Clase F: Config drift post-deploy (rsync unauthorized change)
- **Detector**: Hash MD5 de `.env` inmediatamente post-rsync deploy vs. expected hash en metadata. Desviación → evento.
- **Precondiciones**: Checksum de `.env` pre-deploy registrado en ledger.
- **Acciones**: NotifyHuman("config drift detectado; cambios no autorizados"), **PAUSE recovery** hasta aprobación.
- **Throttle**: 1 notificación por 3600s.
- **Escalación**: Automática a humano (no auto-fix).
- **Requiere aprobación humana**: Sí (antes de seguir).
- **Estado**: Dry-run (NEW).

### Clase G: Handler hardcodeado que evita LLM
- **Detector (tier 1 - runtime)**: Mensaje conversacional entra; respuesta sale sin pasar por `llm_response()`. Pattern: `ConversationMessage` → respuesta no-LLM.
- **Detector (tier 2 - static)**: Pre-commit hook scan por patterns `return get_product_identity(...)`, `return describe_user(...)`, etc. fuera de `IdentityHandler.respond_if_identity()`.
- **Precondiciones**: Logs de conversación; acceso a git pre-commit.
- **Acciones**: 
  - Runtime: NotifyHuman("handler hardcodeado detectado; conversación #ID desviada"), registra ubicación en stack trace.
  - Static: Pre-commit bloquea push; sugerencia: "refactor a `IdentityHandler.respond_if_identity()`".
- **Throttle**: 1 notificación por ID de conversación por 300s.
- **Escalación**: Si 3+ conversaciones/hora → alert crítico + flag en dashboard.
- **Requiere aprobación humana**: Sí (propuesta de refactor).
- **Estado**: Dry-run (NEW).

## Acciones tipadas (allow-list)

```python
RestartContainer(service_name: str) → bool
SetEnv(key: str, value: str) → bool
CallHttp(method: str, url: str, payload: dict, timeout_s: int) → (status, body)
NotifyHuman(message: str, severity: "info"|"warning"|"critical") → bool
ReloadCaddy() → bool
RenewCert(service: str) → bool
```

**Prohibido**: `ExecShell` (sin escape hatches; toda ejecución debe ser tipada).

## Ledger: JSONL en `/var/log/vectrax/recovery_ledger.jsonl`

```json
{
  "timestamp": "2026-04-23T14:32:10.123Z",
  "class": "B",
  "detector_evidence": {
    "event_count": 3,
    "window_seconds": 3600,
    "last_died_timestamp": "2026-04-23T14:30:00Z"
  },
  "action_taken": "RestartContainer",
  "action_params": {"service_name": "telegram_gateway"},
  "result": "success",
  "result_details": {"container_id": "abc123"},
  "rollback_performed": false,
  "escalation_triggered": false,
  "human_approval_required": true,
  "approved_by": null,
  "notes": ""
}
```

## Feature flags para deploy seguro

```python
RESILIENCE_ENABLED = False  # Fase 1: deshabilitado por defecto
RESILIENCE_DRY_RUN = True   # Fase 2: registra, no ejecuta
RESILIENCE_PHASE_3 = False  # Fase 3: ejecución real (deploy futuro)

RECOVERY_INTERVAL_CLASS_A = 60      # segundos
RECOVERY_INTERVAL_CLASS_B = 180     # segundos (escalación si 3 eventos/hora)
RECOVERY_INTERVAL_CLASS_C = 120     # segundos
RECOVERY_INTERVAL_CLASS_D = 300     # segundos
RECOVERY_INTERVAL_CLASS_E = 3600    # segundos
RECOVERY_INTERVAL_CLASS_F = 3600    # segundos
RECOVERY_INTERVAL_CLASS_G = 300     # segundos

RECOVERY_ESCALATION_THRESHOLD_B = 3  # crashes en ventana de 3600s
RECOVERY_ESCALATION_THRESHOLD_G = 3  # conversaciones/hora
```

## Criterios de aceptación motor v1

- [ ] Clase A (gateway silent): detecta USE_WEBHOOK=1 sin webhook en <60s, ejecuta SetEnv.
- [ ] Clase B (crash recurrente): detecta ≥3 Died en 3600s, escalada a humano SIN loop infinito de restarts.
- [ ] Clase C (Telegram 5xx): detects 5xx/429, backoff exponencial, notifica si >5min sostenido.
- [ ] Clase D (DB/disco): detects lock & disk ≥95%, SetEnv READ_ONLY_MODE, notifica.
- [ ] Clase E (TLS): detecta cert vencido en <7 días, RenewCert automático, notifica.
- [ ] Clase F (config drift): detecta .env hash mismatch, PAUSA, notifica humano.
- [ ] Clase G (hardcoded handler): runtime detector emite alerta; static pre-commit scan bloquea nuevas instancias.
- [ ] Ledger JSONL: toda acción registrada, auditable, replicable.
- [ ] Fase 2 stable: 529+ probes, 0 BROKEN emitidos, <14h observación completa.
- [ ] Zero false positives en umbral de escalación clase B (confirmar 3+ restarts reales vs. ruido).

## Fuera de scope v1

- Detección ML / anomaly detection (v2).
- Auto-fix de código (class G propone refactor; humano aprueba + ejecuta).
- Dashboard web de recovery (v2; CLI ledger dump interim).
- Integración con observability externo (Datadog, etc.; v2).

## Plan de implementación por fases

1. **Clase A** (HECHO): SetEnv + notificación.
2. **Clase B** (PRÓXIMA): Escalación por recurrencia; bloquea restarts infinitos.
3. **Clase G** (PARALELA a B): Runtime detector + pre-commit static scan. Alta visibilidad.
4. **Clase E** (DESPUÉS): Auto-cert renewal; bajo riesgo.
5. **Clase D** (DESPUÉS): DB/disco; requiere testing en staging.
6. **Clase C** (FINAL): Circuit breaker Telegram; depende de logging external.
7. **Clase F** (POST-V1): Config drift refinement basado en rsync learnings.

**Esfuerzo estimado**:
- Clase B: 3–4 días (lógica de recurrencia, tests).
- Clase G: 2–3 días (detectors tier 1 + tier 2, pre-commit hook).
- Clase E: 1–2 días (RenewCert integration).
- Clases D, C: 2 días c/u.
- Clase F: 1 día (post-v1 refinement).

## Antipatrones a prevenir

**Core mandate**: Cualquier bug que aparezca >1 vez y requiera patch manual debe volverse una Clase declarada en el motor, no otro patch.

- ❌ Tres copias del mismo anti-pattern (`return get_product_identity(...)` hardcoded) en tres archivos (hoy).
- ❌ Restarts infinitos en modo D-state (B2 palliativo observado).
- ❌ Cambios .env no autorizados post-deploy (rsync accident hoy).
- ✅ **Clase G**: detecta y escala antes de que reaparezca.
- ✅ **Clase F**: hash-check post-deploy; humano aprueba.
- ✅ **Clase B escalada**: N restarts → humano, no loop.

---

**Documento versión**: v1.0  
**Fecha**: 2026-04-23  
**Responsable**: Mario Bravo Castro  
**Próxima revisión**: Después de completar fase 2 (14h observación restante).
