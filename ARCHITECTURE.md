# Vectrax — Arquitectura del Sistema

**Versión:** Septiembre 2026 (actualizado tras auditoría E2E)  
**Creador:** Mario Bravo Castro  
**Entorno de producción:** Mac local · launchd (`com.vectrax.supervisor`) · sin Docker ni Vultr

> **Nota de procedencia:** desde el 2026-08-08 (PR #88, ver `CHANGELOG.md`) la
> producción corre **localmente** en esta Mac, supervisada por `launchd`. El
> despliegue anterior en Vultr (`140.82.28.181`, Docker + Caddy) fue retirado;
> `deploy_vultr.sh` ya no existe en el repo. Esta sección y las secciones 7–9
> y 12 reflejan el despliegue REAL verificado por auditoría E2E de solo
> lectura el 2026-09-02 (procesos vivos, `/health`, logs de producción) — no
> solo lo que el código del repo permite hacer.

---

## 1. Visión General

Vectrax es un organismo digital autónomo con memoria persistente, aprendizaje
continuo y capacidad de acción gobernada. No es un chatbot ni un wrapper de LLM:
es un sistema que percibe, aprende, propone y —con autorización explícita— actúa.

```
┌──────────────────────────────────────────────────────────────────────┐
│              VECTRAX CORE (Mac local · launchd, sin Docker)         │
│                                                                     │
│  Telegram ──► Gateway ──► Queue ──► Worker Pool ──► LLM / Memory   │
│  (long-poll,                            │                          │
│   USE_WEBHOOK=0)             ┌───────────┴───────────┐             │
│                              │    Background Cycles  │             │
│                              │  Market Learn (30min) │             │
│                              │  Domain Learn Thread  │             │
│                              │   freight/real_estate/│             │
│                              │   cyber (6h, aislado  │             │
│                              │   del main loop)      │             │
│                              │  Conv. Learner (24h)  │             │
│                              │  Proactive Nudges(10m)│             │
│                              └────────────────────────┘             │
│                                                                     │
│  REST API (:8900) — bind local, sin proxy TLS público en este host │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Estructura de Módulos

```
vectrax/
├── connectors/
│   ├── etoro/              Trading eToro (señales, patrones, ejecución)
│   │   ├── etoro_client.py         REST client con circuit breaker
│   │   ├── learning_engine.py      Ciclo de aprendizaje de mercado
│   │   ├── pattern_memory.py       Persistencia de patrones
│   │   ├── signal_recorder.py      Registro de señales individuales
│   │   ├── outcome_tracker.py      Seguimiento de outcomes
│   │   ├── auto_executor.py        Ejecución gobernada (OFF/PAPER/LIVE)
│   │   ├── entry_validator.py      9 condiciones de entrada
│   │   └── convergence_learner.py  Detección de deriva de umbrales
│   │
│   ├── alpaca/             Trading Alpaca (paper trading alternativo)
│   │   └── alpaca_client.py        Client con kill switch y audit
│   │
│   ├── broker.py           Router BROKER_PROVIDER (eToro | Alpaca)
│   │
│   └── freight/            Dominio Freight/Truck Broker
│       ├── base.py                 FreightFeedProvider (interfaz abstracta)
│       ├── simulator_adapter.py    SimulatorAdapter + RealFeedAdapter stub
│       ├── __init__.py             Factory get_provider()
│       └── learning_cycle.py      Ciclo periódico ingest→elevate
│
├── core/
│   ├── transport/
│   │   └── pipeline_worker.py     Worker principal + todos los ciclos
│   ├── gravity/
│   │   ├── vector_store.py        SQLite vector store (deep_memory)
│   │   ├── mass_tracker.py        MassKind (VISION/PERSONA/EMOCION/SALUD)
│   │   └── retrieval.py           Retrieval con ranking combinado
│   ├── domain_ingester.py         ingest_event() — fingerprint estable
│   ├── domain_knowledge.py        elevate_pattern() — idempotente
│   ├── continuity_reentry.py      Máquina de estados 3 nudges
│   ├── tenant.py                  Gestión de tenants y dominios
│   ├── learn/
│   │   ├── gravity_engine.py      GravityIndex (stars, convergencias)
│   │   └── criterion.py           Motor de Criterio Aprendido #48 (opinión cross-dominio, grounded)
│   └── nucleus/
│       ├── presencia_pura.py      Capa inhibitoria (Sovereignty)
│       └── convergence_learner.py Learner operacional (PresenciaPura)
│
├── scripts/
│   └── freight_simulator.py       Generador sintético de eventos freight
│
├── config/
│   └── domain_templates/
│       └── freight_logistics.json  Tipos de eventos + signature_fields
│
├── services/
│   └── core/
│       ├── app.py                 FastAPI :8900
│       ├── auth.py                HTTPBearer + RBAC
│       └── routes/                Endpoints por dominio
│
└── vectrax/
    └── telegram_gateway.py        Gateway Telegram (polling/webhook)
```

---

## 3. Dominio Trading

### Pipeline completo

```
eToro API
    │
    ▼  (cada 30min en pipeline_worker, ThreadPool + timeout 60s)
learning_engine.run_learning_cycle(symbols)
    │
    ├──► signal_recorder  →  etoro_signals.jsonl   (266 señales hoy)
    │                        [signal_id, symbol, direction, entry,
    │                         outcome, win/loss/neutral, resolved_at]
    │
    ├──► outcome_tracker  →  resuelve señales con precios reales
    │
    └──► pattern_memory   →  etoro_patterns.json   (68 patrones)
                             [n, wr, expectancy, quality_tier: LOW/MEDIUM/HIGH]

Estado: WR observado = 43.1% | threshold = 60% | 0 patrones usables
```

### Auto Executor (gobernado)

```
Modos: OFF → PAPER → LIVE (solo con autorización explícita del creador)

Condiciones para LIVE:
  ✗ ≥30 paper signals resueltas   (hoy: 0)
  ✗ WR ≥60% en paper             (hoy: N/A)
  ✗ approved_symbols no vacío    (hoy: [])
  ✗ activate_live() explícito    (hoy: no activado)

Límites hard-coded:
  max_position_usd    = $50
  max_daily_loss_usd  = $10
  stop_loss_pct       = 1.5%
  max_consecutive_losses = 3  → auto-revert a PAPER
  max_positions_open  = 2
```

### Convergence Learner (Trading)

```
TradingConvergenceLearner (cada 24h)
  Observa: signals + patterns + auto_executor config
  Detecta:
    WR_GAP      observed_wr < threshold - 5pp  → propuesta de revisión
    SAMPLE_GAP  best_n < min_paper_signals      → propuesta informativa
    EXPECTANCY  EV+ agregado, 0 usables         → propuesta de monitoreo
    DORMANCY    sin outcomes en >3 días          → alerta de conectividad

  Persiste: ~/.vectrax/etoro_learner_proposals.jsonl
  NUNCA muta: etoro_auto_config.json
  Autoriza: creator vía approve_proposal(id)
```

### Broker Routing

```
BROKER_PROVIDER env var (default: etoro)
         │
         ▼
connectors/broker.py :: get_provider()
         │
         ├── "etoro"  → etoro_client  (activo en prod)
         └── "alpaca" → alpaca_client (requiere ALPACA_API_KEY + alpaca-py)
```

---

## 4. Dominio Freight / Truck Broker

### Arquitectura en capas

```
FREIGHT_FEED_PROVIDER env var (default: simulator)
         │
         ▼
connectors/freight :: get_provider()
         │
         ├── "simulator" → SimulatorAdapter(WorldState)
         │                 [8 event types, 6 regiones, 8 carriers]
         │
         └── "real"      → RealFeedAdapter (stub — plug-in ready)
                           [Implementar con DAT / Truckstop / CRM]

         │  stream_events(200) → [FreightEvent]
         ▼
FreightLearningCycle (cada 6h en pipeline_worker, ThreadPool + timeout 120s)
         │
         ├── ingest_event() × N  →  GravityIndex (stars, hits)
         │                          fingerprint = domain:event_type:signature_categórica
         │                          signature_fields definidos en freight_logistics.json
         │
         └── try_elevate_from_gravity() × 1  →  domain_library/freight_logistics.json
                                                 (cuando star.hits ≥ 15)
```

### Fingerprint estable (corrección crítica)

```python
# ANTES (bug): hash(all_data) → cada evento = star única → nunca madura
fingerprint = f"{domain}:{event_type}:{_hash_data(data)}"

# AHORA (correcto): firma categórica de baja cardinalidad
fingerprint = f"{domain}:{event_type}:{_conditions_signature(event_type, data, template)}"
# Ejemplo: "freight_logistics:delay_reported:carrier~FastHaul|cause~weather|region~Southeast"
```

### Estado en producción

```
gravity_index.json: 719 stars totales, 662 freight
  Pre-fix: máx hits=3, 0 maduras (fingerprints únicos)
  Post-fix (validado, 800 eventos): 172 stars, 17 maduras, 17 patrones elevados

domain_library/market.json: existe (68 patrones eToro)
domain_library/freight_logistics.json: pendiente primer ciclo post-deploy
```

---

## 5. Memoria Gravitacional

```
deep_memory (SQLiteVectorStore — ~/.vectrax/gravity.db)
  Columnas: id, user_id, raw_text, summary, embedding_json, tags_json,
            mass, ts, gravity_score, retrieval_count, memory_status,
            memory_type, last_retrieved_at, fused_into

MassKind:
  VISION  = "vision"   mass=5    (imágenes, OCR)
  PERSONA = "persona"  mass=8    (contactos registrados)
  EMOCION = "emocion"  mass=10   [SENSITIVE — excluido de nudges]
  SALUD   = "salud"    mass=10   [SENSITIVE — excluido de nudges]

Retrieval ranking:
  final_score = semantic × 0.55 + mass × 0.25 + recency × 0.10 + retrieval × 0.10

Nudge context filter (_is_sensitive_topic):
  Layer 1: tags_json intersect _SENSITIVE_TAGS
  Layer 2: keyword regex en raw_text/summary
  → Nunca aparece en mensajes de presencia al usuario
```

---

## 6. Presencia y Nudges

```
Máquina de estados por usuario (continuity_reentry.py)

  new user
    │
    ├── record_activity() → nudge_count=0, next_nudge_after=now+random(12-20h)
    │
    ▼ (pipeline_worker cada 10min)
  check_reentry() — solo 09:00-21:00 hora local
    │
    ├── nudge #1 (12-20h) → tono apertura + contexto sesión + gravity topics
    ├── nudge #2 (3-5d)   → tono continuidad
    └── nudge #3 (21-30d) → tono despedida suave → DORMANT
         │
         └── DORMANT: silencio indefinido hasta mensaje entrante
              └── record_activity() → reset completo → ciclo reinicia

Contexto del prompt (por nivel):
  1. Sesión reciente: últimos 3-5 turnos (ventana 8h)
  2. Memoria gravitacional: top-3 topics por mass+gravity+retrieval
  3. Filtro sensible: excluye EMOCION y SALUD (dual-layer: tag + keyword)
```

---

## 7. Despliegue

### Infraestructura

```
Host: Mac local (macOS) · supervisado por launchd · sin Docker, sin Vultr, sin Caddy

Servicios (gestionados por com.vectrax.supervisor vía vectrax_supervisor.py):
  telegram_gateway  python -m vectrax.telegram_gateway   (long-poll, USE_WEBHOOK=0)
  pipeline_worker   python -m core.transport.pipeline_worker
  core_api          python -m uvicorn services.core.app:app --host 0.0.0.0 --port 8900
  meta_loop         python vectrax_unified.py
  audit_cron        python -m observability.audit_cron --loop

launchd agents adicionales (independientes del supervisor):
  com.vectrax.backup-db     diario 03:30 → scripts/backup_db.sh
  com.vectrax.rotate-logs   semanal dom 04:00 → scripts/rotate_logs.sh

Red:
  Puerto 8900: bind 0.0.0.0, alcance de facto local (sin proxy TLS público en
  este host; a diferencia del Caddy/api.vectrax.app del despliegue Vultr retirado)
  Sin puertos 80/443 gestionados por Vectrax en este host

Persistencia:
  ~/.vectrax/       runtime (DBs, logs, gravity_index.json, domain_library/)
  <repo>/vault/     DBs de dominio (convergence_history.db, operational_cycles.db)
  Backups: ~/vectrax_backups/ + iCloud (ver docs/OPERATIONS.md)

Arranque / reinicio (runbook completo en docs/OPERATIONS.md):
  launchctl kickstart -k gui/$(id -u)/com.vectrax.supervisor   # reinicio
  launchctl bootout   gui/$(id -u)/com.vectrax.supervisor      # parar
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vectrax.supervisor.plist  # arrancar
```

### Variables de entorno clave

```
# Obligatorias
TELEGRAM_BOT_TOKEN            Bot de Telegram
OPENAI_API_KEY                LLM principal
ETORO_API_KEY                 eToro REST API
ETORO_USER_KEY                 eToro JWT de usuario
ETORO_ENVIRONMENT              "real" | "demo"

# Opcionales / Feature flags
USE_WEBHOOK                    0 (default local, long-poll) | 1 (requiere WEBHOOK_BASE_URL)
BROKER_PROVIDER                etoro (default) | alpaca
FREIGHT_FEED_PROVIDER          simulator (default) | real — sin flag enable/disable, siempre corre
REAL_ESTATE_FEED_PROVIDER      simulator (default) | rentcast | attom
REAL_ESTATE_LEARN_ENABLED      1 (default) — exige proveedor real salvo REAL_ESTATE_ALLOW_SIMULATOR=1
CYBER_FEED_PROVIDER            simulator (default) | nvd
CYBER_LEARN_ENABLED            0 (default) — exige proveedor real salvo CYBER_ALLOW_SIMULATOR=1
PROACTIVE_ENGINE_ENABLED       0 (default — activar con "1")
CONTINUITY_REENTRY_ENABLED     1 (default)
ALPACA_API_KEY                 Requiere alpaca-py instalado
ALPACA_SECRET_KEY              —
ALPACA_PAPER                   true (default)
```

### Proceso de ciclos en pipeline_worker

| Ciclo | Intervalo | Timeout | Dónde corre | Propósito |
|---|---|---|---|---|
| Market learning (eToro) | 30 min | 60s | main loop (ThreadPool) | Señales → patrones |
| Freight / Real estate / Cyber learning | 6h c/u | 120s c/u | **hilo daemon dedicado** `_domain_learning_thread` | Eventos → stars → elevación |
| Gravity sync | 6h | — | main loop | gravity_index.json → vectrax.db |
| Trading convergence learner | 24h | — | main loop (inline) | Detección de deriva de umbrales |
| Presence nudges | 10 min | — | main loop | Mensajes proactivos 3-nudge |
| Proactive engine | 10 min | — | main loop | Recordatorios (OFF por defecto) |
| Scheduler | 60s | — | main loop | Tareas programadas |
| Router digest | configurable | — | main loop | Telemetría de routing |

> **Fix 2026-09-02:** freight/real_estate/cyber corrían antes síncronos en el
> main loop y superaban `MAIN_LOOP_WATCHDOG_TIMEOUT` (60s), forzando
> `os._exit(1)` cada ~6h sin completar un solo ciclo (14 reinicios en 72h, 0
> ciclos completados — ver auditoría E2E). Ahora corren en
> `_domain_learning_thread`, un hilo daemon separado: su duración ya no
> afecta al watchdog del main loop, que sigue protegiendo contra cuelgues
> reales del pipeline de mensajes.

---

## 8. Seguridad

> Esta sección documentaba la superficie de ataque del despliegue Vultr/Docker
> retirado el 2026-08-08. Se actualiza aquí para reflejar el despliegue local
> real, verificado por auditoría E2E el 2026-09-02 — sin inventar controles no
> verificables desde este documento (p. ej. FileVault, firewall del SO).

### Superficie de ataque y controles (despliegue local actual)

| Superficie | Control | Estado verificado |
|---|---|---|
| API REST (:8900) | HTTPBearer + RBAC multiusuario (`services/core/auth.py`, `TokenManager`) | ✓ Activo (no depende del host) |
| `/health` (público en el host) | Sin auth — solo lectura de estado | ✓ Intencional |
| Puerto 8900 | bind `0.0.0.0`; alcance real = red local (sin proxy público/TLS en este host) | Ver nota abajo |
| Ingreso Telegram | Long-poll (`USE_WEBHOOK=0`) — sin endpoint de webhook expuesto hoy | ✓ Verificado (gateway.log) |
| Webhook (código presente, inactivo) | `TELEGRAM_WEBHOOK_SECRET` solo se valida si `USE_WEBHOOK=1` | ⚠ No aplica hoy (long-poll) |
| `.env` en disco | `chmod 600`, propietario del usuario local (no root) | ✓ Verificado |
| Vault DBs (`vault/*.db`) | `mode 0644`, propietario del usuario local | ⚠ Ver riesgos |
| eToro ejecución | Requiere creator auth explícita | ✓ Activo |
| Sovereignty engine | Inhibe emisiones sin consent | ✓ Activo |
| Circuit breakers | ExternalCallGuard por servicio | ✓ Activo |
| Audit log | `sovereignty.jsonl` (toda acción) | ✓ Activo |

**Nota — puerto 8900 sin TLS público:** a diferencia del despliegue Vultr
retirado (Caddy + Let's Encrypt + UFW), este host no tiene un proxy público
delante de la API. Si se necesita acceso remoto, debe añadirse un control
equivalente (proxy autenticado o túnel) antes de exponer el puerto fuera de
la red local.

### Riesgos residuales

**BAJO-MEDIO — Vault DBs en `0644`**
- Evidencia verificada: `ls -la vault/*.db` → `-rw-r--r--`, propietario del
  usuario local (no root, no multi-tenant — a diferencia del riesgo Vultr
  original, donde cualquier proceso del container podía leerlas).
- Mitigación recomendada: `chmod 600 vault/*.db ~/.vectrax/*.db` si esta Mac
  tiene más de una cuenta de usuario local.

**INFORMATIVO — `.env` en el filesystem**
- Las credenciales están en disco (`chmod 600`) y en `os.environ` del
  proceso. Mitigación futura: gestor de secretos si el despliegue vuelve a
  ser remoto.

---

## 9. Backup y Recuperación

Backup y restauración corren vía `launchd` (no hay Docker ni volúmenes
remotos). Runbook completo, con flujo de restauración paso a paso y
verificación de integridad: **`docs/OPERATIONS.md`**.

```bash
# Estado de los agentes de backup
launchctl list | grep -E "vectrax.backup-db|vectrax.rotate-logs"

# Backup manual de BD (además del cron diario 03:30)
bash scripts/backup_db.sh

# Restaurar un backup a un directorio de trabajo (no destructivo: verifica
# sha256 + integrity_check antes de tocar cualquier dato vivo)
bash scripts/restore_db.sh latest /tmp/vx_restore
```

Destinos: `~/vectrax_backups/` (local) + iCloud, por decisión explícita — sin
disco externo ni Backblaze B2.

---

## 10. Guía de Operación

### Añadir un nuevo proveedor de freight

1. Crear clase que extiende `FreightFeedProvider` en `connectors/freight/`
2. Implementar `stream_events(n)`, `health_check()`, `provider_name`
3. Registrar en `connectors/freight/__init__._REGISTRY`
4. Setear `FREIGHT_FEED_PROVIDER=<nombre>` en `.env`
5. No tocar `FreightLearningCycle`

### Activar trading en PAPER

```
/vx market execution paper     # Activa modo PAPER
                                # Vectrax empezará a proponer trades
                                # cuando WR≥60% y N≥30 (hoy: 0/0)
```

### Revisar propuestas del convergence learner

```python
from connectors.etoro.convergence_learner import get_pending_proposals
proposals = get_pending_proposals()
# Cada propuesta incluye: drift_kind, evidence, suggested_review, confidence
# Aprobar (solo marca; no aplica cambios):
from connectors.etoro.convergence_learner import approve_proposal
approve_proposal("LRN-XXXXXXXX", approved_by="mario")
```

### Monitorear nudges en producción

```bash
bash scripts/check_reentry.sh
# Muestra: nudge #1/2/3 enviados, usuarios DORMANT, últimos 5 nudges
```

---

## 11. Flujo de Datos (End-to-End)

### Mensaje de usuario → respuesta

```
Usuario (Telegram)
    │
    ▼
telegram_gateway.py
    │  record_activity() — resetea nudge timer
    │  tier_check()       — verifica límite diario
    │  enqueue(message)
    ▼
message_queue.db (SQLite WAL)
    │
    ▼
pipeline_worker.py (ThreadPoolExecutor, CONCURRENT threads)
    │
    ├─ _process_one(msg)
    │     ├─ total_convergence_cycle()—fases 1-7
    │     ├─ PresenciaPura.observe()   — capa inhibitoria
    │     ├─ smart_router()            — intención + ruta
    │     ├─ memory: resolve_with_memory() primero
    │     ├─ gravity: deep_memory si hay señales
    │     ├─ LLM: OpenAI / Gemini / local
    │     ├─ enforce_final_answer()    — filtro de calidad
    │     └─ _tg_send()               — sovereignty check + envío
    │
    └─ Ciclos background (main loop, separados del pool):
         ├─ Market learning     cada 30min (ThreadPool, 60s timeout)
         ├─ Freight learning     cada 6h   (ThreadPool, 120s timeout)
         ├─ Convergence learner  cada 24h  (inline, no bloqueo)
         ├─ Presence nudges      cada 10min
         ├─ Scheduler            cada 60s
         └─ Memory watchdog      cada 30s  (auto-restart si RAM > 1.2GB)
```

### Persistencia por capa

```
Capture de usuario    →  user_memory.db     (profiles, interactions, user_facts)
Memoria gravitacional →  gravity.db         (deep_memory, context_identities)
Aprendizaje eToro     →  etoro_signals.jsonl + etoro_patterns.json
Aprendizaje Freight   →  gravity_index.json + domain_library/freight_logistics.json
Nudges               →  continuity_reentry.db
Sobranía             →  sovereignty.db + sovereignty.jsonl
Ledger               →  ledger.db
Queue                 →  message_queue.db
Hearbeat             →  worker_heartbeat, gateway_heartbeat (archivos)
```

---

## 12. CI/CD

### GitHub Actions (`.github/workflows/ci.yml`)

Quality gate automático en cada push a `main` y en cada PR:

```yaml
trigger: push(main) | pull_request
jobs:
  quality-gate (ubuntu-latest, Python 3.9, timeout 30min)
    1. Orchestration tests  — fallos de activación rápidos primero
    2. Full hermetic suite  — pytest tests/ -m "not live" --tb=short
       Tests marcados 'live' se excluyen (requieren credenciales reales)
```

El suite es hermético: `tests/conftest.py` neutraliza credenciales externas
e aúsla todos los stores persistentes. No toca datos reales ni requiere secrets.

### Deploy a producción

`deploy_vultr.sh` fue eliminado (PR #88, 2026-08-08) — no hay deploy remoto.
El código en `main` local ES la producción:

```bash
git pull origin main                     # main local YA es producción
launchctl kickstart -k gui/$(id -u)/com.vectrax.supervisor
# Verificación:
launchctl list | grep vectrax
curl -s http://127.0.0.1:8900/health
```

---

## 13. Tests

```bash
# Suite completa relevante
python -m pytest tests/test_freight_pipeline.py         # 29 tests (freight)
python -m pytest tests/test_broker_routing.py           # 10 tests (broker)
python -m pytest tests/test_advanced_architecture.py    # 22 tests (interface + learner)
python -m pytest tests/test_continuity_reentry.py       # 21 tests (nudges)
python -m pytest tests/test_gravity_engine.py           # gravity engine

# Total: ~82 passing localmente
# CI: pytest tests/ -m "not live" (GitHub Actions, cada push a main)
```

---

## 14. Evidencia de Producción — Jun 23, 2026 (HISTÓRICO — despliegue Vultr retirado)

> ⚠️ Esta sección corresponde al despliegue en Vultr/Docker, retirado el
> 2026-08-08 (PR #88). Se conserva como registro histórico. Para evidencia del
> despliegue local actual, ver la Sección 15.

Primer ciclo completo de todos los motores tras el deploy de la arquitectura avanzada.
Registrado en `/root/.vectrax/worker.log` (ruta dentro del container Vultr, ya no aplica).

### Freight Learning Cycle — Primer ciclo (23:36 server time)

```
vectrax.freight.learning_cycle:
  provider=simulator | ingested=200/200 | errors=0
  stars=758→763 | mature=3 | elevated=3 | elapsed=37.8s

vectrax.domain_knowledge:
  [DOMAIN] Elevated NEW freight_logistics
  delivery_complete:freight_logistics:delivery_complete:region=Mountain|on_time=True
  WR=90% E=+1350.000% | N=15

Domain library:  /root/.vectrax/domain_library/freight_logistics.json  ✅ CREADO
```

### Trading Convergence Learner — Primera ejecución (23:36)

```
vectrax.etoro.convergence_learner:
  proposals=1 | kinds=[DORMANCY] | wr=42.4% | best_n=11

Proposal LRN-F18CC7FA:
  drift_kind: dormancy
  suggested_review: "Verify eToro API connectivity..."
  applied: false

/root/.vectrax/etoro_learner_proposals.jsonl  ✅ CREADO
```

**Nota:** DORMANCY se generó porque el contenedor acababa de reiniciarse y los signals
aún no habían acumulado nuevas resoluciones en el ciclo corriente. Correcto.

### API y sistema

```json
GET /health → 200 OK
{
  "status": "ok",
  "env": "production",
  "uptime_seconds": 2007,
  "components": {"api": "ok", "database": "ok", "governor": "act"},
  "governor_mode": "act",
  "governor_reason": "Nominal — all systems healthy"
}
```

### Schema real de señales (etoro_signals.jsonl)

```json
{
  "signal_id": "...",
  "status": "win" | "loss" | "neutral" | "expired",
  "outcome_timestamp": 1781016807.26,
  "return_pct": 0.35,
  "sl_touched": false,
  "symbol": "BTC",
  "direction": "buy" | "sell",
  "entry_price": 58000.0
}
```

Estados al cierre del día:
- `win`: 55 | `loss`: 74 | `neutral`: 142 | `expired`: 4 | Total: 275
- WR observado (win/loss): **42.6%** | Threshold: 60% | Gap: 17.4pp → WR_GAP proposal
- Best pattern N=11 | Threshold N=30 → SAMPLE_GAP proposal

### Resumen de motores activos

| Motor | Estado | Próxima ejecución |
|---|---|---|
| Market learning (eToro) | ✅ Activo cada 30min | ~30min |
| Freight learning | ✅ Primer ciclo completado (3 elevados) | +6h |
| Trading convergence learner | ✅ Primera propuesta generada | +24h |
| Presence nudges (3-nudge) | ✅ 10 nudges enviados hoy | continuo |
| API :8900 | ✅ Healthy, TLS via Caddy | — |
| UFW :8900 | ✅ Restringido a localhost | — |
| .env | ✅ chmod 600 | — |

---

*Documentado: Junio 23, 2026 — Post auditoría E2E de dominios Trading y Freight*  
*Revisión de seguridad: Junio 23, 2026 — Puerto 8900 restringido + .env 0600*

---

## 15. Evidencia de Producción — Sep 2, 2026 (post auditoría E2E, despliegue local)

Verificación tras la auditoría E2E de solo lectura y la corrección de sus 3
hallazgos (pipeline_worker watchdog, job worker-monitor huérfano, esta
documentación).

### Identidad de despliegue
```
HEAD local = origin/main = 360c372 (working tree clean)
Host: Mac local, launchd com.vectrax.supervisor (KeepAlive, RunAtLoad)
GET /health → 200 {"status":"ok","env":"dev","components":{"api":"ok","database":"ok","governor":"act"}}
```
Nota: `env` reporta `"dev"` (default de `VX_ENV`, no seteada en `.env`)
aunque este proceso ES la producción real — cosmético, `is_production` solo
afecta el `reload` de uvicorn, no la autenticación ni el comportamiento.

### Pipeline conversacional (verificado con tráfico real, 1-2 sep)
```
CONVERGENCE_CYCLE | phase[3]_memory=✓ phase[6]_gravitation=✓ action=proceed
PROC_OUT source=criterion:llm_rendered  ← Motor de Criterio respondiendo con
  evidencia real (expectancy/WR de freight_logistics y market)
```

### Ciclos de dominio freight/real_estate/cyber — antes vs. después del fix
```
Antes:   MAIN_LOOP_WATCHDOG | main loop stuck for 60-90s > 60s — force exit
         14 reinicios del worker en 72h, 0 líneas "Freight/Real estate/
         Cyber learn:" en los logs — los tres ciclos nunca completaban.
Después: los tres ciclos corren en _domain_learning_thread (hilo daemon
         dedicado, core/transport/pipeline_worker.py), totalmente
         desacoplado del main loop — su duración ya no dispara
         MAIN_LOOP_WATCHDOG.
```

### Jobs launchd
```
com.vectrax.supervisor      PID activo, KeepAlive              ✓
com.vectrax.backup-db       agente activo                      ✓
com.vectrax.rotate-logs     agente activo                      ✓
com.vectrax.worker-monitor  ELIMINADO (script inexistente,
                            status 127 en cada corrida) — cerrado ✓
```
