# Vectrax — Motores del sistema

Informe de los motores de Vectrax: su **función** y su **estado**. La función de cada
módulo se toma de su propio docstring/API; el estado se deduce del cableado real
(`core/meta_loop.py`, arranque de `services/core/app.py`, flags de entorno).

> Rama de referencia: `arch/system-quality-universe`.
> Evidencia: smoke import/carga de **48/48 motores OK** y suite hermética **2856 passed, 0 failed**.

## Leyenda de estado
- 🟢 **Activo** — cableado para correr automáticamente (en cada mensaje vía `total_convergence`, o en cada ciclo del `meta_loop` de 8 capas).
- ⚪ **Observador** — corre pero no fuerza acciones (modo no-enforce).
- 🟡 **Gated** — requiere un flag o credencial para tener efecto real.
- 🔵 **On-demand** — se invoca al usarse; no es un loop de fondo.

> "Activo" = cableado para ejecutarse. La activación en vivo depende de que el daemon/API estén corriendo.

## Índice
1. Núcleo cognitivo · 2. Gravedad · 3. Auto-observación · 4. Aprendizaje · 5. Routing · 6. Identidad/Memoria · 7. Mercado/eToro · 8. Otros motores · 9. Cognición

---

## Resumen por grupo (overview)

### 1. Núcleo cognitivo
- ⚪ **Total Convergence** — punto único de entrada; todo input pasa por el ciclo unificado.
- ⚪ **Presencia Pura / PresenciaObserver** — capa inhibidora (PERMIT/PAUSE/SILENCE/BLOCK); modo OBSERVER por defecto.
- 🟢 **Convergence Learner** — observa decisiones, recomienda ajustes (no aplica sin autorización).
- 🟢 **Law Signal** — las 7 Leyes pesan en cada emisión.
> Detalle completo: ver §Núcleo (en progreso).

### 2. Gravedad — *detallado abajo*
Índice por capas + grafo de estrellas + vector store + gobernanza de memoria + Word Gravity + convergencias.

### 3. Auto-observación — *detallado abajo*
meta_loop (8 capas) + observer + universe_observer + ledger + census + evolution + calidad (Pilares C/D).

### 4. Aprendizaje
- 🟢 **Learning Gate** · 🟢 **Pattern Refinement** · 🟢 **Observation Bias** · 🔵 ciclo **Anomaly→Investigation→Verification→Integrator** · ⚪ **Criterion** (criterio propio cross-dominio).
> Detalle completo: ver §Aprendizaje (en progreso).

### 5. Routing
- 🟢 **Smart Router** (selección de provider/modelo) · 🟢 **Router Learning** (aprendizaje pasivo) · 🟢 **Semantic Classifier**.

### 6. Identidad / Memoria
- 🟢 **Identity Layer** · 🟢 **Identity Anchor** · 🟢 **User Memory** · 🟢 **Core Memory** (permanente) · 🟢 **Fact Memory**.

### 7. Mercado / eToro
- 🟡 **Learning Engine** · 🟡 **Auto Executor** (OFF por defecto) · 🟡 **Market Observer** · 🟡 **Pattern Memory**.
> Detalle completo: ver §Mercado (en progreso).

### 8. Otros motores
- 🟢 **Intent Engine** · 🔵 **Risk Engine** · 🔵 **Proposal Engine** · 🟡 **Proactive Engine** · 🔵 **Hypothesis Engine** · 🔵 **Relevance Engine** · 🔵 **VX Prompt Engine** · 🟡 **Voice Engine (TTS)** · 🟡 **Universal Pattern Library** · 🟡 **Recovery Engine** (`RESILIENCE_ENABLED`) · 🟢 **Telegram Guard**.

### 9. Cognición (orquestador)
- 🔵 **Memory Engine** · 🔵 **Perception Engine** (Motor 1) · 🔵 **Reasoning Engine** (Motor 2).

---

## 🌌 Gravedad — memoria gravitacional (detalle)
Flujo: `mensaje → gravity_activator (pre-router) → cognitive_gravity (grafo de estrellas) / gravity_engine (índice por capas) → vector_store + retrieval`. En paralelo, la gobernanza (governor, decay, fusion, essential_summary) mantiene la salud de la memoria.

