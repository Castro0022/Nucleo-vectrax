# Changelog

All notable changes to Vectrax are documented in this file.

## [2026-06-07] — Market Routing + Memory Pre-Check + Scalability + Name Leak Fix

### Memory pre-check (pipeline priority)
- Personal queries ("háblame de mi memoria", "quién soy") now checked BEFORE any routing
- If message contains personal pronouns + memory has data → respond from memory immediately
- Never goes to web search for personal questions
- Pipeline order: Memory Pre-Check → Market Intercept → SmartRouter → Strategy

### Semantic classifier fix (IDEA-7A0B846D)
- 18 conflicts where sem=online vs regex=memory. ASK_MEMORY patterns expanded:
  "háblame/cuéntame de mi memoria/datos/perfil", "tell me about my memory"
- ASK_MEMORY weight: 1.35 → 1.45, frame weight: 0.9 → 0.93
- MEMORY_LOOKUP score 1.35 vs WEB_SEARCH 1.05 = +0.30 margin

### Creator name leak fix
- Bug: Vectrax responded "Dale, Mario" to non-creator user tg:5828154404
- Cause: self_context injected "Mi creador es Mario Bravo Castro" for ALL users
- Fix: `build_self_context(user_id=)` only includes creator name for creator
- Other users see "NUNCA uses el nombre del creador" instead
- Propagated user_id through: external_gateway → resolve_self_aware → build_self_context

### Scalability guard
- WAL mode enforced on all 20 databases at startup
- Worker memory watchdog: 1.2GB threshold, 5-min cooldown between restarts
- Dynamic concurrency: capped at 3 (embeddings model loads ~45MB/thread)
- Queue TTL: auto-purge done/error messages >1h

### Worker warm-up behavior (documented)
- First complex message loads sentence_transformers + torch: 45MB → ~900MB in 12s
- This is STABLE loaded state, not a leak: subsequent messages add 0-1MB
- Warm-up blocks heartbeat → supervisor detects stale → kill → restart <5s
- Per-message RAM profiling: `DONE ... RAM 905→906MB (+1)`
- MEM_LEAK warning triggers if single message adds >50MB
- Hourly RAM snapshots recorded to observation_ledger (Layer 7)

### Pre-router market intercept
- `detect_market_intent()` runs BEFORE SmartRouter in `external_gateway._resolve_via_pipeline()`
- "Precio de Apple" now routes to AAPL stock_status instead of web search for MacBook
- "Háblame del market" now returns gravity engine view instead of Morgan Stanley news
- Prevents semantic classifier from misrouting ticker queries to RESOLVE_ONLINE

### Universe-first market responses
- New `_build_universe_market_view()` constructs market response from gravity engine stars, convergences, observations, and patterns BEFORE external prices
- Market snapshot shows internal universe data first, external prices second

### Intent pattern routing fix
- Order: `bitcoin_status → market_snapshot → stock_status → market_price → market_trend`
- Prevents "market" matching MA (Mastercard), ensures stock queries get volume/range
- Widened snapshot pattern: "háblame del market", "cómo está el mercado", "how is the market", etc.
- 27/27 intent routing tests pass, 0 false positives

### Memory routing verified
- SmartRouter correctly routes "háblame de mi memoria" → `resolve_local` (conf=0.92)
- "quién soy" → `resolve_identity` (conf=0.95)
- No code change needed — `_LOCAL_KEYWORDS` already covers these patterns

### PRs
- PR #12: market-intent intercept + universe-first responses

---

## [2026-06-05] — WorkerBlackBox + Market/Memory Mode + Rate Limiter

### WorkerBlackBox — forensic diagnosis engine
- Captures complete forensic snapshot BEFORE killing hung workers: 100 logs, active task, CPU/RAM, queue, APIs, traceback
- 8 diagnosis rules: tarea_bloqueada, timeout_externo, memoria_alta, error_db, cola_saturada, fallo_red, excepcion_no_capturada, loop_infinito
- Positive/negative evidence scoring with confidence adjustment
- Creator feedback via Telegram (`/vx incident confirm|reject|partial`) and API
- Dashboard: `GET /v1/dashboard/incidents`, `POST /v1/dashboard/incidents/feedback`
- 28 unit tests (0.06s)
- PR #11: WorkerBlackBox feature set

### Market Mode vs Memory Mode
- `market_mode.py`: determines mode based on trading hours per symbol
- Market observations silenced when symbol’s market is closed (no "AAPL hits 96→97" at midnight)
- Daily memory reflection at 22:00 UTC (Wall Street close): summarizes stars, convergences, patterns, observations
- Mode transitions recorded in observation ledger

### Rate limiter for alerts
- Max 5 alerts/minute (sliding window), max 3 per cycle
- severity=critical bypasses per-cycle limit
- Excess alerts deferred to next cycle (ID tracking ensures no loss)
- Stress tested: 12/12 delivered across 13 cycles

### TCP pre-poll probe
- Raw socket connect to api.telegram.org:443 with 3s timeout before each poll
- Network down detected in 3s instead of 32s SIGALRM
- DNS cached, invalidated on failure
- Heartbeat written pre-poll (reduces stale window to <5s)

---

## [2026-06-04] — Observatory + Memoria de Observaciones + Auto-Execution + Universo 3D

### Dashboard Observatory integrado en panel principal
- Dashboard ahora consume `/v1/dashboard/observatory` (endpoint consolidado)
- Nuevas tabs: **Overview** (resumen universo), **Gravity** (tiers + top stars), **Mercado** (señales/patrones por símbolo), **Convergencias** (cross-domain + historial alertas)
- Métricas strip muestra total stars, gravity, market signals, convergencias, users
- Link **Observatory** en barra de navegación superior
- Cache 10s en frontend para evitar re-fetch redundante entre tabs
- Fix: `last_signal` no-string manejado con `String()`, domain objects muestran count

