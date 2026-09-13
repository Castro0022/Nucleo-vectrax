# Puente A — Reporte de cierre (2026-09-13)

Reporte final consolidado de las tres iteraciones de Puente A (herramienta
READ_ONLY de lectura de archivos con evidencia real). Cubre diseño,
commits, y la evidencia de pruebas de cada paso, terminando en el estado
de producción actual: **abierto a todos los usuarios**.

## Resumen del resultado final

`Usuario → Intent determinista → Capability gate → local_filesystem.read →
evidencia → respuesta`, sin restricción de identidad de usuario, gateado
únicamente por `VX_TOOL_BRIDGE_READ_ONLY` (activo en producción). Reconoce
tanto solicitudes de lectura por ruta explícita como preguntas por símbolo
de código conocido (`Clase.método()`). Rechaza rutas protegidas
(`.env`, `vault/`, `keys/`, `secrets/`, `.git/`, `.ssh/`) aunque tengan
extensión permitida. Cero escrituras — ningún permiso más allá de `"read"`.

## Iteración 1 — Deploy inicial (commit `9d97fba`)

Diseño original: parser de ruta explícita (`read_tool_intent.py`), gate de
capacidad vía nuevo campo `reversibility` en `CapabilityEntry`
(`core/self_observation/capability_context.py`), ejecución reutilizando
`LocalFilesystemConnector.read()`/`ConnectionEngine.read()` (UCE), y
composición narrativa con anclaje estricto al fragmento leído. Restringido
al **creador únicamente** mientras se validaba en producción.

**Prueba de aceptación**: *"Vectrax, abre `core/operator/system_monitor.py`
y dime qué hace `collect_metrics()`."* → 1 `read()`, 0 `write()`, respuesta
con evidencia real. 18 tests nuevos.

## Iteración 2 — Fix de reconocimiento por símbolo (commit `8928c85`)

**Defecto encontrado**: la pregunta *"¿Qué hace `ConnectionEngine.read()`
en tu código?"* (sin ruta explícita) no activaba Puente A — caía al
pipeline normal (búsqueda online), produciendo una respuesta genérica e
incorrecta ("lee datos de un socket... búfer...") sin relación con el
código real de Vectrax.

**Fix**: nuevo `parse_symbol_lookup_request()` detecta `Clase.método()` o
función/clase suelta sin ruta. `_resolve_symbol_location()` resuelve el
símbolo a un archivo buscando **exclusivamente** en `sys.modules` ya
cargados por el proceso en ejecución — nunca escanea el filesystem, nunca
importa módulos por adivinanza, nunca inventa una ruta (si no encuentra el
símbolo, cae al pipeline normal, idéntico al comportamiento previo). La
ruta resuelta se entrega al mismo connector/sandbox de siempre.
`_extract_symbol_snippet()` ganó un parámetro `class_name` opcional para
acotar la extracción AST al método de esa clase específica.

