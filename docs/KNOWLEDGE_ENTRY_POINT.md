# Camino de entrada de conocimiento nuevo al Universo Cognitivo

**Por qué existe este documento**: durante la incorporación de TA-Lib como
conocimiento formal de Market (`connectors/market/ta_knowledge.py`,
PR #131), se construyó primero una ruta de siembra incorrecta —196
`GravityRecord` creados directamente en `gravity_index.json`— antes de
localizar la ruta que Vectrax ya tenía para esto. Este documento fija esa
ruta correcta por escrito, para que la **próxima** vez que un dominio
incorpore conocimiento formal (no experiencia, no un patrón aprendido:
*conocimiento*, algo que Vectrax "estudia") se **alimente** este camino en
vez de volver a descubrirlo o de reconstruir otro por accidente.

## El principio

> La Escuela se adapta a Vectrax; Vectrax no se adapta a la Escuela.

Conocimiento nuevo (un catálogo, una definición, una taxonomía —
cualquier cosa que Vectrax "aprende" antes de tener experiencia sobre
ella) entra al universo por una puerta que ya existe. Ningún dominio
nuevo debería necesitar inventar su propio mecanismo de creación de
estrellas, masa, capas, conexiones o convergencias — todos esos
mecanismos ya existen, son genéricos, y **el conocimiento se limita a
usarlos**.

**Escuela y dominios entran por la MISMA puerta — `ingest()`.** No son
dos rutas paralelas al universo. La diferencia es solo *cuándo* llegan:

- **Escuela** (conocimiento formal, sin experiencia todavía — p. ej. el
  catálogo de TA-Lib): entra a `ingest()` de inmediato, el día que se
  incorpora. No tiene experiencia previa que madurar.
- **Dominio** (patrones observados repetidamente en la práctica — p. ej.
  `market:{symbol}`, `freight:{lane}`): acumula primero como
  `GravityRecord` en `gravity_index.json` vía `record_event()` (eso SÍ es
  su ruta propia, y es correcta — es la capa de aprendizaje de
  experiencia, no el universo). Pero en cuanto un patrón madura
  (`hits >= MIN_HITS_TO_SYNC`), `core/gravity_sync.py::_promote_mature_patterns()`
  lo lleva al universo llamando a **la misma `ingest()`** que usa la
  Escuela — mismo `channel="user"`, mismo `owner="vectrax_system"`.

`gravity_index.json` no es una segunda puerta al universo — es una etapa
previa de acumulación de experiencia, propia solo de los dominios (la
Escuela no la necesita porque no tiene experiencia que acumular antes de
existir). Todo lo que termina siendo parte del universo cognitivo visual,
sin excepción, pasa por `ingest()`.

## Las poblaciones de "estrella" que existen — no confundirlas

Antes de tocar nada, hay que saber en cuál de estas tres se está
trabajando. Son sistemas distintos, con distinto propósito:

| Población | Dónde vive | Qué representa | Vocabulario de capas | ¿Es una puerta al universo? |
|---|---|---|---|---|
| **Universo cognitivo visual** | `vectrax.db` tabla `stars` | Contenido/conocimiento discreto, con embedding — lo que este documento cubre | `outer` / `mid` / `core` (`vectrax/gravity.py::assign_layer`) | Es el universo mismo — todo llega aquí vía `ingest()` |
| **Aprendizaje de patrones por dominio** | `~/.vectrax/gravity_index.json` (`core/learn/gravity_engine.py`) | Patrones observados repetidamente por dominio (`market:{symbol}`, `freight:{lane}`, ...) — experiencia, no conocimiento previo | `HOT` / `WARM` / `COLD` / `DEEP` | No — es la etapa PREVIA de los dominios; su contenido maduro entra al universo por `ingest()` vía `gravity_sync.py` |
| **Memoria profunda de conversación** | `~/.vectrax/gravity.db` (`SQLiteVectorStore`, ver `ARCHITECTURE.md §5`) | Recuerdos conversacionales del usuario (visión, persona, emoción, salud) | (`MassKind`, no capas) | No — sistema aparte, sin relación con esto |

## La ruta correcta: `vectrax.engine.ingest()`

```python
from vectrax.engine import ingest

star = ingest(
    text="descripción factual del concepto",
    success=False,           # no hay outcome todavía — es conocimiento, no experiencia
    channel="user",
    owner="vectrax_system",  # convención para conocimiento de sistema, no de un usuario real
)
```

Es la única función del repositorio que crea una "estrella normal de
conocimiento" (confirmado por el test canónico
`tests/core/test_core_pipeline.py::TestIngestV1::test_ingest_creates_knowledge_star`).

### Qué hace, sin que el llamador tenga que saberlo

1. **Embeb el texto** (`vectrax.embeddings.embed`).
2. **Comprueba near-duplicate** dentro del mismo `channel`+`owner`
   (similitud de embedding ≥ 0.95) — si ya existe algo muy parecido,
   actualiza esa estrella (`repetition_count += 1`) en vez de crear otra.
   **No hay que construir una capa de identidad propia** — `ingest()` ya
   decide esto.