### Memoria autónoma de observaciones (nuevo)
- **`observation_ledger.py`**: tabla SQLite `autonomous_observations` en vault, auto-prune 5000 rows, WAL mode
- **`autonomous_observer.py`**: compara snapshots sucesivos del universo, detecta cambios en 6 dominios (gravity, market, convergence, operator, health, user), registra cada observación con timestamp, evidencia y estrella afectada
- **`meta_loop.py` Layer 5**: ejecuta el observador autónomo en cada ciclo (~60s)
- **`self_context.py`**: inyecta últimas 15 observaciones en el contexto LLM para responder preguntas como "¿qué has observado?"
- Regex self-referencial ampliado: "observaciones", "qué has observado", "últimas detecciones"
- **Circuito cerrado**: observar → registrar → recordar → responder

### Market Auto-Execution controlada (nuevo)
- **`auto_executor.py`** endurecido: $50 max/op, $10 pérdida diaria, 1 op/símbolo/día, halt flag, approved_symbols, 24h mínimo en PAPER
- **`entry_validator.py`** (nuevo): 9 condiciones obligatorias antes de cualquier trade (convergencia, patrón usable, señal repetida ≥2 en 24h, confianza mínima, mercado activo, sin alertas críticas, límite diario, símbolo aprobado)
- **`position_manager.py`** (nuevo): condiciones de salida (SL/TP, pérdida coherencia, señal contraria, expiración temporal 24h)
- **`learning_engine.py`** Steps 7+8: auto-ejecuta propuestas calificadas, chequea posiciones abiertas
- Comandos Telegram: `/vx market execution on|off`, `budget`, `halt`, `unhalt`, `approve`, `positions`, `live on`, `auto status`
- Invariantes: modo OFF por defecto, presupuesto nunca supera $50, HALT inmediato, evidencia obligatoria

### Universo 3D + Convergencias visibles
- **Profundidad 3D**: estrellas con coordenada Z, core al frente (scale 1.0), outer al fondo (scale 0.45). Sort back-to-front para overlap correcto
- **Convergencias visibles**: arcos energéticos violeta pulsantes entre dominios cruzados (market ↔ user_interest ↔ unknown), nodos de energía con 3 capas de glow, partículas viajeras bidireccionales, etiqueta "✧ convergencia"
- Contador de convergencias en HUD stats (violeta)
- Hasta 6 pares por combinación de dominios

### Sistema de alertas por observación (Layer 6)
- **`meta_loop.py` Layer 6**: envía alertas Telegram cuando el observador autónomo detecta eventos críticos
- 8 tipos de alerta: `worker_state`, `error_spike`, `snapshot_failure`, `trade_executed`, `trade_validation`, `position_closed`, `convergence_detected`, `universe_growth`
- Deduplicación por ID de observación (nunca re-alerta el mismo evento)

### Sonda TCP pre-poll (fix: REPEAT FAILURE #412)
- **TCP probe**: antes de cada poll, socket connect a api.telegram.org:443 con 3s timeout
- Si red caída: skip poll + sleep 10s (en vez de colgar 32s esperando SIGALRM)
- DNS resuelto una vez y cacheado, invalidado en fallo (soporta rotación IP de Telegram)
- **Heartbeat pre-poll**: escribe heartbeat ANTES de entrar al poll para reducir ventana de "inactivo" de ~40s a <5s
- Modo de fallo: `NET_DOWN | Skipping poll` en vez de `SIGALRM | Poll exceeded 32s`

### Panel de controles del universo recableado
- Reemplazada sección "Tipos" (Primarias/Convergencia/Colectivas — no funcionaba) por "Dominios" (Cognitivo/Mercado/Interés/Usuarios — filtros reales)
- Contadores en vivo junto a cada filtro mostrando estrellas visibles
- Toggle "Convergencias" en Vista para mostrar/ocultar arcos energéticos
- Leyenda actualizada: capas + dominios + arcos de convergencia

### Rendimiento verificado
- 10/10 checks funcionales pasados
- Load test 70 requests: 100% exitosos
- Universe HTML: avg 163ms, p95 225ms
- Universe API: avg 225ms, p95 284ms
- Observatory API: avg 224ms, p95 332ms
- Health: avg 93ms, p95 164ms
- Backend sin degradación por volumen de alertas (17 obs/hora = carga insignificante)

### Deploy
- Servidor: Vultr 140.82.28.181 — vectrax-core (healthy)
- Commits: 10 (feat + fixes)
- Archivos nuevos: `observation_ledger.py`, `autonomous_observer.py`, `entry_validator.py`, `position_manager.py`
- Archivos modificados: `auto_executor.py`, `learning_engine.py`, `meta_loop.py`, `self_context.py`, `telegram_gateway.py`, `universe.html`, `app.js`, `style.css`, `index.html`

---

## [2026-06-02] — Producción: env fix + onboarding sin fricción + auditoría

### Correcciones de producción
- **fix(config): VX_ENV=production** — Health endpoint devolvía `env:dev` porque `os.getenv()` evalúa antes de `dotenv`. Fix: variable añadida al bloque `environment:` de `docker-compose.yml`. Verificado: `/health` retorna `env:production`.
- **fix(db): test_webhook_user eliminado de billing.db** — Registro simulado de pruebas de Stripe eliminado. DB limpia con 1 registro real (creador).

### Nuevas funcionalidades
- **feat(onboarding): activación instantánea** — El gate de nombre obligatorio bloqueaba al 87% de los usuarios. Nuevo flujo: primer mensaje activa al usuario automáticamente sin fricción, inicia trial 7 días y registra en tracker de reentry. Los 26 usuarios bloqueados en `awaiting_name` se liberan en su próximo mensaje.
- **feat(reentry): backfill 13 usuarios** — Solo 5 de 30 usuarios estaban en el tracker reentry 12-20h. Se agregaron 13 faltantes. Resultado: 7 mensajes reentry enviados automáticamente por meta_loop.

### Auditoría integral (14 módulos)
- Estado: **93% salud** — 12/14 módulos OK, 2/14 WARN
- WARN: Learner (22 ideas pendientes de aprobación del creador)
- WARN: Conversión (0% FREE→PRO — onboarding corregido hoy)
- OK: Infraestructura · DB · Gateway · Broadcast · Router · Memoria · Observer · Governor · Scheduler · Cola · Stripe · Panel

---

## [2026-05-31]