### Núcleo del índice
- 🟢 **Gravity Engine** · `core/learn/gravity_engine.py` (678 loc) — memoria por capas **HOT→WARM→COLD→DEEP** (nada se borra); alertas de convergencia y `universe_report`. API: `get_gravity_index()`, `GravityIndex`, `check_convergence_alerts()`, `universe_report(lang)`. Lo lee el observer cada ciclo.
- 🟢 **Cognitive Gravity Engine** · `vectrax/cognitive_gravity.py` (488 loc) — grafo dinámico; cada nodo es una **estrella con masa**, con distancia al núcleo, convergencias y clusters. API: `create_node`, `connect_nodes`, `update_mass`, `compute_distance_to_core`, `detect_convergence`, `cluster_nodes`, `recompute_all_masses`.

### Memoria profunda (vector store)
- 🟢 **Deep Memory Gate** · `core/gravity/gravity_engine.py` (264 loc) — `should_use_deep_memory(message, user_id, known_names)`, `DeepMemoryRouter`.
- 🟢 **Vector Store** · `core/gravity/vector_store.py` (703 loc) — almacén vectorial SQLite. API: `SQLiteVectorStore`, `DeepMemoryRecord`, `cosine_similarity`.
- 🟢 **Retrieval** · `core/gravity/retrieval.py` (268 loc) — recuperación con ranking combinado (similitud + masa + recencia). API: `retrieve(...)`, `rank_results(...)`.

### Gobernanza de memoria
- 🟢 **Memory Governor** · `core/gravity/governor.py` (376 loc) — memoria infinita → significativa; protege identidad, aísla por `user_id`, evita entropía.
- 🟢 **Concept Fusion** · `core/gravity/concept_fusion.py` (215 loc) — fusiona conceptos repetidos en identidades contextuales.
- 🟢 **Essential Summary** · `core/gravity/essential_summary.py` (313 loc) — destila sabiduría persistente.
- 🟢 **Memory Decay** · `core/gravity/decay.py` (182 loc) + **Gravity Decay v1** · `core/learn/gravity_decay.py` (90 loc) — degrada lo inactivo (HOT→WARM 7d · WARM→COLD 45d · COLD→DEEP 180d), sin borrar.
- 🟢 **Mass Tracker** · `core/gravity/mass_tracker.py` (152 loc) — masa cognitiva por categoría.

### Palabras y convergencias
- 🟢 **Word Gravity Index (WGI)** · `core/word_gravity.py` (527 loc) — cada palabra acumula masa; activa su constelación. API: `upsert_word`, `get_effective_mass(word, user_id)`, `record_activation`, `apply_decay`, `seed_global_index`, `get_top_words`.
- 🟢 **Gravity Activator** · `core/gravity_activator.py` (280 loc) — activación de constelaciones **antes** del SmartRouter. API: `activate_gravity(content, user_id, max_activations)`.
- 🟢 **Convergence History** · `core/learn/convergence_history.py` (262 loc) — nacimiento/evolución/disolución de convergencias. API: `record_snapshot`, `get_active`, `get_timeline(days)`, `build_context`.
- 🟢 **Convergence Hook** · `core/convergence_hook.py` (143 loc) — wrapper no-fatal del ciclo de convergencia total.
- 🔵 **Pipeline convergence** · `pipeline/convergence.py` (67 loc) — paso 2, `detect_convergence(signals)`.

**Estado del grupo:** 16/16 operativos; todo activo salvo `pipeline/convergence` (on-demand).

---

## 👁 Auto-observación — el sistema se observa a sí mismo (detalle)
Flujo: `meta_loop.reflect()` (8 capas, cada ciclo) → `autonomous_observer.observe_and_record()` → `observation_ledger`. En paralelo: `universe_observer` → `universe_census` → `/v1/universe` → UI. Pilar C escribe calidad → Pilar D la proyecta.

