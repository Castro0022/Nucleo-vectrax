# Dominio `cybersecurity` (NVD + CISA KEV)
Documento vivo del dominio de ciberseguridad de VECTRAX. Isomórfico a
`market`/`freight_logistics`/`florida_real_estate`: cada `subject` se materializa
como estrella gravitacional con masa por observaciones y el Motor de Criterio
opina desde lo aprendido.
Creador: Mario Bravo Castro · Fecha: 2026-08-02

## Contrato (auditado y aprobado)
- **entity** = CVE individual (NVD).
- **subject** jerárquico (escalera específico→general): `product_family` → `vulnerability_class` → `attack_vector` → `severity_tier`. El criterio usa el nivel más específico con masa suficiente (fallback por masa).
- **outcome** = WIN | LOSS | PENDING | NEUTRAL.
- **timing** = EARLY | LATE | NONE.
- `days_to_kev` obligatorio = `cisaExploitAdd − published` (clamp a 0).

### Decisiones del creador
1. Confirmación en CISA KEV ⇒ `outcome = WIN` **SIEMPRE** (nunca LOSS). `timing`:
   - 0–30 días → EARLY
   - 31–180 días → LATE
   - >180 días → NONE
   El timing mide **capacidad predictiva**, no el hecho de explotación.
2. No en KEV y edad >180d → LOSS. No en KEV y edad ≤180d → PENDING. Sin ninguna dimensión → NEUTRAL (no decisivo).
3. **Alcance histórico inicial**: NVD desde **2020-01-01** hasta hoy; catálogo KEV completo conservado para reconciliación; CVE publicadas **<2020 se excluyen** (ni estrellas ni aprendizaje). 2016–2019 queda para expansión posterior.

## Fuentes (cifras Fase 0, ~ago-2026)
- **NVD API 2.0** (`services.nvd.nist.gov/rest/json/cves/2.0`): corpus ~372,5k CVE; paginación `startIndex`/`resultsPerPage=2000`; ventanas `pubStartDate/pubEndDate` ≤120 días; `NVD_API_KEY` (50/30s) recomendada; el registro embebe `cisaExploitAdd` (fecha KEV).
- **CISA KEV** (JSON oficial): ~1.6k entradas; campos `cveID, dateAdded, knownRansomwareCampaignUse, vendorProject, product`.
- Cambio NVD 15-abr-2026: solo enriquece KEV/federal/crítico. Implicación: la clase WIN (KEV) siempre trae dimensiones limpias; la LOSS/PENDING (no-KEV reciente) pierde dimensiones → el fallback jerárquico + métricas del CNA lo mitigan.

## Arquitectura (archivos)
- `connectors/cybersecurity/base.py` — `DOMAIN`, `CVEEvent`, `CyberFeedProvider`.
- `connectors/cybersecurity/dimensions.py` — extractores de dims, `subject_ladder`, `gravity_events` (paridad de fingerprint), `coverage`.
- `connectors/cybersecurity/nvd_provider.py` — `NvdClient` (paginación/ventanas/backoff), `normalize_cve`, `date_windows`, `NvdProvider`.
- `connectors/cybersecurity/kev_client.py` — descarga/snapshot KEV + `build_kev_map`.
- `connectors/cybersecurity/cve_outcome_adapter.py` — `CyberOutcomeAdapter` (WIN/timing/LOSS/PENDING/NEUTRAL).
- `connectors/cybersecurity/verification_cycle.py` — `verify_events` (registro por nivel, idempotente + masa bulk), `verified_subjects`/`verified_score` (dedupe-at-read), `resolve_best_subject` (fallback jerárquico).
- `connectors/cybersecurity/seen_ledger.py` — SQLite por `cve_id` (idempotencia + checkpoints).
- `connectors/cybersecurity/backfill.py` — orquestador reanudable/idempotente + `--dry-run`.
- `connectors/cybersecurity/learning_cycle.py` — incremental diario (GATED, default OFF).
- `connectors/cybersecurity/simulator_adapter.py` — proveedor hermético (tests/piloto).
- `config/domain_templates/cybersecurity.json` — un `event_type` por nivel (`signature_fields` de un dim → cardinalidad acotada).
- `config/cyber/product_family_map.json` — mapa curado vendor/producto→familia.
- `core/learn/criterion.py` — vocab `cybersecurity` + evidencia desde `verification_ledger` (genérico).
- `services/ui/static/universe.html` — color rojo `#ef4444` + filtro/leyenda "Ciberseguridad".
- `core/transport/pipeline_worker.py` — bloque de ciclo cyber **GATED** (`CYBER_LEARN_ENABLED`, default OFF).

## Estrellas y cardinalidad
Fingerprint por nivel: `cybersecurity:cve_<level>:<field>=<valor>` (idéntico en backfill bulk e incremental `ingest_event`). tiers=4, vectores=4, clases ≲ decenas. **OBSERVADO a escala completa (2020→hoy)**: `product_family` NO quedó acotado — el fallback a `vendor:product` crudo generó decenas de miles de familias distintas → total real **29,899 estrellas** (la cota de diseño de ~5.000 NO se cumplió). Ver §Despliegue → Hallazgos abiertos.

## Idempotencia (seen-ledger)
- `upsert(cve) → (is_new, is_changed)` por `content_hash`.
- Masa: se incrementa **solo** en la primera materialización de la CVE (is_new).
- Outcomes decisivos: se escriben por `(cve_id, nivel)` con `prediction_id="{cve}|{level}"` solo cuando `is_new` o `is_changed`.
- PENDING vive solo en el seen-ledger (nunca en el `verification_ledger`).
- Flip LOSS→WIN (CVE vieja que entra a KEV): se escribe WIN superseding; la lectura deduplica por `prediction_id` (latest `resolved_ts`).
- Re-ejecutar el backfill con los mismos datos ⇒ 0 líneas nuevas y 0 masa.