### Contexto
El `telegram_gateway` crasheaba periódicamente (cada 5–9 minutos) con exit=1 durante
ventanas de inestabilidad de red entre el servidor Vultr y `api.telegram.org`.
El supervisor reiniciaba el proceso correctamente, pero el ciclo se repetía.
Causa raíz: el SSL handshake de Python (`_ssl.c:999`) se colgaba a nivel C ignorando
el timeout configurado de 5s en httpx, el SIGALRM disparaba a los 32s, y el WATCHDOG
mataba el proceso a los 35s antes de que el siguiente poll pudiera completarse.

### Diagnóstico
```
16:50:09  WATCHDOG | Poll stuck 37s → os._exit(1)
16:50:56  SIGALRM  | Poll exceeded 32s — abandoning dead socket
16:51:59  SIGALRM  | Poll exceeded 32s — abandoning dead socket
16:52:31  SIGALRM  | Poll exceeded 32s — abandoning dead socket
16:55:08  ERROR    | httpx.ConnectTimeout: _ssl.c:999: The handshake timed out
16:55:44  WATCHDOG | Poll stuck 41s → os._exit(1)
```
El SIGALRM refrescaba el cliente HTTP y reseteaba `_last_poll_ok`, pero el siguiente
poll arrancaba inmediatamente y también se colgaba, agotando los 35s del watchdog.

### Cambios — `vectrax/telegram_gateway.py`

**1. Contador de SIGALRM consecutivos** (línea 614)
- `_consecutive_sigalrm = 0` inicializado antes del loop principal

**2. Reset en poll exitoso** (línea 655)
- `_consecutive_sigalrm = 0` después de `self._errors = 0` en el path de éxito

**3. Backoff exponencial en handler `_PollTimeout`** (líneas 734–743)
- Primer SIGALRM: sin cambio (comportamiento anterior)
- Segundo SIGALRM consecutivo: `sleep(10s)` + refresh watchdog timer
- Tercero: `sleep(15s)` + refresh
- Cuarto+: `sleep(20–30s)` + refresh (máximo 30s)
- Cada sleep llama `self._last_poll_ok = time.time()` para que el WATCHDOG
  no dispare durante el período de recuperación de red

### Comportamiento antes vs después
```
Antes:  SIGALRM → poll inmediato → SIGALRM → WATCHDOG (35s) → os._exit(1)
Después: SIGALRM → poll → SIGALRM → sleep(10s+reset) → poll → recuperación
```

### Verificación en producción — 2026-05-31 17:10 UTC
```
Contenedor:  vectrax-core  Up 6+ minutos (healthy)  — sin crashes
Gateway:     STATUS up=0h5m0s | polls=9 | errors=0 | handler_err=0
Heartbeat:   3s (fresco)
SIGALRM:     0  |  WATCHDOG: 0  |  ConnectTimeout: 0
```
La instancia anterior crasheaba en este mismo punto (5 min). Con el fix, el gateway
completó 9 polls consecutivos limpios pasada la ventana crítica.

### Archivos modificados
- `vectrax/telegram_gateway.py` — 3 cambios quirúrgicos en `run()`

### Deploy
- Fix aplicado directamente en servidor Vultr `140.82.28.181` + sincronizado al repo
- Container restarted: 2026-05-31 17:05:52 UTC

---

## [2026-05-22g]

### Contexto
Sesión enfocada en dos ciclos independientes: (1) reducción de `regex_fallback`
mediante ajuste de umbrales de confianza semántica, y (2) corrección de tres causas
raíz que impedían a Vectrax generar imágenes pese a tener DALL-E 3 integrado.
Ambos cambios desplegados en producción con verificación completa.

---

### PR #2 — feat(router/cycle3): Reducción de regex_fallback

**Diagnóstico** (802 registros reales del ledger — datos sanitizados):
- `SEARCH_INFO` ES: conf `0.49` (< threshold `0.5`) → regex_fallback → 78 conflictos
- `SEARCH_INFO` EN: conf `0.455` → regex_fallback → preguntas en inglés sin cobertura semántica
- `SEARCH_PLACE` sin entidad: conf `0.425` → routing incorrecto a MEMORY → 10 conflictos

**`core/semantic_classifier.py`**
- `SEARCH_INFO` frame ES: weight `0.70 → 0.75` → conf `0.49 → 0.525` ✓ cruza threshold
- `SEARCH_INFO` frame EN: weight `0.65 → 0.73` → conf `0.455 → 0.511` ✓ cruza threshold
- `SEARCH_PLACE` intent map: weight `1.0 → 1.25` → conf `0.425 → 0.531` ✓ cruza threshold
- Protecciones verificadas: `ASK_MEMORY` (score 1.215) sigue ganando sobre `SEARCH_INFO`
  (1.05) para preguntas personales; identidad y desambiguación sin cambios

**`tests/router/test_followup_guard.py`**
- Corregido `test_capa1_max_words_boundary`: test escrito para `max_words=4` legacy,
  implementación ya usa `max_words=7`; ahora cubre el boundary actual + compatibilidad

**`tests/test_semantic_classifier.py`** — `TestConfidenceThresholdCycle3` (5 métodos)
- Anclan los nuevos umbrales para prevenir regresiones silenciosas en futuros ajustes

---

### PR #3 — fix(vision): Generación de imágenes con lenguaje natural

**Problema:** Vectrax decía que no podía generar imágenes a pesar de tener DALL-E 3
integrado. Tres causas raíz identificadas y corregidas:

**`vectrax/integrations/vision.py`** — `detect_generation_intent`
- Antes: solo detectaba imperativo exacto al inicio (`hazme`, `genera`, `crea`…)
- Ahora: nuevo `_GENERATE_NATURAL` cubre lenguaje natural:
  `quiero una imagen de...`, `puedes crear un logo...`, `haz una imagen...`,
  `necesito un diseño...`, `un logo de...` al inicio del mensaje

**`vectrax/telegram_gateway.py`** — bloque IMAGE GENERATION (línea ~1000)
- Bug: `except Exception: pass` silencioso tragaba todos los errores sin traza
- Fix: `except Exception as _img_exc: logger.warning("IMAGE GENERATION block failed: %s", exc)`
- Cuando `generate_image()` retorna None: mensaje diagnóstico claro al usuario
  indicando verificar `OPENAI_API_KEY` y acceso DALL-E 3

