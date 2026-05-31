# ROUTER_ACTIVATION_QUALITY_REPORT

Reporte automático generado por
`tests/observability/test_router_activation_quality.py`.

## Resumen
- Total de casos: **30**
- Tasa de activación correcta: **80.0%** (24/30)
- Falsos positivos: **0**
- Falsos negativos: **6**
- Latencia SmartRouter — p50: **23.03 ms** · p95: **46.71 ms** · máx: **3599.19 ms**
- Sink JSONL: `/private/var/folders/n7/nyk6625j3qv0lxxb0r1_5gn40000gn/T/pytest-of-mariobravo/pytest-45/test_router_activation_quality0/activation.jsonl`

## Rutas lentas (latencia > umbral)
- `M01_hola`: 3599.19 ms (umbral 80 ms)

## Rutas potencialmente peligrosas
- `M26_ambig_typo` resolvió a `route_single` en lugar de `resolve_identity`

## Detalle por caso

- ✅ `M01_hola` — saludo simple — router debe responder rápido
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'route_cognitive', 'resolve_memory'] · latency: 3599.19 ms
- ✅ `M02_gracias` — agradecimiento — fast-path en pipeline real
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 33.40 ms
- ✅ `M03_chao` — despedida — fast-path
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 28.82 ms
- ✅ `M04_identity_who` — consulta identidad → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 7.27 ms
- ✅ `M05_identity_name` — pregunta por nombre → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 24.14 ms
- ✅ `M06_place_restaurant` — place search → Google Places
  - final: `resolve_places` · esperado en: ['resolve_places'] · latency: 20.06 ms
- ✅ `M07_place_pharmacy` — place search variante
  - final: `resolve_places` · esperado en: ['resolve_places', 'resolve_online'] · latency: 23.03 ms
- ✅ `M08_market_btc` — consulta de mercado → resolve_market
  - final: `resolve_market` · esperado en: ['resolve_market'] · latency: 26.20 ms
- ✅ `M09_market_trend` — mercado general
  - final: `resolve_market` · esperado en: ['resolve_market', 'resolve_online'] · latency: 27.63 ms
- ❌ `M10_memory_what_did_i` — memoria local
  - final: `resolve_online` · esperado en: ['resolve_local'] · latency: 26.20 ms
- ❌ `M11_memory_recuerdas` — memoria — verbo recordar
  - final: `resolve_online` · esperado en: ['resolve_local'] · latency: 23.78 ms
- ✅ `M12_online_definition` — pregunta factual
  - final: `resolve_online` · esperado en: ['route_single', 'route_cognitive', 'resolve_online'] · latency: 8.03 ms
- ✅ `M13_online_history` — factual histórico
  - final: `resolve_online` · esperado en: ['route_single', 'resolve_online'] · latency: 16.18 ms
- ✅ `M14_cognitive_strategy` — razonamiento profundo
  - final: `route_single` · esperado en: ['route_multi', 'route_single', 'route_cognitive'] · latency: 46.71 ms
- ✅ `M15_cognitive_analyze` — análisis profundo
  - final: `resolve_online` · esperado en: ['route_multi', 'route_single', 'route_cognitive', 'resolve_online'] · latency: 28.27 ms
- ✅ `M16_command_ai` — comando /ai
  - final: `route_single` · esperado en: ['route_single'] · latency: 7.75 ms
- ✅ `M17_command_multi` — comando /multi
  - final: `route_multi` · esperado en: ['route_multi'] · latency: 26.42 ms
- ✅ `M18_command_help` — comando /help
  - final: `execute_command` · esperado en: ['execute_command'] · latency: 6.31 ms
- ❌ `M19_memory_note` — nota personal — ingestar
  - final: `route_single` · esperado en: ['resolve_local', 'resolve_memory'] · latency: 26.60 ms
- ❌ `M20_memory_save` — comando guardar
  - final: `route_single` · esperado en: ['resolve_memory'] · latency: 34.37 ms
- ✅ `M21_creator_status` — creator status
  - final: `route_single` · esperado en: ['route_single', 'resolve_online', 'resolve_local', 'route_multi', 'route_cognitive'] · latency: 14.16 ms
- ✅ `M22_creator_orden` — creator orden cognitiva
  - final: `resolve_online` · esperado en: ['resolve_local', 'route_single', 'route_cognitive', 'resolve_online'] · latency: 9.79 ms
- ❌ `M23_sensitive_trading` — sensitivo trading — debe escalar multi/cognitive
  - final: `resolve_market` · esperado en: ['route_multi', 'route_cognitive', 'resolve_online'] · latency: 35.70 ms
- ✅ `M24_sensitive_health` — sensitivo salud
  - final: `resolve_online` · esperado en: ['route_multi', 'route_cognitive', 'resolve_online'] · latency: 11.83 ms
- ✅ `M25_ambig_short` — confirmación corta
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 13.51 ms
- ✅ `M26_ambig_typo` — identidad con typo
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_identity'] · latency: 18.60 ms
- ✅ `M27_empty_ish` — señal vacía
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 14.35 ms
- ✅ `M28_english_factual` — EN factual
  - final: `resolve_online` · esperado en: ['route_single', 'resolve_online'] · latency: 9.00 ms
- ❌ `M29_english_memory` — EN memoria local
  - final: `resolve_online` · esperado en: ['resolve_local'] · latency: 9.49 ms
- ✅ `M30_opportunity_signal` — mensaje con señal de oportunidad
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory', 'resolve_online'] · latency: 16.34 ms

## Ajustes priorizados sugeridos
- P1 — Investigar 6 casos con strategy diferente a la esperada (revisar `classify_intent` y `select_strategy`).
- P2 — 1 rutas exceden el umbral de latencia. Revisar `detect_context` y cache de embeddings.
- P0 — 1 identidades se resolvieron a LLM en lugar de memoria. Posible fuga de privacidad. Bloquear hard en `classify_intent`.
