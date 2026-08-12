# Handoff para el próximo ciclo — 2026-08-12

Resumen de cierre del ciclo de "autoconocimiento con procedencia" y punto de partida
para el siguiente. Reconstruido desde el estado real del repo y del runtime.

## Estado actual
- Rama: `main` sincronizada con `origin/main`. HEAD `9d31d06`.
- Producción: **local** (Mac, launchd `com.vectrax.supervisor` → API `:8900` + `telegram_gateway` + `pipeline_worker`). Sana: heartbeats frescos, `/health` y `/v1/census` → 200. Vultr fue retirado (ya no existe).
- Árbol limpio salvo `vault/cyber_kev/` (datos, sin trackear).

## Entregado este ciclo (PRs #87–#90, todas en `main`)
- **#87 — Conciencia operativa con procedencia.** Nuevos módulos:
  - `core/self_observation/self_knowledge.py`: `get_origin()` (nacimiento institucional canónico 2026-04-07 vs primera huella/gestación derivada de datos), `get_milestones()`, `trace_provenance()` (liga tema→evidencia).
  - `core/operator/certainty.py`: declaración honesta de certeza (salvedad en rutas generativas de baja confianza; nunca grounded; nunca bloquea).
  - `core/operator/external_learning.py`: resultado externo → observación `source=external` + señal al `learning_cycle` (promoción solo CONFIRMED + `MIN_CONFIDENCE_FOR_INTEGRATION=0.60` + Decision Authority).
  - `core/operator/external_gateway.py`: `GatewayResult` expone `resolve_mode`/`confidence`/`evidence['layer']`; confianza real del SmartRouter al ciclo `op_cycles` (auditoría) y a la respuesta, sin releer el ledger.
- **#88 — Producción local.** `self_context` sin claim de Vultr ("infraestructura local"); eliminado `deploy_vultr.sh`.
- **#89 — Fix detección origen/identidad.** `_ORIGIN_RE` amplía tuteo+voseo, pronombre intermedio (`¿desde cuándo tú/vos existís?`) e identidad en 2ª persona (`¿quién/qué eres/sos?`), sin capturar `¿quién soy?` (usuario).
- **#90 — CHANGELOG** con la entrada `2026-08-08`.
- Tests: 53/53 verdes en los módulos tocados. Verificado en vivo (origen real; confianza diferenciada en `op_cycles`).

## Backlog para el próximo ciclo (priorizado)
1. **Voz / ElevenLabs TTS** — cuota agotada (HTTP 401 `quota_exceeded`); el texto responde bien, la voz no. Opciones: recargar créditos o degradar a texto (flag de audio) cuando la cuota falle. Relacionado: issue **#73** (voz — La Presencia Fase 3), abierto a propósito.
2. **Rama `feat/cyber-backfill-instrumentation`** (remota + local, sin mergear): instrumentación de backfill empujada pero sin PR. Decidir: abrir PR + merge, o descartar.
3. **Reactivar worker de ciberseguridad** (`CYBER_LEARN_ENABLED=0` hoy) tras 2 mitigaciones: (a) cardinalidad de `product_family` (fallback `vendor:product` generó ~29.8k estrellas, sobre el límite de diseño); (b) escala del ledger de verificación (JSONL grande ralentiza `rank_domain_evidence`). `vault/cyber_kev/` proviene de aquí.
4. **Mejora de procedencia en respuesta (opcional)**: hoy `evidence['layer']` (personal/shared/external) queda en el objeto de respuesta; evaluar exponerlo en prosa cuando aporte ("lo aprendí de vos" vs "lo sé en general").

## Notas operativas
- **Deploy local** = reiniciar el supervisor para recargar código: `launchctl kickstart -k "gui/$(id -u)/com.vectrax.supervisor"`. Verificar con `/health` y comparando start-time de procesos vs merge.
- Flags relevantes (env / `.env`): `VECTRAX_CERTAINTY_FLOOR` (0.5), `VECTRAX_BIRTH_DATE`/`VECTRAX_BIRTH_ENTITY` (ancla institucional), `VX_CREATOR_ID`, `CYBER_LEARN_ENABLED` (0 = pausado).
- Ledger de auditoría por-ciclo: `vault/operational_cycles.db` (`op_cycles`). Observaciones: `vault/observation_ledger.db`.

## Ramas locales preservadas (trabajo sin mergear — revisar/limpiar el próximo ciclo)
`feat/cyber-backfill-instrumentation`, `backup/arch-squ-prerebase`, `arch/quality-observer`, `arch/test-contracts`, `arch/universe-quality`, `fix/test-suite-py39-isolation`, `fix/vault-paths-and-docker-env`, `pr/etoro-integration-2026-05-31`, `pr/prod-fixes-2026-06-02`.

## Logs archivados de esta sesión
`~/.vectrax/session_archives/2026-08-12T1851Z/` (11 logs comprimidos: supervisor, worker, gateway, daemon, monitores, boot). Los originales en `~/.vectrax/*.log` quedaron intactos (rotación semanal ya configurada).