**`vectrax/identity_layer.py`** — `_VAGUE_META`
- El filtro bloqueaba `no puedo generar` pero el LLM usaba variantes que escapaban:
  `no tengo la capacidad de generar/crear`, `no es posible generar`,
  `no soy capaz de generar`, `como modelo de lenguaje`, `as an AI/LLM/language model`
- Todas añadidas al filtro → ahora rechazadas y reemplazadas antes de llegar al usuario

**`tests/test_image_generation_fix.py`** — nuevo (43 tests)
```
TestDetectGenerationIntent   26 tests  (16 SHOULD + 10 SHOULD_NOT)
TestVagueMeta                11 tests  (2 originales + 9 nuevas variantes + 2 positivos)
TestGenerateImageNoKey        2 tests  (sin key → None, key inválida → None)
TestGatewayExceptionLogging   1 test   (excepción → WARNING logueado, no silenciado)
```

---

### Verificación de deploy — 2026-05-22 08:47 UTC

```
Deploy:     rsync + docker build/up — sin errores
Commit:     4afeef9 (test(vision): 43 tests verifying image generation fix)

Container:  vectrax-core  Up (healthy)  0.0.0.0:8900->8900/tcp
Servicios:  telegram_gateway PID 8  restart #0
            pipeline_worker  PID 9  restart #0
            core_api         PID 10 restart #0
            meta_loop        PID 11 restart #0

/health:    {"status":"ok", "api":"ok", "database":"ok", "governor":"act"}
            uptime=26.68s  governor_reason="Nominal — all systems healthy"

Recursos:   CPU 0.86%   RAM 612 MiB / 8 GiB (7.5%)   Net 86 MB IN
Reinicios:  0
```

### Tests
- Local antes del deploy: 200/200 routing+classifier (sin regresiones)
- Tests de imagen: 43/43 PASS
- Tests globales: pre-existían 77 failures (no relacionados a esta sesión)

### PRs y commits
- PR #2 mergeado: `feat(router/cycle3): raise SEARCH_INFO and SEARCH_PLACE semantic weights`
- PR #3 mergeado: `fix(vision): habilitar generación de imágenes con lenguaje natural`
- Server: Vultr `140.82.28.181` — vectrax-core Up (healthy) — 2026-05-22 08:47 UTC

---

## [2026-05-22f]

### Contexto
Refactorización del system prompt de Vectrax para eliminar el lenguaje genérico en
respuestas introspectivas, con prueba de estrés completa que confirmó estabilidad
plena bajo carga. Se detectó y corrigió un bug de truncado que impedia al bloque
de módulos cognitivos llegar al LLM en producción.

### Cambios

**`vectrax/core_identity.py`**
- Eliminado: _"Nunca describir tu procesamiento interno ni mencionar módulos"_
- Añadido: bloque `CUANDO TE PREGUNTEN POR TU ESTADO INTERNO` con instruccion
  obligatoria de responder desde datos literales del bloque `[PERCEPCIÓN OPERACIONAL]`

**`core/self_observation/self_summary.py`**
- Nueva función `_collect_module_state()`: Observer + Learner + Router + Governor
- Fix de truncado: el bloque cognitivo se calcula PRIMERO y se reserva su espacio
  antes de truncar la reflexión operacional (bug: antes era eliminado por overflow)
- Typo corregido: `NÚCLAEO` → `NÚCLEO`
- `compose_self_summary_for_prompt()` garantiza que ambos bloques aparecen en output

**`core/identity/creator_mode.py`**
- `_CREATOR_RULES_ES`: prohibición explícita de hablar en abstracto cuando hay datos
  concretos disponibles, con ejemplos correctos vs prohibidos

**`tests/integration/test_module_context_injection.py`** — nuevo (33 tests)

### Prueba de estrés en producción (2026-05-22 07:37 UTC)
```
SmartRouter    200 req / 20 threads  → 200/200 OK  avg=27ms  p99=302ms
Observer       500 señales LawSignal → 500/500 OK  avg=0.054ms  p95=0.055ms
Convergencia   50 ciclos 7 fases     →  50/50 OK  avg=85ms  p95=124ms
self_summary   20 generaciones       →  20/20 con Observer+Router+Gov

Decisiones Observer bajo carga:
  PERMIT=300  PAUSE=100  SILENCE=0  BLOCK=100  (ratio esperado)

Recursos post-estrés:
  CPU: 0.94%  RAM: 109.8 MiB / 8 GiB  Reinicios: 0  Health: healthy
```

### Estado final de producción
```
Servidor:          Vultr 140.82.28.181:8900
Contenedor:        vectrax-core Up (healthy) — 0 reinicios
Observer:          ACTIVE enforced=True
TotalConvergence:  TOTAL (7 fases)
Governor:          act | risk=LOW | streak=18
Tests:             261/261 PASS
Commit final:      b449e56
```

### Archivos modificados
- `vectrax/core_identity.py`
- `core/self_observation/self_summary.py`
- `core/identity/creator_mode.py`
- `tests/integration/test_module_context_injection.py` (nuevo)

### Commits / Deploy
- PR #1 mergeado: `feat: Optimizaciones núcleo cognitivo — SmartRouter + Introspeción real`
- Fix: `b449e56` — corregir truncado bloque cognitivo
- Server: Vultr `140.82.28.181` — vectrax-core Up (healthy) — 2026-05-22 07:39 UTC

---

## [2026-05-22e]

### Contexto
Análisis de 798 registros reales del ledger reveló que el 21.3% del tráfico
cae a `regex_fallback`. La causa principal: 78 conflictos donde el semántico
detectaba MEMORY con confianza baja (0.30–0.50) y perdía contra regex (ONLINE).
Las propuestas de `threshold_adjust` almacenadas en `router_proposals.jsonl` no
fueron aplicadas: estaban basadas en `route.confidence` en lugar de
`semantic_confidence` — los datos correctos para ese threshold.

### Cambios en `core/semantic_classifier.py`