## Operación
### Variables de entorno
- `NVD_API_KEY` — opcional; sube el rate limit (recomendada para el backfill).
- `CYBER_FEED_PROVIDER` — `nvd` | `simulator` (default: simulator).
- `CYBER_LEARN_ENABLED` — `1`|`0` (default: **0**, worker apagado).
- `CYBER_ALLOW_SIMULATOR` — `1`|`0` (default: **0**). Guard de seguridad: el worker solo corre el ciclo si el proveedor es **real** (`CYBER_FEED_PROVIDER=nvd`) o si se permite el simulador explícitamente. Evita sembrar datos sintéticos en el universo de producción.
- `CYBER_EVENTS_PER_CYCLE`, `CYBER_VERIFY_ENABLED`, `CYBER_INCREMENTAL_DAYS`, `NVD_REQUEST_DELAY`, `CYBER_SEEN_DB`.

### Comandos
Dry-run (no escribe; reporta cobertura y outcomes):
```
python -m connectors.cybersecurity.backfill --dry-run --since 2021-01-01 --until 2021-05-01
```
Backfill real 2020→hoy (deliberado; escribe gravity + verification_ledger + seen-ledger):
```
NVD_API_KEY=… python -m connectors.cybersecurity.backfill
```
Incremental diario (manual, mientras el worker está apagado):
```
CYBER_LEARN_ENABLED=1 CYBER_FEED_PROVIDER=nvd python -c "from connectors.cybersecurity.learning_cycle import run_learning_cycle as r; print(r())"
```

## Validación / criterios de aceptación
- Suite `tests/test_cybersecurity_domain.py` verde (hermética, sin red). Cubre dims, escalera, KEV/NVD (mock), outcome/timing, KEV-nunca-LOSS, exclusión <2020, idempotencia, masa acotada, dry-run, detección de dominio, criterio grounded, fallback jerárquico y freno del worker.
- Backfill idempotente (re-run = 0 nuevas) y masa acotada verificados en test.
- Worker **habilitado en producción** el 2026-08-03 (`CYBER_LEARN_ENABLED=1`, `CYBER_FEED_PROVIDER=nvd`, guard anti-simulador activo). Backfill 2020→hoy ejecutado y verificado (ver §Despliegue).

## Despliegue (rollout) — registro 2026-08-03
Rollout incremental y verificable (gate OFF por defecto en cada paso):
1. **Construcción gated**: `CYBER_LEARN_ENABLED=0`; el worker no corre sin proveedor real (guard anti-simulador).
2. **Tests herméticos**: `tests/test_cybersecurity_domain.py` (34) verdes; sin regresión en `tests/test_criterion.py` (37).
3. **Dry-run** contra NVD+KEV real (sin escrituras) para medir cobertura/outcomes.
4. **Habilitación del worker**: `.env` → `CYBER_FEED_PROVIDER=nvd` + `CYBER_LEARN_ENABLED=1`; supervisor reiniciado; ciclo incremental cada 6h.
5. **Checkpoint reanudable (live)**: interrupción tras 1 ventana → reanudó desde la ventana 2 (no reinició); reproceso `--no-resume` con **0 duplicados** (seen_total/estrellas/ledger idénticos).
6. **Backfill completo 2020→hoy**: durable vía launchd one-shot instrumentado (`scripts/cyber_backfill_demo.py`).
7. **Monitoreo**: agente `com.vectrax.worker-monitor` (cada 300s) vigila el log del worker.
### Resultado del backfill (2026-08-03)
- **CVE observadas (2020→hoy): 236,494** (el corpus NVD completo es ~372,505; <2020 excluido por decisión 2).
- Outcomes: LOSS 182,825 · PENDING 40,756 · NEUTRAL 11,797 · WIN (KEV) 1,116.
- **Estrellas cognitivas: 29,899** → **ratio ≈ 7.9 CVE/estrella** (1 CVE ≠ 1 estrella: las estrellas son subjects abstractos, no CVEs).
- Rendimiento: ~37 min (2,244s), ~105 CVE/s, CPU ~47–60%, RSS pico 296 MB.
- Tamaño BD al cierre: gravity 18.9 MB · seen-ledger 36.8 MB · verification-ledger **162.6 MB**.
- Log del run: `~/.vectrax/cyber_backfill_demo.log` (`PROGRESS`/`REPORT`).
### Hallazgos abiertos (resolver antes de uso intensivo del criterio)
1. **Sobre-fragmentación de `product_family`** (29,899 > cota ~5.000): el fallback a `vendor:product` crudo crea decenas de miles de familias a escala. Mitigación: ampliar el mapa curado vendor→familia y/o agrupar los no mapeados (p. ej. descartar el nivel family cuando el vendor no está mapeado).
2. **Escala del verification-ledger (162 MB / 693k filas)**: `rank_domain_evidence`→`subject_scores` carga el JSONL completo por consulta → lento en producción. Mitigación: agregados precomputados (SQLite/resumen por subject) en vez del JSONL crudo.
### Limpieza
El agente one-shot `com.vectrax.cyber-backfill` se descargó y su plist se eliminó tras completar (no re-ejecuta en reinicios). Persisten los agentes de operación (`supervisor`, `worker-monitor`, `backup-db`, `rotate-logs`).
## Fuera de alcance (v1)
- Convergencia semántica cross-dominio por embeddings a nivel CVE.
- Rango 2016–2019 (expansión posterior).
- Fuentes extra (EPSS, ATT&CK, CVE List v5).