- 🟢 **Meta Loop** · `core/meta_loop.py` (461 loc) — reflexión post-ciclo con **8 capas**: 1 Actividad · 2 Salud · 3 Ritmo · 4 Ideas (15 min) · 5 Observación autónoma · 6 Alertas Telegram · 7 RAM (cada hora) · 8 Evolución diaria. API: `reflect(ingested_count)`.
- 🟢 **Autonomous Observer** · `core/self_observation/autonomous_observer.py` (540 loc) — compara snapshots y registra cambios en 6 dominios (gravity, market, convergence, operator, health, user). API: `observe_and_record()`. (Layer 5)
- 🟢 **Universe Observer** · `core/self_observation/universe_observer.py` (439 loc) — fusiona estado gravitacional + operacional. API: `observe_universe()`, `UniverseSnapshot`. Sirve `/v1/universe`.
- 🟢 **Observation Ledger** · `core/self_observation/observation_ledger.py` (170 loc) — memoria persistente (SQLite, auto-prune 5000, WAL). API: `record(...)`, `get_recent`, `get_by_domain`, `count`.
- 🟢 **Universe Census** · `core/universe_census.py` (266 loc) — única fuente de verdad; cuenta todo (incl. `quality`) con cache TTL 10s. API: `get_census()`, `UniverseCensus`.
- 🟢 **Evolution Memory** · `core/self_observation/evolution_memory.py` (291 loc) — snapshots diarios (longitudinal). API: `record_daily_snapshot`, `get_snapshot(days_ago)`, `get_evolution_context`. (Layer 8)
- 🟢 **Quality Entities (Pilar D)** · `core/self_observation/quality_entities.py` (301 loc) — mapea eventos de calidad a fenómenos del universo (fallos=estrellas, recuperaciones=enfriamiento, convergencias). API: `get_quality_entities(limit)`, `get_quality_summary(limit)`.
- 🟡 **Quality Observer (Pilar C)** · `core/self_observation/quality_observer.py` (456 loc) — registra salud de ingeniería (test/code/quality/runtime) con sanitizador de privacidad. API: `record_failed_test`, `record_recovered_test`, `record_code_change`, `record_runtime_error`, `record_suite_start/end`, `bind_vault_dir`. Escritor opt-in: `VECTRAX_TEST_LEDGER=1`.
- 🔵 **Code Change Observer** · `core/self_observation/code_change_observer.py` (219 loc) — commits/diffs → `code_change` (sin contenido sensible). API: `observe_commit(rev, repo_dir)`, `observe_diff(repo_dir)`, `main(argv)`. No instala hooks.

**Estado del grupo:** 9/9 operativos; Pilar C listo pero su escritor es opt-in; `code_change_observer` es on-demand.

---

## 🧠 Núcleo cognitivo (detalle)
El núcleo decide **cómo entra** y **si sale** cada emisión. Flujo: `input → total_convergence.process()` (ciclo unificado) → motores internos → emisión evaluada por PresenciaObserver (con peso de LawSignal) → ConvergenceLearner registra la decisión. La médula operativa (`UniversalBus` + `OperatorRuntime`) transporta eventos entre capas.

- 🟢 **Total Convergence** · `core/nucleus/total_convergence.py` (946 loc) — punto único de entrada; `process()` ejecuta el ciclo unificado de 7 fases (percepción → clasificación → memoria estructural → análisis → síntesis → gravitación → aprendizaje) para TODO input: texto, código, errores, conversaciones, decisiones y resultados. API: `get_convergence_engine()`, `TotalConvergenceEngine` (`activate`, `deactivate`, `is_active`, `process`, `status`), `InputType`, `ConvergencePhase`, `ConvergenceRecord`. Estado: activo como entrada obligatoria; puede activarse/desactivarse por motor.
- ⚪ **PresenciaObserver** · `core/nucleus/presencia_pura.py` (801 loc) — capa inhibidora que evalúa cada emisión por **origen, soberanía, convergencia y ruido** y decide `PERMIT / PAUSE / SILENCE / BLOCK` sin reemplazar a ningún motor. API: `get_observer()`, `PresenciaObserver` (`evaluate`, `observe`, `set_mode`, `get_records`, `get_stats`, `disconnect`), `EmissionSignal`, `InhibitionRecord`, `EmissionOrigin`. Estado: **OBSERVER por defecto** (`enforced=False`, registra sin bloquear). Pasa a **ACTIVE** (`enforced=True`) solo con `activate_observer()` y autorización.
  - Reglas principales: `origin==UNKNOWN → BLOCK`, `sovereignty<0.30 → BLOCK`, `convergence<0.30 → SILENCE`, `noise>0.90 + convergence<0.5 → BLOCK`, `noise>0.80 → PAUSE`, resto `PERMIT`.