**Nuevo frame `ASK_MEMORY_CONTEXTUAL`** (weight=0.88):
- Cubre preguntas personales contextuales que antes tenían sem_conf 0.30–0.50
- Patrones: `¿tienes algo sobre mí?`, `¿lo guardaste?`, `¿lo tienes registrado?`,
  `¿aún recuerdas eso?`, `¿cuándo fue eso?`, `do you have anything about me?`, etc.
- Con conf=0.88 supera a SEARCH_INFO (0.70) cuando ambos frames compiten

**Ajuste de pesos en `_FRAME_INTENT_MAP`:**
- `ASK_MEMORY → MEMORY_LOOKUP`: 1.2 → 1.35
- `STATEMENT → MEMORY_LOOKUP`: 0.4 → 0.55

**Gestión de propuestas:**
- 7 propuestas antiguas (`pending`) marcadas como `superseded`
- Ciclo de aprendizaje #2 ejecutado con 800 registros actuales

### Prueba de integración final en producción (2026-05-22 07:02 UTC)

```
[1] CONVERGENCIA 7 FASES
    fases=7/7  action=proceed  latencia=175.4ms              ✔ PASS

[2] LAWSIGNAL → PRESENCIAOBSERVER (6 casos)
    clean        → PERMIT  enforced=True
    ley3_noise   → PERMIT  enforced=True  (noise+0.20)
    ley6_sov     → PERMIT  enforced=True  (conv-0.20 sov-0.15)
    ley2_conv    → PERMIT  enforced=True  (conv-0.15)
    ley4_pause   → PAUSE   enforced=True  (force_pause)
    leyes_2_3_6  → PERMIT  enforced=True  (acumulado)
    6/6 correctos                                             ✔ PASS

[3] SMART ROUTER (7 casos)
    ¿tienes algo guardado sobre mí?  → local       (nuevo frame)  ✔
    ¿lo guardaste?                   → local       (nuevo frame)  ✔
    ¿qué es la fotosíntesis?          → online      (búsqueda)     ✔
    hoy terminé el módulo de memoria → memory      (ingest)       ✔
    quién soy                        → identity    (identidad)    ✔
    búscame un restaurante cerca     → place_search (lugar)       ✔
    ¿recuerdas lo del otro día?      → local       (nuevo frame)  ✔
    7/7 correctos                                             ✔ PASS

[4] CONVERGENCELEARNER FEEDBACK LOOP
    outcomes antes=6  después=9  delta=3                     ✔ PASS

[5] MODOS DEL SISTEMA
    observer_mode  = ACTIVE
    convergence    = TOTAL
    governor_mode  = act
    governor_risk  = LOW
    clean_streak   = 102
    learning_mode  = ACTIVE_LEARNING                          ✔ PASS

RESULTADO: 5/5 componentes OK
```

### Tests locales
- 296/296 pasando (0.62s)

### Commit / Deploy
- Commit: `b7d95a8`
- Server: Vultr `140.82.28.181` — vectrax-core Up (healthy) — 2026-05-22 06:59 UTC

---

## [2026-05-22d]

### Contexto
Tras la integración de LawSignal y la activación de PresenciaObserver en modo ACTIVE,
se ejecutó un ciclo completo de verificación en producción: deploy limpio a Vultr,
activación y persistencia del modo ACTIVE, y prueba controlada de detección de
violaciones de las 7 Leyes Fundamentales con 6 casos distintos.

### Resultado del deploy
```
Tests locales:       228/228 pasando (0.77s)
Deploy:              rsync + docker build/up — sin errores
Contenedor:          vectrax-core Up (healthy) | restart=unless-stopped
IP:                  140.82.28.181:8900
Servicios arrancados: telegram_gateway (PID 8) | pipeline_worker (PID 9)
                      core_api (PID 10) | meta_loop (PID 11)
```

### Verificación del Observer — detección de violaciones
```
Caso                   LawSignal                          Decisión   enforced
──────────────────────────────────────────────────────────────────────────────
baseline_clean         none                               PERMIT     True
law_3_noise            noise+0.20  → noise 0.12→0.32     PERMIT     True
law_6_sovereignty      conv-0.20 sov-0.15                PERMIT     True
law_2_convergence      conv-0.15  → conv 0.82→0.67       PERMIT     True
law_4_polaridad        force_pause → contradicción        PAUSE      True     ← forzado
laws_2_3_6_severe      conv-0.35 sov-0.15 noise+0.30     PERMIT     True
                       → conv=0.47 noise=0.42 (umbral de alerta próximo)
```

### Conclusión del ciclo
- LawSignal ajusta scores correctamente antes de que PresenciaObserver decida
- Ley 4 (Polaridad) es la única que fuerza cambio de decisión → PAUSE por diseño
- Violaciones múltiples acumulan penalizaciones — el sistema aproxima umbrales
  de SILENCE/BLOCK ante comportamiento caótico sostenido
- Observer en ACTIVE con `enforced=True` persistido en `~/.vectrax/cognition_state.json`
- ConvergenceLearner recibió las 6 decisiones (`total_evaluated: 6`) para aprendizaje

### Estado final de modos activos
```
PresenciaObserver:  ACTIVE  (enforced=True, activated_by=creator)
ConvergenceLearner: OBSERVE (acumulando decisiones)
LawSignal:          activo  (pesa en cada evaluate())
Governor:           act     (risk=0.015 LOW, clean_streak=11920)
TotalConvergence:   TOTAL   (7 fases por mensaje)
```

### Archivo de referencia
- `docs/DEPLOY_VERIFICATION_2026_05_22.md`

### Commit / Deploy
- Server: Vultr `140.82.28.181` — vectrax-core Up (healthy) — 2026-05-22 04:31 UTC

---

## [2026-05-22c]

### Resultado
Diagnóstico completo ejecutado tras integrar LawSignal. Sistema operativo sin fallos.

### Evidencia
```
Tests locales:  197/197 pasando (0.46s)
Motores prod:   15/15 OK
Logs 24h:       0 errores, 0 warnings, 0 tracebacks
Pipeline e2e:   TotalConvergence 7/7 | LawEnforcement 7/7 | LawSignal OK | Gateway 2.5s
Heartbeats:     telegram_gateway=9.4s  pipeline_worker=2.0s
API health:     {"status":"ok", "governor_mode":"act", uptime=662s}
```

