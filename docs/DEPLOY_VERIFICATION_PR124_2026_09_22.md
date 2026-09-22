# Vectrax — Verificación de Merge, Despliegue y Validación en Vivo (PR #124)
**Fecha:** 2026-09-22
**Responsable:** Mario Bravo Castro (creator), ejecutado vía agente Warp
**Host:** Mac local — `/Users/mariobravo/Vectrax` (`launchd` — `com.vectrax.supervisor`)

---

## 1. Contexto

PR #124 — *"fix: corrección de permisos, evidencia interna e higiene de pruebas (PR 1)"*
(rama `claude/pr1-permissions-evidence-test-isolation`, 7 commits, 9 archivos, +1805/−50):

- Reemplaza el permiso inexistente `core.write` por `apply_proposal` en las rutas
  `services/core/routes/gravitational.py` e `ideas.py`.
- Corrige el orden de selección del diagnóstico más reciente (por fecha real,
  no por nombre de archivo) y el conteo de patrones de dominio (clave real
  `patterns`, no la inexistente `total_patterns`) en `core/nucleus/internal_evidence.py`.
- Añade `core/learn/learned_rules.py` (aislamiento de reglas aprendidas del vault vivo)
  y 5 archivos de test nuevos.

Este documento cierra el ciclo: merge → despliegue → validación en vivo.

---

## 2. Merge

| Campo | Valor |
|---|---|
| SHA `main` antes | `5f7a5d7862c409c86505877df94beac962b09661` |
| SHA `main` después | `42720ce69fb7e3c0731073c0e0ab6fc1b6ae4b17` |
| SHA de merge | `42720ce69fb7e3c0731073c0e0ab6fc1b6ae4b17` |
| Padres del merge | `5f7a5d786...` (main previo) + `2548cde92a9eb4fdd2e98a101958ef7403df8657` (head PR) |
| Método | `gh pr merge 124 --merge` (merge commit, sin squash) |
| CI pre-merge | `quality-gate`: SUCCESS · `mergeStateStatus`: CLEAN |

Confirmado: `2548cde` y los 6 commits restantes del PR (`bd1b227`, `69bc665`, `77dc5db`,
`e208cf1`, `b80501f`, `c916f30`) son ancestros de `main` (`git merge-base --is-ancestor`).

---

## 3. Despliegue

**Mecanismo (único, documentado en el propio repo, no improvisado):**
launchd `com.vectrax.supervisor` (`KeepAlive=true`) → `scripts/vectrax_boot.sh` →
`vectrax_supervisor.py`, que supervisa 5 procesos hijos.

Comando: `launchctl kickstart -k gui/501/com.vectrax.supervisor` — **18:43:09 UTC**.
Apagado ordenado (`stop_all()`) + relanzamiento automático por `KeepAlive`.

| Servicio | PID antes | PID después |
|---|---|---|
| supervisor | 5970 | 45159 |
| telegram_gateway | 6036 | 45209 |
| pipeline_worker | 26892 | 45210 |
| core_api (uvicorn :8900) | 6038 | 45211 |
| meta_loop | 6039 | 45213 |
| audit_cron | 6040 | 45214 |

Arranque limpio 18:43:14 UTC, `restart #0` en los 5 servicios (sin ciclo de
reinicio). `GET /health` → `200 {"status":"ok","components":{"api":"ok",
"database":"ok","governor":"act"}}` verificado dos veces (t+15s, t+35s).
Auditoría diaria de arranque: **ÓPTIMO** (9 checks, 0.7s).
No se tocaron variables de entorno, credenciales ni configuración. Docker no
se usó (daemon caído, servicios corren nativos).

---

## 4. Preservación del estado preexistente

`vault/learned_rules.jsonl` ya estaba modificado en el working tree **antes**
del merge (estado preexistente, no introducido por esta operación).

| Checkpoint | HEAD | `git status --short` | SHA-256 `learned_rules.jsonl` |
|---|---|---|---|
| Pre-deploy | `42720ce...` | ` M vault/learned_rules.jsonl` | `8818a1491e4d5fdc5a0ff1c4056f4e9d33ab0012267326e8c006bd59c0608296` |
| Post-restart | `42720ce...` | ` M vault/learned_rules.jsonl` | idéntico |
| Post-pruebas HTTP | `42720ce...` | ` M vault/learned_rules.jsonl` | idéntico |
| Post-conversación | `42720ce...` | ` M vault/learned_rules.jsonl` | idéntico |
| Post-evidencia | `42720ce...` | ` M vault/learned_rules.jsonl` | idéntico |

No se restauró, no se editó, no se agregó a Git. `git diff` idéntico byte a
byte en todos los checkpoints.

---

## 5. Autorización en vivo

Endpoint: `POST /v1/ideas/{id}/approve` en `http://127.0.0.1:8900`
(idea inexistente `IDEA-PR124-NONEXISTENT-20260922`, no escribe en
`data/ideas.jsonl` — `IdeaStore.approve()` retorna antes de `_append()`).