- 🟡 **Presencia Pura (modo)** · `core/nucleus/presencia_pura.py` — modo de núcleo con **cero tokens externos**: bloquea llamadas a OpenAI/Gemini/Claude manteniendo el ciclo interno activo (convergencia, memoria, gravedad, identidad). API: `activate`, `deactivate`, `is_active`, `status`, `check_and_block_llm`, `check_and_block_online`. Estado: activable bajo demanda.
- 🟢 **Convergence Learner** · `core/nucleus/convergence_learner.py` (667 loc) — cierra la conciencia operacional: observa decisiones del observer + resultado posterior (`IMPROVED / NEUTRAL / DEGRADED`), detecta patrones por motor y recomienda ajustes de umbral con evidencia. API: `get_learner()`, `ConvergenceLearner` (`record_decision`, `record_outcome`, `analyze`, `generate_recommendations`, `approve_recommendation`, `reject_recommendation`, `advance_phase`). Estado: activo en fases observar→aprender→recomendar; aplicar cambios requiere autorización del creador.
- 🟢 **Law Signal** · `core/nucleus/law_signal.py` (194 loc) — traduce violaciones de las 7 Leyes en ajustes de score (convergencia, soberanía, ruido) **antes** de que PresenciaObserver decida. API: `build_law_signal(violations)`, `LawSignal` (`is_severe`, `has_impact`, `summary`), `LawImpact`. Estado: activo; las violaciones fluyen desde el gateway externo.

### Médula operativa del núcleo
- 🟢 **Universal Bus** · `core/operator/universal_bus.py` (411 loc) — bus central de eventos entre capas; PresenciaObserver se conecta como observador. API: `get_universal_bus()`, `UniversalBus` (`publish`, `emit`, `broadcast`, `subscribe`, `unsubscribe`, `get_history`, `get_stats`), `Channels`, `BusEvent`, `EventPriority`.
- 🟡 **Operator Runtime** · `core/operator/activation.py` (1126 loc) — runtime del operador: ciclo `perceive → interpret → decide → act → verify → log`, con dominios permitidos/restringidos. API: `get_runtime()`, `activate_operator()`, `OperatorRuntime` (`activate`, `run_cycle`, `pause`, `resume`, `is_active`), `run_operator_forever(interval, on_cycle)`. Estado: cableado; el loop en vivo corre cuando se activa el operador.

**Estado del grupo:** núcleo cognitivo operativo. PresenciaObserver está en **OBSERVER** por seguridad; ConvergenceLearner aprende y recomienda sin aplicar cambios automáticamente; Presencia Pura y Operator Runtime son activables.

## 🔬 Aprendizaje (detalle)
Dos capas complementarias: (A) **aprendizaje selectivo** que decide qué entra al universo gravitacional y lo auto-organiza, y (B) un **ciclo de aprendizaje continuo** (señal→investigación→verificación→integración) que solo guarda lo verificado. Más el **auto-aprendizaje del router**.