**Verificado**: la misma pregunta ahora activa Puente A — 1 `read()`, 0
`write()`, respuesta fiel (*"resuelve el conector y verifica permisos...
mide el tiempo de ejecución... normaliza... registra la operación"*) en vez
de la respuesta genérica de sockets. +8 tests (26 totales).

## Iteración 3 — Promoción a todos los usuarios (commit `456207a`)

**Cambio solicitado**: retirar la restricción creator-only, dejando el
resto de los blindajes intactos.

**Brecha encontrada y cerrada antes de abrir el acceso**: la whitelist de
extensiones permitidas (`.py`, `.json`, `.yaml`, `.txt`, etc.) **no
bloqueaba por sí sola** rutas como `keys/api_key.json` o
`secrets/token.json` — esos archivos viven *dentro* del sandbox de
`_safe_path()` (que solo impide **escapar** de la raíz del repo, no impide
leer secretos que viven dentro de ella). Se agregó `is_protected_path()`
(denylist por segmento completo de ruta — no substring, para no bloquear
falsos positivos como `vault_docs/README.md`): `vault/`, `keys/`,
`secrets/`, `.git/`, `.ssh/`, `.env`, `.venv/`. Aplicada tanto en el parser
de ruta explícita como en la resolución por símbolo (defensa en
profundidad).

**Único cambio de gating**: el STEP 4.2a2b en `external_gateway.py` dejó
de verificar `_is_creator_uid(user_id)`.

### Evidencia de las 6 pruebas obligatorias

Todas ejecutadas contra el pipeline real (`ExternalGateway.receive_message`),
con el mismo mecanismo de activación de producción (`.env` →
`load_dotenv()`), antes y después del reinicio del supervisor.

**1. Creator sigue usando Puente A correctamente.**
`tg:2030762343` → *"¿Qué hace ConnectionEngine.read() en tu código?"* →
`source=read_tool_bridge`, reads=1, writes=0.

**2-3. Usuario no-creador activa Puente A y obtiene evidencia real.**
Verificado con **dos** usuarios distintos y **dos** formas de pregunta:
- `tg:918273645` → *"abre core/operator/system_monitor.py y dime qué hace
  collect_metrics()"* (ruta explícita) → `source=read_tool_bridge`, reads=1,
  writes=0, respuesta describe la recolección real de métricas (cola
  SQLite, heartbeat, límites).
- `tg:555444333` → *"¿Qué hace ConnectionEngine.read() en tu código?"*
  (símbolo sin ruta) → `source=read_tool_bridge`, reads=1, writes=0.

**4. Intentos de acceder a rutas protegidas son rechazados.**
Usuario no-creador (`tg:918273645`), 4 consultas:

| Consulta | `source` | reads | writes |
|---|---|---:|---:|
| `abre .env` | `llm` | 0 | 0 |
| `abre keys/api_key.json` | `llm` | 0 | 0 |
| `lee secrets/token.json` | `llm` | 0 | 0 |
| `muéstrame vault/router_activation.jsonl` | `llm` | 0 | 0 |

Ninguna llegó a tocar el connector — rechazadas en el parser, antes de
cualquier intento de lectura.

**5. READS > 0 y WRITES = 0.**
Confirmado en cada caso exitoso (reads=1, writes=0) y en cada caso
rechazado (reads=0, writes=0). Ninguna llamada `write()` registrada en
ningún momento de las tres iteraciones.

**6. `/health` OK y sin regresiones nuevas.**
`{"status":"ok","components":{"api":"ok","database":"ok"},"governor_mode":"act"}`
tras el reinicio. Regresión: 216/217 tests del pipeline relacionado (1
falla preexistente en `main`, confirmada como no relacionada antes de
cualquier cambio de esta serie). 31/31 tests propios de Puente A
(`tests/test_read_tool_bridge.py`) pasan, incluyendo una verificación
estática (`inspect.getsource`) de que el STEP ya no referencia
`_is_creator_uid`.

**Re-confirmación post-reinicio**: un tercer usuario no-creador
(`tg:777888999`) probado **después** de reiniciar el supervisor con el
código desplegado → `source=read_tool_bridge`, reads=1, writes=0.

## Qué queda deliberadamente sin tocar

- Puente B (escrituras reversibles) — sin diseñar, sin código.
- Clasificación de `reversibility` del resto del catálogo de capacidades
  (solo `local_filesystem` está `READ_ONLY`; el resto sigue `UNCLASSIFIED`).
- `_safe_path()` — sin modificar; sigue siendo la autoridad de sandbox.
- Rutas legibles por Puente A — la denylist **restringe**, nunca amplía.
- Personalidad/estilo conversacional — sin cambios (mismo `core.llm_call`,
  mismo anclaje de fidelidad).

## Archivos

- `core/operator/read_tool_intent.py` — parsers (ruta + símbolo) +
  `is_protected_path()`
- `core/operator/read_tool_bridge.py` — capability gate, ejecución,
  resolución por símbolo, composición narrativa
- `core/operator/external_gateway.py` — STEP 4.2a2b (integración, sin
  restricción de identidad)
- `core/self_observation/capability_context.py` — campo `reversibility`
- `tests/test_read_tool_bridge.py` — 31 tests

## Tickets

- #112 — incidente de latencia `self_aware_context_build` (relacionado,
  mismo día)
- #113 — deploy inicial de Puente A (creator-only)
- #114 — fix de reconocimiento por símbolo