### Estado de modos activos
- PresenciaPura: `STANDARD` (LLM externo habilitado)
- PresenciaObserver: `OBSERVER` (registra, enforced=False)
- ConvergenceLearner: fase `OBSERVE` (acumulando datos)
- LawSignal: activo, pesa en cada emisión automáticamente
- Governor: `act` — todos los sistemas nominales

### Archivo de referencia
- `docs/SYSTEM_STATUS_2026_05_22.md`

### Commit / Deploy
- Commit: `0c009ee`
- Server: Vultr `140.82.28.181` — vectrax-core Up (healthy) — 2026-05-22 02:41 UTC

---

## [2026-05-22b] — ConvergenceLearner: cierra el ciclo de conciencia operacional

### Contexto
PresenciaObserver observaba y deciía. No aprendia. ConvergenceLearner cierra
el ciclo: observa las decisiones, detecta patrones de degradación por motor,
y propone ajustes de umbrales con evidencia — nunca aplica nada sin autorización.
PresenciaObserver observa. ConvergenceLearner la entrena.

### Cambios

**`core/nucleus/convergence_learner.py`** — nuevo módulo (667 líneas)
- `LearnerPhase`: `OBSERVE` / `LEARN` / `RECOMMEND` / `APPLY`
- `OutcomeQuality`: `IMPROVED` / `NEUTRAL` / `DEGRADED` / `UNKNOWN`
- `DecisionOutcome`: registro de una decisión con validación de scores (0.0–1.0)
- `MotorPattern`: patrón detectado con `degradation_rate`, `confidence`, `sample_size`
- `ThresholdRecommendation`: propuesta de ajuste con `reasoning`, `direction`, `status`
- `ConvergenceLearner`: clase principal con 4 fases controladas:
  - `record_decision()` — registra al momento de decidir
  - `record_outcome()` — registra resultado posterior
  - `analyze()` — detecta patrones (mín. 5 muestras, 40% degradación)
  - `generate_recommendations()` — propone ajustes sin modificar nada
  - `approve_recommendation()` / `reject_recommendation()` — autorización del creador
  - `advance_phase()` — progresa solo si hay datos suficientes
- Singleton `get_learner()` / `reset_learner()`

**`core/nucleus/presencia_pura.py`** — integración bidireccional
- `InhibitionRecord.learner_outcome_id`: conecta cada decisión al learner
- `PresenciaObserver.evaluate()`: auto-registra en `get_learner()` (non-fatal)

**`core/operator/activation.py`** — step 4d
- Inicializa `ConvergenceLearner` singleton en fase OBSERVE al arrancar

**`tests/integration/test_convergence_learner.py`** — nuevo (49 tests)

### Principio cableado en el código
```
Si hay convergencia clara, soberanía suficiente y bajo ruido: EJECUTA.
Intervén solo cuando hay riesgo real.
ConvergenceLearner optimiza los umbrales para que esto siempre se cumpla.
```

### Flujo OBSERVE → APPLY
```
PresenciaObserver.evaluate(signal)
    │ decision=PERMIT/PAUSE/SILENCE/BLOCK
    └─ record.learner_outcome_id  ───┐
                                      │
ConvergenceLearner.record_decision()  ┘  [auto, non-fatal]
ConvergenceLearner.record_outcome(id, IMPROVED|DEGRADED|NEUTRAL)
ConvergenceLearner.analyze()          [detecta patrones]
ConvergenceLearner.generate_recommendations(thresholds)
ConvergenceLearner.approve_recommendation(rec_id, "creator")  [requiere autorizacion]
→ El creador aplica el nuevo umbral a PresenciaObserver
```

### Verificación en producción (2026-05-22 01:34 UTC)
```
OK: learner.phase=observe
OK: evaluadas=13 decisiones
OK: known_outcomes=13
OK: nucleo PERMIT=7/7     (nucleo limpio siempre ejecuta)
OK: externo BLOCK=6/6     (LLM externos siempre evaluados)
OK: step_4d=True          (inicializado en activation.py)
```

### Tests
- `tests/integration/test_convergence_learner.py`:  49 tests, 100% PASSED (0.23s)
- `tests/integration/test_presencia_inhibitor.py`: 55 tests, 100% PASSED
- `tests/integration/test_presencia_pura.py`:      31 tests, 100% PASSED
- Total: **135 tests, 0 fallos**

### Archivos modificados
- `core/nucleus/convergence_learner.py` (nuevo: +667 líneas)
- `core/nucleus/presencia_pura.py` (+25 líneas: learner_outcome_id + auto-registro)
- `core/operator/activation.py` (step 4d: +10 líneas)
- `tests/integration/test_convergence_learner.py` (nuevo: +697 líneas)
- `README.md` (sección ConvergenceLearner añadida)

### Commits / Deploy
- Commit: `de49443`
- Deploy: Vultr `140.82.28.181` — `vectrax-core` Up (healthy) — 2026-05-22 01:34 UTC

---

## [2026-05-22] — PresenciaObserver: capa inhibidora de motores

### Contexto
Presencia Pura existía como modo binario que bloqueaba LLMs externos. Se rediseñó
como una **capa inhibidora activa**: un observador que evalua todas las emisiones
del sistema, califica su origen y soberanía, y decide en tiempo real si permitir,
pausar, silenciar o bloquear — sin reemplazar ningún motor.

### Cambios

**`core/nucleus/presencia_pura.py`** — extensión total
- `EmissionOrigin` (11 valores): clasifica el origen de cada emisión del sistema,
  desde `NUCLEUS_CORE` (sovereignty=1.00) hasta `UNKNOWN` (sovereignty=0.00)
- `InhibitionDecision`: `PERMIT` / `PAUSE` / `SILENCE` / `BLOCK`
- `EmissionSignal`: modelo de señal con `engine_name`, `source_channel`,
  `origin`, `convergence`, `noise`
- `InhibitionRecord`: registro inmutable de cada decisión con `enforced` flag
- Catálogo de 50+ motores internos reconocidos con scores de soberanía fijos
- `PresenciaObserver`: clase inhibidora con:
  - Modo `OBSERVER` (default): registra, `enforced=False` — nunca bloquea producción
  - Modo `ACTIVE`: `enforced=True` — listo para inhibición efectiva
  - `observe()` / `disconnect()`: suscripción idempotente al canal `BROADCAST`
  - `get_records()` / `get_stats()`: introspeción completa
