# ROUTER_ACTIVATION_QUALITY_REPORT

Reporte automático generado por
`tests/observability/test_router_activation_quality.py`.

## Resumen
- Total de casos: **30**
- Tasa de activación correcta: **86.7%** (26/30)
- Falsos positivos: **0**
- Falsos negativos: **4**
- Latencia SmartRouter — p50: **0.17 ms** · p95: **6.94 ms** · máx: **59.91 ms**
- Sink JSONL: `/private/var/folders/n7/nyk6625j3qv0lxxb0r1_5gn40000gn/T/pytest-of-mariobravo/pytest-14/test_router_activation_quality0/activation.jsonl`

## Rutas lentas (latencia > umbral)
Ninguna.

## Rutas potencialmente peligrosas
- `M26_ambig_typo` resolvió a `resolve_memory` en lugar de `resolve_identity`

## Detalle por caso

- ✅ `M01_hola` — saludo simple — router debe responder rápido
  - final: `resolve_memory` · esperado en: ['route_cognitive', 'resolve_memory', 'resolve_local', 'route_single'] · latency: 59.91 ms
- ✅ `M02_gracias` — agradecimiento — fast-path en pipeline real
  - final: `resolve_memory` · esperado en: ['resolve_memory', 'resolve_local', 'route_single'] · latency: 0.16 ms
- ✅ `M03_chao` — despedida — fast-path
  - final: `resolve_memory` · esperado en: ['resolve_memory', 'resolve_local', 'route_single'] · latency: 0.11 ms
- ✅ `M04_identity_who` — consulta identidad → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 0.14 ms
- ✅ `M05_identity_name` — pregunta por nombre → resolve_identity
  - final: `resolve_identity` · esperado en: ['resolve_identity'] · latency: 0.15 ms
- ✅ `M06_place_restaurant` — place search → Google Places
  - final: `resolve_places` · esperado en: ['resolve_places'] · latency: 0.08 ms
- ✅ `M07_place_pharmacy` — place search variante
  - final: `resolve_places` · esperado en: ['resolve_online', 'resolve_places'] · latency: 0.12 ms
- ✅ `M08_market_btc` — consulta de mercado → resolve_market
  - final: `resolve_market` · esperado en: ['resolve_market'] · latency: 6.94 ms
- ✅ `M09_market_trend` — mercado general
  - final: `resolve_market` · esperado en: ['resolve_online', 'resolve_market'] · latency: 0.19 ms
- ✅ `M10_memory_what_did_i` — memoria local
  - final: `resolve_local` · esperado en: ['resolve_local'] · latency: 0.19 ms
- ✅ `M11_memory_recuerdas` — memoria — verbo recordar
  - final: `resolve_local` · esperado en: ['resolve_local'] · latency: 0.20 ms
- ✅ `M12_online_definition` — pregunta factual
  - final: `resolve_online` · esperado en: ['route_cognitive', 'resolve_online', 'route_single'] · latency: 0.17 ms
- ✅ `M13_online_history` — factual histórico
  - final: `resolve_online` · esperado en: ['resolve_online', 'route_single'] · latency: 0.17 ms
- ❌ `M14_cognitive_strategy` — razonamiento profundo
  - final: `resolve_memory` · esperado en: ['route_cognitive', 'route_multi', 'route_single'] · latency: 0.22 ms
- ✅ `M15_cognitive_analyze` — análisis profundo
  - final: `resolve_online` · esperado en: ['route_cognitive', 'resolve_online', 'route_multi', 'route_single'] · latency: 0.19 ms
- ✅ `M16_command_ai` — comando /ai
  - final: `route_single` · esperado en: ['route_single'] · latency: 0.04 ms
- ✅ `M17_command_multi` — comando /multi
  - final: `route_multi` · esperado en: ['route_multi'] · latency: 0.04 ms
- ✅ `M18_command_help` — comando /help
  - final: `execute_command` · esperado en: ['execute_command'] · latency: 0.03 ms
- ✅ `M19_memory_note` — nota personal — ingestar
  - final: `resolve_memory` · esperado en: ['resolve_memory', 'resolve_local'] · latency: 0.21 ms
- ✅ `M20_memory_save` — comando guardar
  - final: `resolve_memory` · esperado en: ['resolve_memory'] · latency: 0.18 ms
- ❌ `M21_creator_status` — creator status
  - final: `resolve_memory` · esperado en: ['route_cognitive', 'route_multi', 'resolve_local', 'resolve_online', 'route_single'] · latency: 0.18 ms
- ✅ `M22_creator_orden` — creator orden cognitiva
  - final: `resolve_online` · esperado en: ['route_cognitive', 'resolve_online', 'resolve_local', 'route_single'] · latency: 0.17 ms
- ❌ `M23_sensitive_trading` — sensitivo trading — debe escalar multi/cognitive
  - final: `resolve_market` · esperado en: ['route_cognitive', 'resolve_online', 'route_multi'] · latency: 0.22 ms
- ✅ `M24_sensitive_health` — sensitivo salud
  - final: `resolve_online` · esperado en: ['route_cognitive', 'resolve_online', 'route_multi'] · latency: 0.19 ms
- ✅ `M25_ambig_short` — confirmación corta
  - final: `resolve_memory` · esperado en: ['resolve_memory', 'resolve_local', 'route_single'] · latency: 0.10 ms
- ❌ `M26_ambig_typo` — identidad con typo
  - final: `resolve_memory` · esperado en: ['resolve_local', 'resolve_identity', 'route_single'] · latency: 0.13 ms
- ✅ `M27_empty_ish` — señal vacía
  - final: `resolve_memory` · esperado en: ['resolve_memory', 'resolve_local', 'route_single'] · latency: 0.10 ms
- ✅ `M28_english_factual` — EN factual
  - final: `resolve_online` · esperado en: ['resolve_online', 'route_single'] · latency: 0.33 ms
- ✅ `M29_english_memory` — EN memoria local
  - final: `resolve_local` · esperado en: ['resolve_local'] · latency: 0.16 ms
- ✅ `M30_opportunity_signal` — mensaje con señal de oportunidad
  - final: `resolve_memory` · esperado en: ['resolve_memory', 'resolve_local', 'resolve_online', 'route_single'] · latency: 0.20 ms

## Ajustes priorizados sugeridos
- P1 — Investigar 4 casos con strategy diferente a la esperada (revisar `classify_intent` y `select_strategy`).
- P0 — 1 identidades se resolvieron a LLM en lugar de memoria. Posible fuga de privacidad. Bloquear hard en `classify_intent`.