### A. Aprendizaje selectivo (entra al universo)
- 🟢 **Learning Gate** · `core/learn/learning_gate.py` (165 loc) — antes de incorporar experiencia evalúa **novedad / coherencia / impacto** → `ACTIVE` (transforma el universo) vs `PASSIVE` (registra sin transformar). API: `evaluate()`, `LearningDecision` (`is_active`), `LearningType`. Corre en el ingest.
- 🟢 **Pattern Refinement** · `core/learn/pattern_refinement.py` (172 loc) — cada N interacciones el universo se auto-organiza: lo útil gana masa y se acerca al centro, lo inútil se aleja, convergencias débiles se disuelven. API: `run_refinement_cycle()`, `tick()`, `RefinementResult`.
- 🟢 **Observation Bias** · `core/learn/observation_bias.py` (186 loc) — recalcula los pesos de observación por dominio; cierra el loop aprendizaje→observación→mejor aprendizaje. API: `compute_bias()`, `get_bias()`, `get_domain_weight()`, `ObservationBias`. Lo consume el `autonomous_observer` (y UPL le suma su boost aditivo).
- 🟡 **Active Learning Orchestrator** · `core/learn/active_learning.py` (705 loc) — conecta todos los subsistemas en un ciclo autónomo continuo (detectar patrones → generar → integrar). API: `get_orchestrator()`, `ActiveLearningOrchestrator` (`activate`, `deactivate`, `is_active`, `run_cycle`, `on_file_ingested`, `status`). Estado: activable.

### B. Ciclo de aprendizaje continuo (solo guarda lo verificado)
Flujo: `pipeline.process_event()` → ANOMALY → INVESTIGATION → VERIFICATION → INTEGRATOR.
- 🔵 **Anomaly Detector** · `core/learning_cycle/anomaly_detector.py` (407 loc) — detecta patrones fuera de lo normal **sin concluir verdad** (solo señal). API: `AnomalyDetector` (`analyze`, `feed_batch`, `stats`, `reset`), `AnomalySignal`, `InputEvent`.
- 🔵 **Investigation Engine** · `core/learning_cycle/investigation_engine.py` (480 loc) — al marcarse una anomalía, busca contexto en múltiples fuentes y genera **hipótesis múltiples**. API: `InvestigationEngine` (`register_external_source`, `investigate`, `investigate_batch`, `stats`).
- 🔵 **Verification Engine** · `core/learning_cycle/verification_engine.py` (493 loc) — valida hipótesis con evidencia cruzada, asigna confianza y **rechaza** conclusiones sin datos. API: `VerificationEngine` (`verify`, `verify_batch`, `stats`).
- 🔵 **Learning Integrator** · `core/learning_cycle/learning_integrator.py` (425 loc) — solo almacena **patrones verificados** como reglas reutilizables y actualiza memoria estructural. API: `LearningIntegrator` (`integrate`, `integrate_batch`, `stats`).
- 🔵 **Learning Pipeline** · `core/learning_cycle/pipeline.py` (373 loc) — orquesta el ciclo completo SEÑAL→INVESTIGACIÓN→VERIFICACIÓN→INTEGRACIÓN. API: `get_learning_pipeline()`, `LearningPipeline` (`process_event`, `process_batch`, `run_cycle`, `status`).

### C. Auto-aprendizaje del Router
- 🟢 **Router Learning** · `core/router_learning.py` (927 loc) — aprendizaje **pasivo**: observa decisiones del SmartRouter, evalúa calidad, clasifica errores y sugiere ajustes de umbral con reporte. API: `RouterLearningEngine` (`analyze`, `classify_errors`, `suggest_threshold_adjustments`, `generate_report`), `RouterDecisionLedger`, `PostResolutionEvaluator`, `get_ledger()`.
- 🟡 **Router Learning Cycle** · `core/router_learning_cycle.py` (444 loc) — ciclo continuo que detecta conflictos recurrentes (semántico vs regex) y genera **propuestas** de mejora (no aplica sin aprobación). API: `RouterLearningCycle` (`activate`, `deactivate`, `is_active`, `run_cycle`, `get_pending_proposals`).

### D. Criterio aprendido (opinión propia)
- ⚪ **Criterion** · `core/learn/criterion.py` — forma y expresa criterio propio **cross-dominio** (market, freight_logistics, …) desde evidencia persistida (WR/E/Wilson/N/confianza/masa); entiende el **tema concreto** de la pregunta y se abstiene de forma constructiva si no hay experiencia relacionada, sin fabricar. Read-only; cableado como compuerta en `external_gateway` (STEP 4.2a3, precede al narrador self-aware). API: `build_criterion(domain, query)`, `detect_criterion_request`, `detect_domain`, `strongest_domain`, `rank_domain_evidence`.