| Caso | Resultado | Coincide con lo esperado |
|---|---|---|
| Sin cabecera Authorization | `401 Missing or invalid Authorization header` | ✅ |
| Token inválido | `403 Invalid or expired API token` | ✅ (no cuenta como validación RBAC) |
| owner / operator / viewer con token válido | **NO VALIDABLE EN VIVO** | tokens solo hasheados (SHA-256); texto plano no recuperable; `VX_API_TOKEN` no configurado; 14 tokens `owner` activos y 4 `operator` (0 activos), 0 `viewer` — creación de tokens nuevos prohibida explícitamente |

`data/ideas.jsonl` (hash `ce2c4b2c53a3e6d78dce062d9f37231bbb55b2de8e94ad9d04c252b8bf66efa6`)
y `git status --short data/ vault/` sin cambios nuevos causados por las pruebas.

---

## 6. Comprobación conversacional (como owner)

Vía el contrato único documentado `NucleusAuthority.resolve(text, channel="creator",
owner="mario", source="api")` (`core/nucleus/nucleus_authority.py`) — el mismo
código que usa el adaptador web tras autenticar.

**Pregunta:** *"¿Qué sucede después de aprobar una propuesta?"*

Resuelta por el override `internal_evidence` (familia `approval_pipeline`,
`confidence=0.95`, determinista, sin LLM):

```
- [relacion] circuitos INDEPENDIENTES: no se llaman entre sí ni comparten almacén;
  aprobar una idea no dispara el endpoint de proposals · estado: independent
- [ideas/1.endpoint] POST /v1/ideas/{id}/approve (permiso apply_proposal) · estado: present
- [ideas/2.persistencia] IdeaStore.approve() -> status=approved en data/ideas.jsonl · estado: present
- [ideas/3.auditoria] NO escribe en audit_ledger · estado: absent
- [ideas/4.ejecutor] AUSENTE: ningún proceso consume el estado approved
  (IdeaStore.mark_applied() no tiene llamador en producción) · estado: absent
- [proposals/1.endpoint] POST /v1/proposals/{id}/approve (permiso apply_proposal) · estado: present
- [proposals/2.persistencia] db.update_proposal_status() -> vectrax.db · estado: present
- [proposals/3.auditoria] escribe entrada en audit_ledger (best-effort) · estado: present
- [proposals/4.ejecutor] NO VERIFICADO por esta traza (sin punto de aplicación único y nombrado)
Fuente: services/core/routes/{ideas,proposals}.py + core/idea_store.py (traza estática).
```

Criterios verificados: menciona `apply_proposal` ✅ · no menciona `core.write` ✅ ·
distingue ideas/proposals como circuitos independientes ✅ · no equipara aprobar
con ejecutar ✅ · explica que `mark_applied()` no tiene consumidor de producción ✅.

---

## 7. Evidencia interna y patrones

Consultado vía `InternalEvidence` (fachada de solo lectura, `core/nucleus/internal_evidence.py`):

- **Diagnóstico más reciente:** `audit_daily_2026-09-22_184345.json`, modo `daily`,
  seleccionado por fecha real (corrección de PR #124, ya no por nombre de archivo),
  antigüedad 290.6s, estado `ok`, "sin problemas detectados".
- **Freight (`freight_logistics`)**, clave real `patterns`: **733 patrones abstractos**
  (`strong_patterns=551`, `avg_win_rate=90.1%`, `total_observations=1,226,277`,
  `max_contributing_tenants=109`), fuente `core.domain_knowledge.get_domain_summary`.

Ambas lecturas provienen de datos reales en disco; ninguna afirmación de
aprendizaje fue fabricada sin respaldo verificable.

---

## 8. Desviaciones observadas

1. Casos HTTP owner/operator/viewer no validables en vivo (limitación estructural
   de almacenamiento de tokens + restricción de no crear tokens nuevos).
2. ~140 archivos temporales huérfanos `freight_logistics.json.tmp.<pid>` en
   `~/.vectrax/domain_library/` (escrituras atómicas interrumpidas de sesiones
   previas) — observación colateral, no relacionada con el merge, no corregida
   en esta operación.
3. Warning benigno de arranque (`vectrax_boot.sh`: `.venv/python3` apunta a ruta
   externa, cae correctamente al Python del sistema) — preexistente.

---

## 9. Estado final confirmado

```
main HEAD:            42720ce69fb7e3c0731073c0e0ab6fc1b6ae4b17
Servicios:             5/5 up, restart #0, sin ciclos de reinicio
Health check:          200 ok (api/database/governor)
learned_rules.jsonl:   sin cambios (hash idéntico en 5 checkpoints)
data/ideas.jsonl:      sin cambios
Auth (401/403 inválido): correctos
Auth (RBAC owner/op/viewer): NO VALIDABLE EN VIVO (sin tokens en texto plano)
Conversación owner:    cumple los 5 criterios exigidos
Diagnóstico:           audit_daily_2026-09-22_184345.json (ok, 290.6s)
Freight patterns:      733 (fuente real, clave contractual `patterns`)
```

No se implementó el puente causal, no se crearon entitlements, no se
conectaron dominios nuevos y no se inició otra etapa.

---

*Vectrax — Núcleo Cognitivo. Creado por Mario Bravo Castro.*