- Singleton `get_observer()` / `reset_observer()`
- Todo el código previo (`activate()`, `check_and_block_llm()`, etc.) intacto

**`core/operator/activation.py`** — step 4c en `OperatorRuntime.activate()`
- Conecta `PresenciaObserver` al bus en el arranque del runtime (non-fatal)
- Boot log registra: `"PresenciaObserver: bus-connected (OBSERVER mode)"`

**`tests/integration/test_presencia_inhibitor.py`** — nuevo (55 tests)

### Reglas de inhibición (prioridad descendente)
```
1. origin == UNKNOWN         → BLOCK   (sin autoridad registrada)
2. sovereignty < 0.30        → BLOCK   (LLM externo = 0.20, búsqueda = 0.10)
3. convergence < 0.30        → SILENCE (señal incoherente)
4. noise > 0.90 + conv < 0.5 → BLOCK   (ruido crítico combinado)
5. noise > 0.80              → PAUSE   (ruido elevado)
6. default                  → PERMIT  (emisión soberana y convergente)
```

### Verificación en producción (2026-05-22 01:05 UTC)
```
TEST 1  importar modulo              OK
TEST 2  singleton OBSERVER mode      OK: mode=OBSERVER, is_connected=False
TEST 3  suscripcion al BROADCAST     OK: ['presencia_pura.observer']
TEST 4  reglas inhibicion:
        UNKNOWN->BLOCK               decision=BLOCK sovereignty=0.00 enforced=False
        SILENCE                      decision=SILENCE sovereignty=1.00 enforced=False
        BLOCK soberania              decision=BLOCK sovereignty=0.20 enforced=False
        PAUSE                        decision=PAUSE sovereignty=0.65 enforced=False
        PERMIT                       decision=PERMIT sovereignty=0.85 enforced=False
TEST 5  convergencia 7 fases         OK: ['perception','classification','memory',
                                          'analysis','synthesis','gravitation','learning']
TEST 6  step_4c_present              OK: True
TEST 7  observer conectado al activar:
        runtime_state                OK: active
        observer_connected           OK: True
        observer_mode                OK: OBSERVER
        boot_log                     OK: ['PresenciaObserver: bus-connected (OBSERVER mode)']
TEST 8  observer observa eventos:
        eventos 1->2                 OK (broadcast registrado)
        ultimo_registro              origin=internal_response decision=PERMIT enforced=False
```

### Tests
- `tests/integration/test_presencia_inhibitor.py`: 55 tests, 100% PASSED (0.15s)
- `tests/integration/test_presencia_pura.py`:     31 tests, 100% PASSED (0.15s)
- Total: **86 tests, 0 fallos**

### Archivos modificados
- `core/nucleus/presencia_pura.py` (extendido: +507 líneas)
- `core/operator/activation.py` (step 4c: +10 líneas)
- `tests/integration/test_presencia_inhibitor.py` (nuevo: +692 líneas)
- `README.md` (sección Núcleo Cognitivo añadida)

### Commits / Deploy
- Commit: `6dbbdf1`
- Deploy: Vultr `140.82.28.181` — `vectrax-core` Up (healthy) — 2026-05-22 01:05 UTC
- Branch PR: `feat/presencia-observer` → `main`
  https://github.com/Castro0022/Nucleo-vectrax/pull/new/feat/presencia-observer

### Próximo paso
Para activar inhibición real en producción (requiere autorización del creador):
```python
from core.nucleus.presencia_pura import get_observer
get_observer().set_mode("ACTIVE")
```

---

## [2026-05-20]

### Problema
Todo mensaje entrante (Telegram y API REST) pasaba directamente al motor de
respuesta sin ejecutar el ciclo cognitivo de 7 fases definido en
`core/nucleus/total_convergence.py`. Las fases [3] Memoria Estructural y
[6] Gravitación — esenciales para conectar con patrones previos y almacenar
el conocimiento con peso gravitacional — eran ignoradas en producción.

### Solución

**`core/convergence_hook.py`** — nuevo módulo wrapper (singleton, non-fatal)
- Encapsula `TotalConvergenceEngine.process()` en una función inyectable
- Non-fatal: si el motor falla, el pipeline continúa sin interrupciones
- Loggea evidencia de fases [3] y [6] por cada mensaje procesado
- Expone `should_block()` para cortar el pipeline si `action=block`

**`core/transport/pipeline_worker.py`** — inyección en `_process_one()`
- `run_convergence_cycle()` ejecuta ANTES de `ExternalGateway.receive_message()`
- Cada mensaje Telegram pasa por las 7 fases antes de generar respuesta
- Si `action_recommended=block`, el pipeline se corta sin enviar al usuario

**`services/core/routes/chat.py`** — inyección en `POST /v1/chat`
- `run_convergence_cycle()` ejecuta ANTES de `resolver.resolve()`
- Mensajes vía API REST también pasan por el ciclo completo

### Flujo garantizado post-deploy
```
Mensaje → [3] Memoria Estructural → [6] Gravitación → receive_message() → Respuesta
```

### Verificación en producción (2026-05-20 11:10 UTC)
```
OK  ciclo=CONV-0149BBD5
    fases_completadas=7/7
    [3] memoria=True
    [6] gravitacion=True
    action=proceed
    tier=HOT
    connections=10
    tiempo=174.1ms
```

### Tests
- `tests/integration/test_convergence_integration.py`: 32 tests, 100% PASSED (0.28s)
- Cobertura: 7 fases ejecutadas, orden [3]→[6], non-fatal, bloqueo, singleton,
  activación automática, fuentes múltiples (telegram/api/webhook/web)

### Archivos modificados
- `core/convergence_hook.py` (nuevo)
- `core/transport/pipeline_worker.py`
- `services/core/routes/chat.py`
- `tests/integration/test_convergence_integration.py` (nuevo)

### Commit / Deploy
- Commit: `910681d`
- Deploy: Vultr `140.82.28.181` — `vectrax-core` Up (healthy) — 2026-05-20 11:09 UTC

