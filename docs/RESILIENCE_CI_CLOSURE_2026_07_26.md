# Resiliencia y Auto-Recuperación + CI Verde — Cierre del ticket

- **Task:** "Quiero que Vectrax no falle jamás" → llevar el sistema a **auto-recuperación real** (detectar/aislar/recuperar sin intervención) y blindar contra regresiones con un quality gate.
- **Delivered:** 2026-07-26 · **Status:** ✅ Cerrado (P0–P3 implementados, desplegados y verificados en vivo; CI en verde en `main`).
- **Entorno:** local en Mac bajo `launchd` (`com.vectrax.supervisor`). No Vultr/Docker.
- **PRs:** #56 (deadlock), #57 (watchdog de progreso + auto-auditor), #58 (activar CI), #59 (deps + tests de auth), #60 (aiosqlite + phase3 live + base dir + Router).
- **Plan:** `9d154b9f` (documento de plan "Vectrax: Resiliencia y Auto-Recuperación").

## Marco honesto
Cero fallos absolutos no existe. El objetivo alcanzable es **degradación elegante + auto-recuperación**: cuando algo falla, se detecta y se recupera solo. Ante caídas externas (red, Google, Telegram, OpenAI) el sistema degrada sin caerse; no hay inmunidad total.

## P0 — Deadlock del poll de Telegram (PR #56)
El poll corría `getUpdates` en un subproceso y devolvía el resultado por `multiprocessing.Queue`, pero el padre hacía `join` ANTES de drenar. Con backlog grande el *feeder* del hijo se bloqueaba (payload > buffer del socketpair) → el hijo nunca salía → matado a los ~40s → el offset nunca avanzaba → **deadlock auto-perpetuante** (bot muerto ~20h; 148 updates sin consumir).
Fix: `_poll_subprocess` acepta `limit` (`POLL_UPDATE_LIMIT=25`); el bucle lee `_result_q.get(timeout)` PRIMERO (desbloquea el feeder) y solo después hace `join`. Repro bajo `spawn`: patrón viejo con 2.4 MB → deadlock; nuevo → drena 300 updates en 0.06 s. En vivo: `pending_update_count` 148 → 0.

## P1 — Watchdog de PROGRESO del gateway (PR #57)
El watchdog previo vigilaba `_last_poll_ok`, que el kill-path refresca cada ~40 s → **nunca** detectó el deadlock (proceso vivo, 0 progreso, 20 h). El hueco: se vigilaba *liveness*, no *progreso*.
Fix: nuevo watchdog en `_heartbeat_loop` sobre `self._polls` (solo incrementa en poll exitoso). Sin avance en `POLL_PROGRESS_STALL_THRESHOLD` (180 s) → `os._exit(1)` para reinicio limpio del supervisor. `NET_DOWN` cuenta como progreso (no reinicia en outage de red real).

## P2 — Self-Audit Engine revivido en macOS (PR #57)
`audit_cron` estaba definido como `["cron", "-f"]` (cron del sistema) → SIGKILL/`exit=-9` en macOS → el auditor **nunca corría**.
Fix: `audit_engine` con `check_container`/`check_processes` cross-platform (fallback `pgrep`/`ps` cuando no hay `/proc`); nuevo modo `audit_cron --loop` (scheduler Python defensivo + `load_dotenv`); el supervisor lanza `python -m observability.audit_cron --loop`. Verificado en vivo: `startup daily audit: ESTABLE`, proceso estable sin `exit=-9`.

## P3 — Quality gate (CI) activado y en verde (PRs #58–#60)
`.github/workflows/ci.yml` corre la suite hermética (`pytest -m "not live"`, Python 3.9) en cada push a `main` y en cada PR. Requirió otorgar el scope `workflow` al token de `gh`.
Para llevarlo a verde:
- **Deps reales no declaradas** → agregadas a `pyproject.toml`/`setup.py`: `sqlalchemy`, `python-multipart`, `aiosqlite` (vivían solo en `requirements_new.txt`; `pip install -e .` en CI no las traía → errores de colección).
- **Tests de auth** (`test_agent_register`, `test_propose_remote`): usaban `settings.api_token` (ahora `""` tras el hardening) → 401. Ahora fijan un `VX_API_TOKEN` fuerte vía `monkeypatch`. `test_legacy_token_fallback` reescrito: acepta un token fuerte como owner y **rechaza** el default prohibido (guard de regresión). Sin reabrir el backdoor.
- **`test_phase3`** requiere Ollama → `pytestmark = pytest.mark.live` (excluido del gate; sigue ejecutable con `-m live`).
- **`BASE_DIR` hardcodeado** (`~/Vectrax`) en `code_reader.py`, `integrator.py`, `sandbox_validator.py` → en CI/Docker el checkout no vive ahí → 0 módulos / no-conflict. Ahora derivan del `__file__` (repo root) con override `VECTRAX_DIR`. Fix de robustez real.
- **Bloque Router** de `_collect_module_state` se omitía sin datos en el ledger → ahora emite defaults para estar siempre presente (consistente con Observer/Learner/Governor).

Resultado en `main`: **3092 passed, 1 skipped, 6 deselected (`live`), 0 failed**.

## Despliegue + verificación en vivo
Reinicio del stack vía `launchctl kickstart -k gui/$(id -u)/com.vectrax.supervisor`. Post-deploy: 6 procesos arriba (incluido `audit_cron --loop`), gateway heartbeat fresco y `polls` avanzando (0 `POLL_SUBPROCESS_KILL` en bucle), API `:8900` `status ok` / governor `act`, `pending_update_count=0`.

## Límites conocidos / follow-ups (no bloqueantes)
- No hay garantía de 0 fallos ante causas externas (red/Google/Telegram/OpenAI/disco). Objetivo: degradación + auto-recuperación.
- Varias deps reales viven solo en `requirements_new.txt`; conviene reconciliar `requirements*.txt` con `pyproject.toml` en una tanda futura.
- Google Places sigue con 403 (credencial externa, pendiente de una key `AIza…` válida) — ajeno a este ticket.
- El token del bot se loguea en claro en `~/.vectrax/*.log` (httpx) — recomendado rotar + subir el logger a WARNING.

## Estado
**Cerrado.** Auto-recuperación implementada y verificada; el hueco que causó el outage de 20 h (liveness ≠ progreso) queda cubierto por el watchdog de progreso; el auto-auditor vuelve a correr en macOS; el quality gate protege cada push/PR y está en verde.

## Referencias
- Código: `vectrax/telegram_gateway.py`, `vectrax_supervisor.py`, `observability/audit_cron.py`, `observability/audit_engine.py`, `core/operator/builder/{code_reader,integrator,sandbox_validator}.py`, `core/self_observation/self_summary.py`.
- Deps/tests: `pyproject.toml`, `setup.py`, `tests/{test_agent_register,test_propose_remote,test_multiuser,test_phase3}.py`.
- CI: `.github/workflows/ci.yml`.
- PRs: #56, #57, #58, #59, #60. `main` @ `901bfe3` (+ merge del doc).