3. Si es nueva: calcula `gravity_score` (`compute_star_gravity`) y
   `layer` (`assign_layer`, `vectrax/gravity.py`) — **nunca se asignan a
   mano**. Una estrella recién creada, sin repeticiones ni conexiones,
   computa gravedad baja y cae en `outer` por consecuencia natural del
   cálculo, no por decisión de quien la crea.
4. Nace en `MIN_MASS` (`vectrax/models.py`) — **nunca se asigna masa
   artificial**.
5. Persiste (`vectrax.db.insert_star`) y la añade al grafo
   (`vectrax.graph.add_star`).
6. Corre `_post_ingest()` (mismo archivo, `vectrax/engine.py`):
   - Emite el evento `STAR_CREATED`/`STAR_UPDATED` al bus universal.
   - **Enlaza con vecinas similares** por similitud de embedding, dentro
     del mismo `channel`+`owner` — la relación entre dos conceptos la
     decide el motor de embeddings, **nunca una regla escrita a mano**.
   - Registra trayectoria.
   - **Auto-detecta convergencia** (`vectrax/convergence_engine.py`).
   - **Detecta patrones de constelación**.

Todo esto ya existía y sigue exactamente igual — conocimiento nuevo no le
añade ni le cambia nada.

### Convención para conocimiento de sistema (no conversacional)

`channel="user"`, `owner="vectrax_system"` — ya establecida y en uso en
`core/gravity_sync.py::_promote_mature_patterns()` (que promueve
patrones maduros de `gravity_index.json` hacia esta misma capa) y
reconocida explícitamente por `core/universe_census.py` para excluir
estas estrellas del conteo conversacional real.

## Ejemplo de referencia real

`connectors/etoro/knowledge_gravity_seed.py` (PR #131, commit `8b33341`)
es el ejemplo funcionando: incorpora el catálogo de 196 funciones de
TA-Lib llamando a `ingest()` una vez por concepto, con texto factual
tomado de los propios metadatos de TA-Lib (`display_name`/`group`, nunca
relaciones inventadas). Verificado en vivo: las 196 nacieron en
`layer=outer`, `mass=MIN_MASS`, y 3 estrellas de convergencia + 3
constelaciones emergieron solas, sin ninguna regla añadida — puro efecto
de `_post_ingest()` sobre contenido real.

## Qué NO hacer (aprendido de la ruta incorrecta que se corrigió)

- **No crear `GravityRecord` para conocimiento nuevo.** `gravity_index.json`
  es para patrones de experiencia ya vivida, no para conceptos que Vectrax
  todavía no ha observado en la práctica.
- **No asignar `layer` manualmente.** Si algo necesita nacer en `outer`,
  debe ser porque `compute_star_gravity()`+`assign_layer()` lo calculan
  así de forma natural — nunca porque el código lo fuerza.
- **No asignar masa artificial.** `MIN_MASS` al nacer es correcto y
  suficiente; la masa crece por el propio motor cuando hay repetición o
  conexiones reales.
- **No inventar una capa de identidad/deduplicación propia.** `ingest()`
  ya decide qué es "lo mismo" vía similitud de embedding.
- **No hardcodear relaciones entre conceptos.** Ni "A implica B", ni
  agruparlos con una etiqueta compartida que sesgue después cómo los
  mecanismos existentes (p. ej. `core/learn/constellation.py`) los tratan.
  Si dos conceptos están relacionados, que lo descubra el motor de
  embeddings o la experiencia posterior — no una decisión de quien siembra.

## Checklist para la próxima Escuela (o el próximo dominio)

1. Escuela y dominio llegan al mismo sitio — `ingest()` — solo cambia
   CUÁNDO: conocimiento nuevo sin experiencia todavía → `ingest()`
   directo, el mismo día que se incorpora. Un patrón que se está
   observando en la práctica → primero `gravity_engine.record_event()`
   (acumula experiencia como `GravityRecord`), y cuando madure
   (`hits >= MIN_HITS_TO_SYNC`), `gravity_sync.py` lo lleva a esa misma
   `ingest()`. Nunca se inventa una tercera ruta.
2. Un texto factual por concepto — sin relaciones inventadas, sin
   implicaciones de acción.
3. `channel="user"`, `owner="vectrax_system"` (o el `owner` que
   corresponda si no es del sistema).
4. Dejar que `ingest()`/`_post_ingest()` hagan el resto. No escribir
   ningún código nuevo de capas, masa, conexiones, convergencias o
   constelaciones.
5. Verificar en vivo (DB aislada, `embed()` determinista como en
   `tests/core/test_core_pipeline.py`) que el resultado cae en `outer`,
   `MIN_MASS`, y que no se creó ningún `GravityRecord`.

Creador: Mario Bravo Castro