---

## [2026-04-11]

### Changes
- Added `RotatingFileHandler` to gateway (`~/.vectrax/gateway.log`, 5MB x3)
- Instrumented poll cycles (POLL), message receipt (RECV), fast-path (FAST),
  queue-path (QUEUED), handler errors (HANDLE ERROR), slow handlers (HANDLE SLOW)
- Added periodic STATUS summary every 5 min with all operational counters
- Fixed supervisor `stdout=DEVNULL` → `stdout=None` for docker log visibility
- Commit: `175d63f`

### Bottleneck Analysis (from real traffic load test)

**LLM cold-start**: First invocation after deploy takes ~15s (vs 1-4s warm).
Affects first user post-restart. Consider pre-warming the LLM provider on startup.

**Language leak**: "En chile ?" (Spanish) got an English response from the
pipeline worker. The `language_gate` / `enforce_final_answer` didn't catch it
on the worker path. The gate only applies to fast-path responses in the gateway.
Needs enforcement in `pipeline_worker.py` post-processing.

**Duplicate processing**: "Yo vivo en Miami" processed twice (49s apart).
`should_accept_job` dedup window is too narrow. Consider extending to 120s
or hashing by (user_id + content).

**No bottlenecks found in**:
- Queue wait time: 0.13-0.25s (excellent)
- Poll cycle: avg 28.6s, no anomalies
- Thread pool: 1/6 used, zero saturation
- Error rate: 0 handler errors, 0 poll errors

### Fixes Applied + Verified (commit `02b13f6`)

**1. LLM cold-start → warmup on startup**
- `pipeline_worker.py`: Pre-initializes `ExternalGateway` before main loop
- Before: first message post-deploy took 15s
- After: first message took 0.1s (150x faster)

**2. Language enforcement in pipeline worker**
- `pipeline_worker.py`: `enforce_language(response, user_lang)` applied before send
- Before: "En chile ?" → English response (language leak)
- After: "Yo vivo en Miami" → Spanish response (lang=es confirmed)

**3. Deduplication window 5s → 120s**
- `system_monitor.py`: `DUPLICATE_WINDOW_S` 5 → 120, stable `md5` hash
- Before: "Yo vivo en Miami" processed 2x (49s apart)
- After: 0 duplicates in queue across 8h session

## [2026-04-10] — Gateway Heartbeat Stability Fix

### Problem
The `telegram_gateway` process restarted every ~3 hours due to heartbeat stale
detection (REPEAT FAILURE #68-69 `gateway_stale`). The supervisor killed the
gateway when its heartbeat exceeded 90 seconds of age.

### Root Cause
The heartbeat was written only at the top of each polling cycle, **before** the
blocking `getUpdates` call (~30-40s). Two consecutive polls with any network
latency pushed the interval past the 90s threshold:

```
t=0s   → write heartbeat → poll (40s) → process
t=42s  → write heartbeat → poll (40s + network delay = 50s)
t=92s  → STALE → supervisor kills gateway
```

### Fix
- **`vectrax/telegram_gateway.py`**: Added a dedicated daemon thread
  (`gw-heartbeat`) that writes the heartbeat every 10 seconds, fully decoupled
  from the polling loop. Same strategy the `pipeline_worker` already uses.
- **`vectrax_supervisor.py`**: Increased `GATEWAY_HEARTBEAT_MAX_AGE` from 90s
  to 120s as additional safety margin.

### Verification
1-hour stability monitor (13 checks, 3 samples each):

```
[01] 01:56 UTC | gw_max=0.1s  | OK
[02] 02:00 UTC | gw_max=0.2s  | OK
[03] 02:05 UTC | gw_max=0.4s  | OK
[04] 02:10 UTC | gw_max=0.4s  | OK
[05] 02:15 UTC | gw_max=0.5s  | OK
[06] 02:20 UTC | gw_max=0.6s  | OK
[07] 02:25 UTC | gw_max=0.7s  | OK
[08] 02:29 UTC | gw_max=0.9s  | OK
[09] 02:34 UTC | gw_max=1.0s  | OK
[10] 02:39 UTC | gw_max=1.0s  | OK
[11] 02:44 UTC | gw_max=1.1s  | OK
[12] 02:49 UTC | gw_max=1.3s  | OK
[13] 02:54 UTC | gw_max=1.4s  | OK
```

- Before fix: heartbeat age reached 90-102s → kill every ~3h
- After fix: heartbeat age stays under 1.5s → zero restarts
- Commit: `e1acd80`

### 2026-06-02
- **fix(config): VX_ENV=production** — La variable de entorno `VX_ENV` no estaba definida en el entorno del contenedor Docker, por lo que el health endpoint devolvía `"env":"dev"` en producción. Fix: añadida al bloque `environment:` de `docker-compose.yml` (las variables de sistema tienen prioridad sobre `dotenv`). Verificado: `curl /health` ahora retorna `"env":"production"`.
- **fix(db): eliminado registro test_webhook_user de billing.db** — La tabla `billing` en producción contenía un registro simulado (`tg:test_webhook_user / cus_test_simulated_12345`) creado durante pruebas de webhook. Eliminado manualmente. DB limpia con 1 registro real del creador.
- **feat(onboarding): activación instantánea sin fricción** — El onboarding de 2 pasos (presencia + nombre obligatorio) bloqueaba al 87% de los usuarios. Nuevo flujo: el primer mensaje activa al usuario automáticamente sin pedir nombre, inicia el trial de 7 días y lo agrega al tracker de reentry. Los 26 usuarios bloqueados en estado `awaiting_name` se liberan automáticamente al siguiente mensaje.
- **feat(reentry): backfill de 13 usuarios al tracker** — Solo 5 de 30 usuarios estaban en el tracker de reentry 12-20h. Se agregaron los 13 usuarios reales faltantes. Resultado: 7 mensajes de reentry enviados automáticamente hoy por el meta_loop.
- **audit: salud del sistema 93%** — Auditoría integral de 14 módulos. 12/14 OK, 2/14 WARN (Learner: 22 ideas pendientes; Conversión: 0% FREE→PRO).
