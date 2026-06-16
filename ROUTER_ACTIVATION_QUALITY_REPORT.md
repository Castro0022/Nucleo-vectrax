# ROUTER_ACTIVATION_QUALITY_REPORT

Reporte automático generado por
`tests/observability/test_router_activation_quality.py`.

## Resumen
- Total de casos: **30**
- Tasa de activación correcta: **80.0%** (24/30)
- Falsos positivos: **0**
- Falsos negativos: **6**
- Latencia SmartRouter — p50: **24.42 ms** · p95: **46.67 ms** · máx: **49.96 ms**
- Sink JSONL: `/private/var/folders/n7/nyk6625j3qv0lxxb0r1_5gn40000gn/T/pytest-of-mariobravo/pytest-79/test_router_activation_quality0/activation.jsonl`

## Rutas lentas (latencia > umbral)
Ninguna.

## Rutas potencialmente peligrosas
- `M26_ambig_typo` resolvió a `route_single` en lugar de `resolve_identity`

## Detalle por caso

- ✅ `M01_hola` — saludo simple — router debe responder rápido
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'route_cognitive', 'resolve_memory'] · latency: 49.96 ms
- ✅ `M02_gracias` — agradecimiento — fast-path en pipeline real
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 33.16 ms
- ✅ `M03_chao` — despedida — fast-path
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 38.66 ms
- ✅ `M04_identity_who` — consulta identidad → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 22.42 ms
- ✅ `M05_identity_name` — pregunta por nombre → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 39.83 ms
- ✅ `M06_place_restaurant` — place search → Google Places
  - final: `resolve_places` · esperado en: ['resolve_places'] · latency: 13.26 ms
- ✅ `M07_place_pharmacy` — place search variante
  - final: `resolve_places` · esperado en: ['resolve_places', 'resolve_online'] · latency: 37.98 ms
- ✅ `M08_market_btc` — consulta de mercado → resolve_market
  - final: `resolve_market` · esperado en: ['resolve_market'] · latency: 22.83 ms
- ✅ `M09_market_trend` — mercado general
  - final: `resolve_market` · esperado en: ['resolve_market', 'resolve_online'] · latency: 15.89 ms
- ❌ `M10_memory_what_did_i` — memoria local
  - final: `resolve_online` · esperado en: ['resolve_local'] · latency: 23.25 ms
- ❌ `M11_memory_recuerdas` — memoria — verbo recordar
  - final: `resolve_online` · esperado en: ['resolve_local'] · latency: 27.57 ms
- ✅ `M12_online_definition` — pregunta factual
  - final: `resolve_online` · esperado en: ['route_single', 'route_cognitive', 'resolve_online'] · latency: 18.55 ms
- ✅ `M13_online_history` — factual histórico
  - final: `resolve_online` · esperado en: ['route_single', 'resolve_online'] · latency: 10.91 ms
- ✅ `M14_cognitive_strategy` — razonamiento profundo
  - final: `route_single` · esperado en: ['route_single', 'route_multi', 'route_cognitive'] · latency: 46.67 ms
- ✅ `M15_cognitive_analyze` — análisis profundo
  - final: `resolve_online` · esperado en: ['route_single', 'route_multi', 'route_cognitive', 'resolve_online'] · latency: 23.64 ms
- ✅ `M16_command_ai` — comando /ai
  - final: `route_single` · esperado en: ['route_single'] · latency: 24.15 ms
- ✅ `M17_command_multi` — comando /multi
  - final: `route_multi` · esperado en: ['route_multi'] · latency: 15.09 ms
- ✅ `M18_command_help` — comando /help
  - final: `execute_command` · esperado en: ['execute_command'] · latency: 14.13 ms
- ❌ `M19_memory_note` — nota personal — ingestar
  - final: `route_single` · esperado en: ['resolve_local', 'resolve_memory'] · latency: 14.26 ms
- ❌ `M20_memory_save` — comando guardar
  - final: `route_single` · esperado en: ['resolve_memory'] · latency: 34.47 ms
- ✅ `M21_creator_status` — creator status
  - final: `route_single` · esperado en: ['resolve_local', 'route_cognitive', 'route_single', 'route_multi', 'resolve_online'] · latency: 38.31 ms
- ✅ `M22_creator_orden` — creator orden cognitiva
  - final: `resolve_online` · esperado en: ['resolve_local', 'route_single', 'route_cognitive', 'resolve_online'] · latency: 14.66 ms
- ❌ `M23_sensitive_trading` — sensitivo trading — debe escalar multi/cognitive
  - final: `resolve_market` · esperado en: ['route_multi', 'route_cognitive', 'resolve_online'] · latency: 39.02 ms
- ✅ `M24_sensitive_health` — sensitivo salud
  - final: `resolve_online` · esperado en: ['route_multi', 'route_cognitive', 'resolve_online'] · latency: 39.03 ms
- ✅ `M25_ambig_short` — confirmación corta
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 41.05 ms
- ✅ `M26_ambig_typo` — identidad con typo
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_identity'] · latency: 29.30 ms
- ✅ `M27_empty_ish` — señal vacía
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_memory'] · latency: 24.42 ms
- ✅ `M28_english_factual` — EN factual
  - final: `resolve_online` · esperado en: ['route_single', 'resolve_online'] · latency: 14.79 ms
- ❌ `M29_english_memory` — EN memoria local
  - final: `resolve_online` · esperado en: ['resolve_local'] · latency: 11.96 ms
- ✅ `M30_opportunity_signal` — mensaje con señal de oportunidad
  - final: `route_single` · esperado en: ['resolve_local', 'route_single', 'resolve_online', 'resolve_memory'] · latency: 39.48 ms

## Ajustes priorizados sugeridos
- P1 — Investigar 6 casos con strategy diferente a la esperada (revisar `classify_intent` y `select_strategy`).
- P0 — 1 identidades se resolvieron a LLM en lugar de memoria. Posible fuga de privacidad. Bloquear hard en `classify_intent`.
