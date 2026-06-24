# Vectrax — Arquitectura del Sistema

**Versión:** Junio 2026  
**Creador:** Mario Bravo Castro  
**Entorno de producción:** Vultr VPS · 140.82.28.181 · Docker

---

## 1. Visión General

Vectrax es un organismo digital autónomo con memoria persistente, aprendizaje
continuo y capacidad de acción gobernada. No es un chatbot ni un wrapper de LLM:
es un sistema que percibe, aprende, propone y —con autorización explícita— actúa.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         VECTRAX CORE                                │
│                                                                     │
│  Telegram ──► Gateway ──► Queue ──► Worker Pool ──► LLM / Memory   │
│                                          │                          │
│                              ┌───────────┴───────────┐             │
│                              │    Background Cycles  │             │
│                              │  Market Learn (30min) │             │
│                              │  Freight Learn (6h)   │             │
│                              │  Conv. Learner (24h)  │             │
│                              │  Proactive Nudges(10m)│             │
│                              └───────────────────────┘             │
│                                                                     │
│  REST API (:8900) ──► Caddy TLS ──► api.vectrax.app                │
└─────────────────────────────────────────────────────────────────────┘
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
│   │   └── gravity_engine.py      GravityIndex (stars, convergencias)
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
VPS: Vultr  │  140.82.28.181  │  OS: Ubuntu  │  RAM: 8GB limit  │  CPU: 4 cores

Servicios activos:
  vectrax-core  (python:3.11-slim, restart: unless-stopped, healthy)
  caddy         (caddy:2-alpine, perfil TLS, api.vectrax.app → vectrax:8900)

Red:
  vectrax-net (bridge) — comunicación interna Caddy↔Vectrax
  Puerto 8900: bind 0.0.0.0 (host) — solo accesible desde localhost (UFW)
  Puerto 443:  público — HTTPS con TLS automático (Let's Encrypt)
  Puerto 80:   público — redirect permanente a 443

Volumen persistente:
  vectrax-runtime → /root/.vectrax/ (sobrevive docker compose down/up)
  CRÍTICO: down -v destruye el volumen. Backup: scripts/backup_runner.sh

Deploy:
  bash deploy_vultr.sh
  (rsync código → docker compose build → docker compose up -d)
  .env y vault/ nunca sobrescritos por rsync
```

### Variables de entorno clave

```
# Obligatorias
TELEGRAM_BOT_TOKEN          Bot de Telegram
OPENAI_API_KEY              LLM principal
ETORO_API_KEY               eToro REST API
ETORO_USER_KEY              eToro JWT de usuario
ETORO_ENVIRONMENT           "real" | "demo"

# Opcionales / Feature flags
BROKER_PROVIDER             etoro (default) | alpaca
FREIGHT_FEED_PROVIDER       simulator (default) | real
FREIGHT_EVENTS_PER_CYCLE    200 (default)
FREIGHT_LEARN_ENABLED       1 (default)
PROACTIVE_ENGINE_ENABLED    0 (default — activar con "1")
CONTINUITY_REENTRY_ENABLED  1 (default)
ALPACA_API_KEY              Requiere alpaca-py instalado
ALPACA_SECRET_KEY           —
ALPACA_PAPER                true (default)
```

### Proceso de ciclos en pipeline_worker

| Ciclo | Intervalo | Timeout | Propósito |
|---|---|---|---|
| Market learning (eToro) | 30 min | 60s | Señales → patrones |
| Freight learning | 6h | 120s | Eventos → stars → elevación |
| Trading convergence learner | 24h | — | Detección de deriva de umbrales |
| Presence nudges | 10 min | — | Mensajes proactivos 3-nudge |
| Proactive engine | 10 min | — | Recordatorios (OFF por defecto) |
| Scheduler | 60s | — | Tareas programadas |
| Router digest | configurable | — | Telemetría de routing |

---

## 8. Seguridad

### Superficie de ataque y controles

| Superficie | Control | Estado |
|---|---|---|
| API REST (:8900) | HTTPBearer + RBAC (require_token, require_role) | ✓ Activo |
| `/health` (público) | Sin auth — solo lectura de estado | ✓ Intencional |
| Puerto 8900 externo | UFW: allow only from 127.0.0.1 | ✓ Aplicado Jun-23 |
| Puerto 443 (HTTPS) | Caddy TLS + security headers | ✓ Activo |
| Telegram webhook | TELEGRAM_WEBHOOK_SECRET validado | ✓ Activo |
| .env en disco | chmod 600, propietario root | ✓ Aplicado Jun-23 |
| Vault DBs | mode 0644 dentro del container | ⚠ Ver riesgos |
| Container como root | Sin directiva USER en Dockerfile | ⚠ Ver riesgos |
| eToro ejecución | Requiere creator auth explícita | ✓ Activo |
| Sovereignty engine | Inhibe emisiones sin consent | ✓ Activo |
| Circuit breakers | ExternalCallGuard por servicio | ✓ Activo |
| Audit log | sovereignty.jsonl (207KB, toda acción) | ✓ Activo |
| SSH | Key-based auth únicamente (vectrax_server) | ✓ Activo |

### Riesgos residuales y mitigaciones recomendadas

**ALTO — Container corre como root**
- Evidencia: Dockerfile sin directiva `USER`; todos los procesos PID como root
- Riesgo: si hay RCE en el LLM o la API, el attacker tiene root en el container
- Mitigación recomendada:
  ```dockerfile
  RUN groupadd -r vectrax && useradd -r -g vectrax -d /home/vectrax vectrax
  RUN chown -R vectrax:vectrax /root/.vectrax /app/vault
  USER vectrax
  ```
  Requiere migrar el volumen de `/root/.vectrax` a `/home/vectrax/.vectrax`

**MEDIO — Vault files mode 0644**
- Evidencia: ls -la /root/.vectrax/*.db → -rw-r--r--
- Riesgo: cualquier proceso en el container (o futuro usuario no-root) puede leer DBs
- Mitigación: `chmod 600 /root/.vectrax/*.db /root/.vectrax/*.jsonl` en startup

**BAJO — SSH root login habilitado**
- Evidencia: sshd en puerto 22, usuario root, key-based auth
- Riesgo: si la clave privada se expone, acceso root directo al servidor
- Mitigación: crear usuario sin privilegios para deploy, deshabilitar root SSH

**INFORMATIVO — .env montado en /app**
- Evidencia: `./:/app` bind mount + `env_file: .env`
- Las credenciales están en el filesystem del container y en `os.environ`
- Mitigación futura: usar Docker secrets o Vault para inyección en runtime

---

## 9. Backup y Recuperación

```bash
# Backup manual (local + iCloud)
bash scripts/backup_runner.sh

# Proteger volumen antes de operaciones destructivas
bash scripts/protect_volume.sh

# NUNCA ejecutar sin backup previo:
docker compose down -v   # DESTRUYE vectrax-runtime

# Restaurar desde backup
docker run --rm -v vectrax-runtime:/root/.vectrax \
  -v /ruta/backup:/backup alpine \
  tar xzf /backup/vectrax_runtime_YYYYMMDD.tar.gz -C /
```

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

```bash
bash deploy_vultr.sh
# rsync código (excluye .env, vault/, data/, logs/)
# docker compose build  (usa cache — solo reconstruye capas modificadas)
# docker compose up -d  (restart unless-stopped)
# Verificación: docker compose ps + docker compose logs --tail=15
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

## 14. Evidencia de Producción — Jun 23, 2026

Primer ciclo completo de todos los motores tras el deploy de la arquitectura avanzada.
Registrado en `/root/.vectrax/worker.log`.

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
