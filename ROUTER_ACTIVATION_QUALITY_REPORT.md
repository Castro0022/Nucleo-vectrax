# ROUTER_ACTIVATION_QUALITY_REPORT

Reporte automático generado por
`tests/observability/test_router_activation_quality.py`.

## Resumen
- Total de casos: **30**
- Tasa de activación correcta: **86.7%** (26/30)
- Falsos positivos: **0**
- Falsos negativos: **4**
- Latencia SmartRouter — p50: **24.92 ms** · p95: **42.11 ms** · máx: **46.11 ms**
- Sink JSONL: `/private/var/folders/n7/nyk6625j3qv0lxxb0r1_5gn40000gn/T/pytest-of-mariobravo/pytest-56/test_router_activation_quality0/activation.jsonl`

## Rutas lentas (latencia > umbral)
Ninguna.

## Rutas potencialmente peligrosas
- `M26_ambig_typo` resolvió a `resolve_memory` en lugar de `resolve_identity`

## Detalle por caso

- ✅ `M01_hola` — saludo simple — router debe responder rápido
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_memory', 'route_cognitive', 'route_single'] · latency: 46.11 ms
- ✅ `M02_gracias` — agradecimiento — fast-path en pipeline real
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_memory', 'route_single'] · latency: 30.64 ms
- ✅ `M03_chao` — despedida — fast-path
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_memory', 'route_single'] · latency: 35.94 ms
- ✅ `M04_identity_who` — consulta identidad → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 15.94 ms
- ✅ `M05_identity_name` — pregunta por nombre → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 17.67 ms
- ✅ `M06_place_restaurant` — place search → Google Places
  - final: `resolve_places` · esperado en: ['resolve_places'] · latency: 38.73 ms
- ✅ `M07_place_pharmacy` — place search variante
  - final: `resolve_places` · esperado en: ['resolve_online', 'resolve_places'] · latency: 24.92 ms
- ✅ `M08_market_btc` — consulta de mercado → resolve_market
  - final: `resolve_market` · esperado en: ['resolve_market'] · latency: 1.49 ms
- ✅ `M09_market_trend` — mercado general
  - final: `resolve_market` · esperado en: ['resolve_online', 'resolve_market'] · latency: 33.55 ms
- ✅ `M10_memory_what_did_i` — memoria local
  - final: `resolve_local` · esperado en: ['resolve_local'] · latency: 22.14 ms
- ✅ `M11_memory_recuerdas` — memoria — verbo recordar
  - final: `resolve_local` · esperado en: ['resolve_local'] · latency: 14.85 ms
- ✅ `M12_online_definition` — pregunta factual
  - final: `resolve_online` · esperado en: ['resolve_online', 'route_single', 'route_cognitive'] · latency: 18.90 ms
- ✅ `M13_online_history` — factual histórico
  - final: `resolve_online` · esperado en: ['resolve_online', 'route_single'] · latency: 26.75 ms
- ❌ `M14_cognitive_strategy` — razonamiento profundo
  - final: `resolve_memory` · esperado en: ['route_multi', 'route_single', 'route_cognitive'] · latency: 34.48 ms
- ✅ `M15_cognitive_analyze` — análisis profundo
  - final: `resolve_online` · esperado en: ['route_multi', 'route_single', 'route_cognitive', 'resolve_online'] · latency: 1.43 ms
- ✅ `M16_command_ai` — comando /ai
  - final: `route_single` · esperado en: ['route_single'] · latency: 14.65 ms
- ✅ `M17_command_multi` — comando /multi
  - final: `route_multi` · esperado en: ['route_multi'] · latency: 15.85 ms
- ✅ `M18_command_help` — comando /help
  - final: `execute_command` · esperado en: ['execute_command'] · latency: 19.98 ms
- ✅ `M19_memory_note` — nota personal — ingestar
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_memory'] · latency: 42.11 ms
- ✅ `M20_memory_save` — comando guardar
  - final: `resolve_memory` · esperado en: ['resolve_memory'] · latency: 40.13 ms
- ❌ `M21_creator_status` — creator status
  - final: `resolve_memory` · esperado en: ['route_single', 'resolve_online', 'route_multi', 'resolve_local', 'route_cognitive'] · latency: 25.11 ms
- ✅ `M22_creator_orden` — creator orden cognitiva
  - final: `resolve_online` · esperado en: ['resolve_online', 'route_single', 'route_cognitive', 'resolve_local'] · latency: 16.50 ms
- ❌ `M23_sensitive_trading` — sensitivo trading — debe escalar multi/cognitive
  - final: `resolve_market` · esperado en: ['route_multi', 'route_cognitive', 'resolve_online'] · latency: 1.74 ms
- ✅ `M24_sensitive_health` — sensitivo salud
  - final: `resolve_online` · esperado en: ['route_multi', 'route_cognitive', 'resolve_online'] · latency: 33.54 ms
- ✅ `M25_ambig_short` — confirmación corta
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_memory', 'route_single'] · latency: 34.86 ms
- ❌ `M26_ambig_typo` — identidad con typo
  - final: `resolve_memory` · esperado en: ['route_single', 'resolve_identity', 'resolve_local'] · latency: 23.08 ms
- ✅ `M27_empty_ish` — señal vacía
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_memory', 'route_single'] · latency: 39.10 ms
- ✅ `M28_english_factual` — EN factual
  - final: `resolve_online` · esperado en: ['resolve_online', 'route_single'] · latency: 34.66 ms
- ✅ `M29_english_memory` — EN memoria local
  - final: `resolve_local` · esperado en: ['resolve_local'] · latency: 18.19 ms
- ✅ `M30_opportunity_signal` — mensaje con señal de oportunidad
  - final: `resolve_memory` · esperado en: ['resolve_online', 'resolve_local', 'resolve_memory', 'route_single'] · latency: 21.83 ms

## Ajustes priorizados sugeridos
- P1 — Investigar 4 casos con strategy diferente a la esperada (revisar `classify_intent` y `select_strategy`).
- P0 — 1 identidades se resolvieron a LLM en lugar de memoria. Posible fuga de privacidad. Bloquear hard en `classify_intent`.