**Estado del grupo:** el aprendizaje selectivo (gate/refinement/bias) está activo en el flujo de ingest. El ciclo continuo y los orquestadores (active_learning, router_learning_cycle) son activables/on-demand y **generan propuestas sin aplicar cambios** sin autorización — coherente con "aprender solo de patrones confirmados".

## 📈 Mercado / eToro (detalle)
Todo el grupo es **gated**: requiere credenciales de broker y datos de mercado, y la ejecución real exige autorización explícita del creador. Ciclo: `market_mode (¿abierto?) → market_observer (4 condiciones) → signal_recorder → outcome_tracker → pattern_memory → learning_engine (propone) → [auto_executor PAPER/LIVE | trade_executor con autorización]`.

### Observación y señales
- 🟡 **Market Observer** · `connectors/etoro/market_observer.py` (441 loc) — evalúa **4 condiciones** en tiempo real (PRECIO_EN_ZONA, VOLUMEN_RELATIVO, ALINEACIÓN_TEMPORAL, DIRECCIÓN_TENDENCIA) y clasifica el escenario en `NO OPERABLE / PRE-OPERABLE / OPERABLE`. API: `evaluate()`, `set_mode()`, `ScenarioState`, `ScenarioResult`, `ConditionResult`. Necesita feed de mercado.
- 🟡 **Signal Recorder** · `connectors/etoro/signal_recorder.py` (252 loc) — guarda cada escenario PRE/OPERABLE como señal con id único y estado. API: `record_from_scenario()`, `get_pending_signals()`, `get_signal_stats()`, `MarketSignal`, `SignalStatus`.
- 🟡 **Outcome Tracker** · `connectors/etoro/outcome_tracker.py` (215 loc) — resuelve señales pendientes comparando precio actual vs entrada/invalidación. API: `resolve_pending_signals()`, `get_recent_outcomes()`.
- 🟡 **Pattern Memory** · `connectors/etoro/pattern_memory.py` (256 loc) — construye memoria estadística de señales resueltas; un patrón es "usable" con N≥15, WR≥55%, E>0. API: `update_patterns_from_signals()`, `get_patterns()`, `get_best_patterns()`, `PatternStats` (`win_rate`, `is_usable`).
- 🟡 **Market Mode** · `connectors/etoro/market_mode.py` (72 loc) — distingue Modo Mercado vs Modo Memoria. API: `is_market_open()`, `get_open_symbols()`, `get_active_mode()`. Lo consulta el `autonomous_observer`.
- 🟡 **Market Context** · `connectors/etoro/market_context.py` (206 loc) — acumula insight por símbolo para responder opiniones desde memoria propia. API: `record_market_interest()`, `get_market_insight()`, `get_watchlist_summary()`.

### Aprendizaje y propuestas
- 🟡 **Learning Engine** · `connectors/etoro/learning_engine.py` (733 loc) — cierra el loop de mercado: OBSERVE → record → learn → propose. API: `run_learning_cycle()`, `load_proposals()`, `update_proposal_status()`, `get_learning_status()`, `TradeProposal`.
- 🟡 **Entry Validator** · `connectors/etoro/entry_validator.py` (264 loc) — puerta de 9 condiciones antes de permitir una entrada. API: `validate_entry()`.
- 🟡 **Position Manager** · `connectors/etoro/position_manager.py` (273 loc) — monitorea posiciones abiertas y condiciones de salida (stop/take/coherencia/tiempo). API: `check_open_positions()`, `get_open_positions_summary()`.

### Ejecución (gated + autorización)
- 🟡 **Auto Executor** · `connectors/etoro/auto_executor.py` (585 loc) — ejecución automática por fases con límites duros. Modos (persistidos en `~/.vectrax/etoro_auto_config.json`): **OFF (default)** / PAPER / LIVE. Límites: `max_position_usd`, `max_daily_loss_usd`, `max_consecutive_losses=3` → **auto-shutdown a PAPER**; kill switch `halt()`/`unhalt()`; `approve_symbol()` por símbolo. API: `get_config()`, `get_mode()`, `record_symbol_op()`, `AutoMode`. Estado: **OFF por defecto**.
- ⚪ **Trade Executor** · `connectors/etoro/trade_executor.py` (348 loc) y **Trading (supervisado)** · `connectors/etoro/trading.py` (183 loc) — ejecutan **solo con autorización explícita del creador** (propose→approve→reject). API: `execute_open()`, `execute_close()`, `is_creator()`, `get_audit_log()`; `propose_trade()`, `approve_trade()`, `reject_trade()`.

