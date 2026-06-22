# Vectrax — Deploy + Cutover a Webhook de Telegram
**Fecha:** 2026-06-22
**Responsable:** Mario Bravo Castro (creator)
**Servidor:** Vultr `140.82.28.181` — `/opt/vectrax`

---

## 1. Contexto

Desde hacía ~1 semana el `telegram_gateway` sufría incidentes recurrentes de
`heartbeat stale` (el supervisor lo mataba y reiniciaba), y Vectrax "se demoraba
a veces" en responder.

Causa raíz del heartbeat: el ingreso por **long-poll** abría un
`multiprocessing.Process` + `mp.Queue` NUEVOS en CADA poll (~30s), encima de 4
mecanismos solapados (SIGALRM, TCP probe, watchdog `os._exit`, refresh de cliente).
Ese churn de subprocesos/semaforos/fds terminaba congelando el propio heartbeat
que pretendía proteger. El diagnóstico del WorkerBlackBox además etiquetaba todo
como `timeout_externo` por un falso positivo (el log del gateway siempre menciona
"telegram").

La solución durable: **migrar el ingreso a webhook** (ya estaba implementado pero
dormido detrás de `USE_WEBHOOK`), eliminando por construcción el loop de polling.

## 2. Qué se liberó a producción

Dos PRs mergeadas a `main` y desplegadas:

- **PR #21** (`fix(resilience)`): diagnóstico honesto del worker (fin del falso
  `timeout_externo`), webhook durable detrás de flag, indicador `typing` y
  timeouts del `ExternalCallGuard` configurables por env.
- **PR #20** (`arch`): suite hermética verde, capa de orquestación de motores
  (`/v1/engines`), visibilidad de motores/brokers en el dashboard + `self_context`,
  y el fix de `language_gate` (desambiguación ES/IT que evitaba traducir y
  corromper respuestas correctas en español).

`main` quedó en el merge `d3e7f16`. Suite completa: **2879 passed, 1 skipped, 0 failed**.

## 3. Acciones ejecutadas

### 3.1 Deploy de código
```
bash deploy_vultr.sh
[0/4] snapshot git
[1/4] rsync local main → root@140.82.28.181:/opt/vectrax  (excluye .env, vault/, data/, logs/)
[2/4] docker presente
[3/4] docker compose build + up -d
[4/4] vectrax-core Up (healthy)
```

### 3.2 Cutover a webhook (config en servidor, NO en repo)
El `.env` del servidor se gestiona manualmente y NO se sincroniza por rsync.
Se hizo backup (`/opt/vectrax/.env.bak.<ts>`) antes de tocarlo.

```
USE_WEBHOOK=1
WEBHOOK_BASE_URL=https://api.vectrax.app
TELEGRAM_WEBHOOK_SECRET=<reusado, ya existía fuerte (54 chars) — no se tocó>
```

```
docker compose up -d --force-recreate vectrax      # recrea para leer env_file
docker compose exec -T vectrax python scripts/set_webhook.py   # registra webhook + verifica
```

`up -d --force-recreate` es obligatorio (no `restart`): compose lee `env_file`
en creación del contenedor, no en un restart simple.

## 4. Verificación

| Check | Resultado |
|-------|-----------|
| `/v1/webhook/telegram/status` | `{"state":"ready","mode":"webhook","workers":6}` |
| Supervisor | `skipping long-poll telegram_gateway` + `core_api marcado required` |
| Servicios activos | pipeline_worker, core_api, meta_loop, audit_cron (sin gateway long-poll) |
| `getWebhookInfo` (Telegram) | `url` set, `pending=0`, `last_error=(none)`, `max_conn=40` |
| Ruta pública (secreto incorrecto) | `POST https://api.vectrax.app/v1/webhook/telegram/<wrong>` → **403** |
| Contenedor | `vectrax-core` Up (healthy) |
| `worker_heartbeat` | 4.6s–7.1s (umbral 30s) |
| Errores en ~16 min | 0 (`ERROR`/`CRITICAL`/`409`/`stale`/`WATCHDOG`) |

### Prueba de extremo a extremo (mensaje real)
`gateway.log`:
```
2026-06-22 00:54:54 [INFO] vectrax.telegram_gateway — RECV 2030762343 | Hola vectrax
2026-06-22 00:54:54 [INFO] vectrax.telegram_gateway — QUEUED 2030762343 | p=0 | Hola vectrax → 6e03bc1a3f85
```
Contador del endpoint: `processed: 0 → 1`, `offset` avanzado. Mensaje recibido
por webhook, encolado y procesado sin errores.

## 5. Estado final

```
Ingreso:        WEBHOOK (long-poll apagado)
Webhook status: ready / mode=webhook
Telegram:       url set, pending=0, last_error=none
Contenedor:     vectrax-core Up (healthy), restart=unless-stopped
Worker:         heartbeat fresco (<10s)
main:           d3e7f16 (PR #20 + #21 mergeadas)
Suite:          2879 passed, 1 skipped, 0 failed
Errores prod:   0 en ~16 min de monitoreo
```

## 6. Rollback (segundos)

```bash
ssh root@140.82.28.181 'cd /opt/vectrax && \
  docker compose exec -T vectrax python scripts/remove_webhook.py && \
  sed -i "s/^USE_WEBHOOK=.*/USE_WEBHOOK=0/" .env && \
  docker compose up -d --force-recreate vectrax'
```
Vuelve al long-poll. El `.env` del servidor tiene backup `.env.bak.<ts>`.

## 7. Cierre

**Ticket:** Migración de ingreso Telegram a webhook (fin de incidentes heartbeat stale)
**Estado:** ✅ CERRADO — validado en producción con tráfico real
**Pendiente operativo:** ninguno. (CI: `.github/workflows/ci.yml` queda fuera del
repo hasta tener el scope `workflow` en el token; activar vía UI de GitHub.)

El ingreso por webhook está activo, estable y verificado. Se elimina el loop de
long-poll que causaba los incidentes de heartbeat de la última semana.

---

*Vectrax — Núcleo Cognitivo. Creado por Mario Bravo Castro.*
