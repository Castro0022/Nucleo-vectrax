# Vectrax — Manual de Operaciones del Sistema

**Versión:** 1.0 · **Fecha:** 2026-07-29 · **Ámbito:** despliegue local-first en Mac
(launchd `com.vectrax.supervisor`, Core API en el puerto `8900`).

Este runbook cubre las operaciones del día a día: gestión del servicio, backups y
—lo más crítico— el **flujo de trabajo de restauración**. Para instalación ver
`docs/INSTALL.md`; para el resumen de la feature de backup ver el README
(*Backups y Rotación de Logs (Mac)*).

---

## 1. Gestión del servicio

Vectrax corre bajo un agente launchd (`RunAtLoad` + `KeepAlive`): arranca en el
boot y se reinicia solo si un servicio muere. El supervisor lanza el gateway de
Telegram, el pipeline worker, la Core API y el meta-loop.

| Acción | Comando |
|---|---|
| Estado | `launchctl list \| grep vectrax` |
| Salud API | `curl -s http://127.0.0.1:8900/health` |
| Reiniciar | `launchctl kickstart -k gui/$(id -u)/com.vectrax.supervisor` |
| Parar (no reinicia) | `launchctl bootout gui/$(id -u)/com.vectrax.supervisor` |
| Arrancar | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vectrax.supervisor.plist` |
| Logs | `~/.vectrax/*.log` |

---

## 2. Backups (contexto)

Dos agentes launchd mantienen copias durables en `~/vectrax_backups/` (la de BD,
además, espejada a iCloud). Detalle completo en el README.

| Agente | Cuándo | Qué | Script |
|---|---|---|---|
| `com.vectrax.backup-db` | diario 03:30 | BD (SQLite + ledgers) → `db/` + iCloud | `scripts/backup_db.sh` |
| `com.vectrax.rotate-logs` | semanal Dom 04:00 | logs → `logs/` | `scripts/rotate_logs.sh` |

El backup de BD usa la copia **online** de SQLite (`.backup`), consistente con WAL
aunque el sistema esté escribiendo, y escribe un `.sha256` por archivo.

---

## 3. Flujo de trabajo de restauración

> **Principio:** un backup no probado no es un backup. `scripts/restore_db.sh` es
> **no destructivo**: restaura a un directorio y verifica `sha256` +
> `integrity_check`; **nunca** toca la base viva. Aplicar una restauración sobre
> los datos vivos es un paso **manual y deliberado** (Sección 3.4), con el sistema
> parado.

### Dónde vive cada dato

| Entidad | BD viva | Tabla | Ruta dentro del backup |
|---|---|---|---|
| estrellas | `~/.vectrax/vectrax.db` | `stars` | `home_vectrax/vectrax.db` |
| convergencias | `vault/convergence_history.db` | `convergence_events` | `repo/vault/convergence_history.db` |
| op_cycles | `vault/operational_cycles.db` | `op_cycles` | `repo/vault/operational_cycles.db` |

> La BD gravitacional viva es `~/.vectrax/vectrax.db`. El `./vectrax.db` del repo
> está obsoleto; no lo uses como fuente.

### 3.1 Elegir y verificar el backup (dry-run, no toca nada)

```bash
# Listar backups disponibles (más reciente primero)
ls -1t ~/vectrax_backups/db/vectrax_db_*.tar.gz

# Restaurar el último (o un STAMP concreto) a un directorio de trabajo.
# Verifica sha256 + integrity_check de cada .db.
bash scripts/restore_db.sh latest /tmp/vx_restore
# o: bash scripts/restore_db.sh 20260729_132005 /tmp/vx_restore
```

**No continúes** si no ves `sha256: OK` y `N/N DBs íntegras`. Si el `sha256`
falla, el contenedor está corrupto → prueba con el backup anterior o con la copia
de iCloud (`~/Library/Mobile Documents/com~apple~CloudDocs/vectrax_backups/db/`).

### 3.2 Inspeccionar el contenido del snapshot

```bash
sqlite3 /tmp/vx_restore/home_vectrax/vectrax.db              "SELECT count(*) FROM stars;"
sqlite3 /tmp/vx_restore/repo/vault/convergence_history.db    "SELECT count(*) FROM convergence_events;"
sqlite3 /tmp/vx_restore/repo/vault/operational_cycles.db     "SELECT count(*) FROM op_cycles;"
```

Compara con la base viva para dimensionar la pérdida (si la hay) antes de aplicar:

```bash
sqlite3 ~/.vectrax/vectrax.db            "SELECT count(*) FROM stars;"
sqlite3 vault/convergence_history.db     "SELECT count(*) FROM convergence_events;"
sqlite3 vault/operational_cycles.db      "SELECT count(*) FROM op_cycles;"
```

Si solo querías **probar** el backup, termina aquí y borra el directorio:
`rm -rf /tmp/vx_restore`.

### 3.3 Parar el sistema (obligatorio antes de aplicar)

Aplicar con el sistema vivo mezclaría escrituras y dejaría WAL colgando. Para el
supervisor primero:

```bash
launchctl bootout gui/$(id -u)/com.vectrax.supervisor
# Verifica que no quedan procesos:
ps -Ao pid,command | grep -iE "vectrax_supervisor|uvicorn" | grep -v grep || echo "parado"
```

### 3.4 Aplicar (deliberado): respaldar la viva → copiar la restaurada

Por cada BD a restaurar: **guarda primero la viva actual** (para rollback), copia
la restaurada y **elimina los sidecars `-wal`/`-shm`** (el `.backup` produce una
BD autónoma; un WAL viejo colgando corrompería la nueva).

```bash
# Ejemplo: op_cycles
cp -p vault/operational_cycles.db vault/operational_cycles.db.pre-restore
cp    /tmp/vx_restore/repo/vault/operational_cycles.db vault/operational_cycles.db
rm -f vault/operational_cycles.db-wal vault/operational_cycles.db-shm

# Ejemplo: estrellas (BD gravitacional)
cp -p ~/.vectrax/vectrax.db ~/.vectrax/vectrax.db.pre-restore
cp    /tmp/vx_restore/home_vectrax/vectrax.db ~/.vectrax/vectrax.db
rm -f ~/.vectrax/vectrax.db-wal ~/.vectrax/vectrax.db-shm
```

### 3.5 Reiniciar y verificar

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vectrax.supervisor.plist
sleep 5
curl -s http://127.0.0.1:8900/health
# Reconteo en las bases vivas ya restauradas:
sqlite3 ~/.vectrax/vectrax.db "SELECT count(*) FROM stars;"
sqlite3 vault/operational_cycles.db "SELECT count(*) FROM op_cycles;"
```

Salud `200` + conteos esperados = restauración aplicada.

### 3.6 Rollback

Si algo va mal, para el sistema y revierte con las copias `*.pre-restore`:

```bash
launchctl bootout gui/$(id -u)/com.vectrax.supervisor
mv vault/operational_cycles.db.pre-restore vault/operational_cycles.db
mv ~/.vectrax/vectrax.db.pre-restore ~/.vectrax/vectrax.db
rm -f vault/operational_cycles.db-wal vault/operational_cycles.db-shm \
      ~/.vectrax/vectrax.db-wal ~/.vectrax/vectrax.db-shm
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vectrax.supervisor.plist
```

### 3.7 Prueba de referencia (2026-07-29)

Restauración del último backup (`vectrax_db_20260729_132005.tar.gz`) verificada
contra la base viva:
- `sha256`: **OK** · **28/28 DBs** pasan `integrity_check`.
- estrellas **776** (snapshot) vs **777** (vivo); convergencias **5056** = **5056**;
  op_cycles **952** vs **953**. Los `Δ +1` son operación normal entre el backup
  (13:20) y la verificación (~17:35): el snapshot es puntual y coherente.

---

## 4. Referencia rápida

**Scripts** (`scripts/`)

| Script | Función |
|---|---|
| `backup_db.sh` | Backup consistente de la BD → `~/vectrax_backups/db/` + iCloud + retención |
| `rotate_logs.sh` | Rotación de logs → `~/vectrax_backups/logs/` + retención + truncado seguro |
| `restore_db.sh` | Restaura un backup a un dir (sha256 + integrity_check); no destructivo |

**Variables de entorno**

| Variable | Por defecto | Efecto |
|---|---|---|
| `VX_DB_RETENTION` | `14` | Backups de BD a conservar |
| `VX_LOG_RETENTION` | `8` | Archivos de log a conservar |
| `VX_LOG_TRUNCATE` | `1` | `0` = archivar sin truncar los logs vivos |
| `VX_DB_BACKUP_DIR` | `~/vectrax_backups/db` | Origen que lee `restore_db.sh` |

---

## 5. Notas de seguridad

- `vault/`, `.env` y los `.db`/`.db-wal`/`.db-shm` son **hard limits**: nunca se
  modifican por auto-apply (ver `docs/operational_philosophy.md`). La restauración
  es siempre una operación **humana y deliberada**.
- El backup y la restauración son **no destructivos** por diseño; el único paso
  que sobrescribe datos vivos es la Sección 3.4, y va precedido de una copia
  `*.pre-restore` para rollback.