### Conectores de broker (datos + órdenes)
- 🟡 **eToro Client** · `connectors/etoro/etoro_client.py` (570 loc) + `client.py` + `market.py` + `portfolio.py` — cliente REST autenticado (httpx persistente, HTTP/2, cache de instrument id). API: `get_price()`, `get_candles()`, `open_position()`, `close_position()`, `get_portfolio()`, `healthcheck()`. Requiere credenciales eToro.
- 🟡 **Alpaca Client** · `connectors/alpaca/alpaca_client.py` (417 loc) — conector de broker alternativo (paper trading). API: `health_check()`, `get_account()`, `get_positions()`, `submit_order()`, `close_position()`, `get_bars()`. Se selecciona por `BROKER_PROVIDER`. Requiere API keys.

**Estado del grupo:** operativo a nivel de código pero **gated**. Por defecto el sistema observa/aprende mercado sin ejecutar (`auto_executor=OFF`); la ejecución PAPER/LIVE y las órdenes reales requieren credenciales de broker + activación manual y, en LIVE, autorización del creador y cumplimiento de límites de riesgo.

---

## ⚡ Activación / Orquestación de motores
Capa única y declarativa para conectar/activar todos los motores: `core/orchestration/` (se apoya en el operator runtime y el meta_loop existentes; no los reemplaza).

### Tiers de seguridad
- **CORE** — cognitivo/interno; seguro de activar.
- **OBSERVE** — observación/aprendizaje; no destructivo.
- **GATED_INTERNAL** — interno sensible; se activa en su **sub-modo seguro** (operador GUIDED, PresenciaObserver OBSERVER). El modo que fuerza (ACTIVE/no-GUIDED) jamás lo enciende el bootstrap. Respeta flags (`RESILIENCE_ENABLED`, etc.).
- **EXTERNAL** — credenciales/dinero/red (eToro, providers cloud, TTS). **Solo health-check**; nunca se pone en LIVE automáticamente (`auto_executor` marcado `__never__`).

### Perfiles
- `safe` (default) — activa CORE+OBSERVE, GATED_INTERNAL en sub-modo seguro, EXTERNAL solo verificado.
- `full` — igual, pero permite activar GATED_INTERNAL marcados; **EXTERNAL/LIVE sigue requiriendo autorización explícita** (no se enciende aquí).

### Cómo se activa
- **Arranque del API** (`services/core/app.py` `on_startup`): lanza `activate_all('safe')` en un **hilo daemon** (no bloquea ni puede tumbar el arranque). Controlado por `VECTRAX_ACTIVATE_ENGINES` (default `safe`; `off` lo desactiva; `full` usa el perfil full).
- **Comando único (local):** `make activate` · `make engines` (estado) · o `python -m core.orchestration [safe|full|status|dry]`.

### Observabilidad
- `GET /v1/engines` — estado **read-only** por motor (tier, disponible, gating). Igual que `/v1/universe` y `/v1/health`: solo lectura, sin secretos.
- Cada bootstrap deja huella en el `observation_ledger` (dominio `activation`) y el snapshot del universo expone un bloque `engines` (conteos por tier + lista).

### Seguridad (decisión explícita)
NO existe ningún endpoint HTTP que dispare la activación: activar motores es una operación privilegiada (decisión operativa que requiere autorización del creador), por eso solo ocurre en el arranque del proceso y por el comando local. Así no se abre ninguna puerta de activación remota sin autenticación.

### Garantías
Idempotente · defensivo (un motor que falla no aborta el resto) · aditivo (no cambia el comportamiento de ningún motor; solo los cablea) · `dry_run` 100% read-only.
Archivos: `core/orchestration/engine_registry.py`, `core/orchestration/bootstrap.py`, `core/orchestration/__main__.py`, `services/core/routes/engines.py`. Tests: `tests/test_orchestration.py` (16).
