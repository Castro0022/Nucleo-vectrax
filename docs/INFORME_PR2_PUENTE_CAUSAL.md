# PR 2 — Puente causal: convergencia → aprendizaje → criterio

**Rama**: `claude/pr2-causal-learning-bridge`
**Base**: `42720ce69fb7e3c0731073c0e0ab6fc1b6ae4b17` (merge del PR #124)
**Estado**: producción directa. Sin modo sombra.

---

## 0. Confirmación de rama

```
git branch --show-current   -> claude/pr2-causal-learning-bridge
git merge-base HEAD main    -> 42720ce69fb7e3c0731073c0e0ab6fc1b6ae4b17
```

El puente causal existe **solo** en los commits de esta rama. La rama del PR
#124 (`claude/pr1-permissions-evidence-test-isolation`) apunta a `2548cde` y no
contiene ninguno de estos commits. Lo que la interfaz muestra como "PR #124
fusionado" es la **base** desde la que se cortó esta rama, que es exactamente
lo especificado.

Nota sobre una confusión real detectada durante la sesión: `git merge-base HEAD
main` devolvía `b011823` porque el ref **local** `main` estaba desactualizado en
el PR #122. `origin/main` (`d4e48b8`) sí contiene `42720ce`. Se sincronizó el
ref local; no se tocó ningún commit.

---

## 1. Qué políticas de producción quedaron activas

`causal_learning.ensure_production_policies()` registra, de forma idempotente,
una política por cada dominio operativo con productor real:

| Dominio | policy_id |
|---|---|
| `market` | `production-market` |
| `freight_logistics` | `production-freight_logistics` |
| `florida_real_estate` | `production-florida_real_estate` |
| `cybersecurity` | `production-cybersecurity` |

Ninguno queda en `AWAITING_POLICY` (probado).

**Discrepancia de nombre que conviene que veas**: pediste `real_estate`. La
clave REAL del dominio inmobiliario en el código es `florida_real_estate`
(`internal_evidence._AUDIT_ACTION_DOMAIN` la mapea así desde
`real_estate_learning_cycle`). Registré la política bajo la clave real, porque
una política bajo `real_estate` no se aplicaría a ninguna convergencia viva.
Si quieres además el alias, es una línea.

Los dominios sin productor real (los 8 `config/domain_templates/*.json`, entre
ellos `restaurant`) **no** reciben política: sin productor no hay evidencia que
pueda cruzar un umbral, y fingir lo contrario sería inventar semántica.

---

## 2 y 3. Umbrales exactos y de qué constante procede cada uno

Los cuatro dominios usan los **mismos** valores. Ninguno se rebajó para
conseguir que aparecieran aprendizajes (probado: los umbrales de los cuatro
dominios son idénticos).

### Cualificación de CADA patrón fuente

| Umbral | Valor | Procedencia |
|---|---|---|
| `min_sample_size` | 15 | `core.domain_knowledge.MIN_SAMPLE` |
| `min_win_rate` | 55.0 % | `core.domain_knowledge.MIN_WIN_RATE` |
| `min_expectancy` | 0.0 (estricto `>`) | `core.domain_knowledge.MIN_EXPECTANCY` |

Semántica de origen: son los umbrales que definen literalmente cuándo un patrón
es lo bastante sólido para elevarse a conocimiento de dominio.

### Fuerza de la CONVERGENCIA

| Umbral | Valor | Procedencia |
|---|---|---|
| `combined_hits` | 5 | `core.learn.gravity_engine.ALERT_MIN_HITS` — comentario literal: *"combined hits to consider significant"* |
| `combined_cc` | 0.4 | `core.learn.gravity_engine.ALERT_MIN_CC` — comentario literal: *"combined coherence to consider strong"* |

### El umbral que NO tiene precedente, y por qué vale 1

`minimum_distinct_evidence_revisions = 1`.

En el diseño anterior valía 2, como "hace falta al menos una revisión además de
la inicial". **Lo bajé a 1 y eso no es una rebaja**: tu requisito 17 exige que
el primer ciclo vivo pueda producir aprendizaje si ya existe evidencia
suficiente. Exigir 2 revisiones sería exactamente el periodo artificial de
espera que prohibiste.

La protección contra la falsa repetición **no** es una espera: es el
`evidence_revision_hash`. Un re-escaneo produce la MISMA revisión, así que no
añade evidencia ni confianza. La barra real la ponen las métricas de los
patrones, que sí son evidencia observada.

Consecuencia: **todos** los umbrales proceden ahora de una constante existente.
`LearningPolicy` rechaza en construcción cualquier umbral sin procedencia
documentada, así que esto no puede degradarse en silencio.

---

## 4. Que no existe modo sombra

`tests/test_no_shadow_mode_contract.py` recorre el código vivo y falla si
reaparece `ELIGIBLE_SHADOW`, `shadow_criterion`, `learning_shadow` o
`MODE_SHADOW` — ninguno existe hoy en el árbol — o cualquier variable de
entorno `VECTRAX_(CAUSAL|LEARNING)_(SHADOW|MODE)`. Además comprueba sobre la
API real que no sobrevive ningún atributo de modo, y sobre el esquema SQLite
que no queda ninguna columna `mode` ni `execution_mode`.

**Matiz importante, y es un hallazgo tuyo**: la frase genérica "shadow mode" no
se puede prohibir en todo el árbol porque Vectrax ya tenía un subsistema
preexistente y ajeno con ese nombre. Ver el anexo al final: son **tres** cosas
distintas que comparten la palabra, y solo una es sombra de verdad. La frase
queda prohibida en la **superficie causal**, que es donde significaría lo que
eliminamos, y un guard comprueba que el subsistema ajeno sigue en su sitio (no
se borra nada a ciegas).

---

## 5. Un caso sintético completo que cambia realmente el criterio

`tests/test_causal_criterion_effective.py` y `tests/test_causal_live_cycle.py`.

Cadena completa, con las otras tres fuentes del ranking neutralizadas para que
el efecto medido sea el del puente:

1. Dos patrones con métricas reales (20 outcomes graduados, 80 % de acierto,
   expectancy 0.6) convergen con `combined_cc=0.77`, `combined_hits=9`.
2. `evaluate_convergence` → `LEARNED`.
3. `criterion.rank_domain_evidence("market")` incluye la entrada, con la misma
   forma que las otras tres fuentes y la escala de confianza existente
   (`domain_knowledge._compute_confidence`: N=20, WR=80 → `MEDIUM`).
4. `criterion.effective_criterion("market")` devuelve `criterion_changed=True`,
   `causal_learning_ids=[LRN-…]`, `convergence_ids=[CONV-…]`,
   `criterion_version="crit-…"`, y el criterio anterior para poder contrastar.
5. Una decisión posterior conserva `criterion_version` y `decision_id`.
6. Un outcome favorable lo refuerza; uno contrario lo contradice.

Y por el camino vivo real: `record_convergence_snapshot()` con un candidato
cualificado produce aprendizaje **en el primer ciclo**, sin espera.

---

## 6. Que el aprendizaje no concede ejecución

`tests/test_causal_no_execution_authority.py` (10 pruebas):

- Las diez condiciones numeradas de `validate_entry` siguen presentes (0..9).
- El `return False` del HALT sigue siendo el primero de la función.
- El puente causal no importa `auto_executor`, `etoro` ni ningún broker.
- No define ninguna función cuyo nombre contenga `execute/order/buy/sell/trade`.
- No manipula `capital`, `max_ops`, `approved_symbols`, `paper`, `live_mode`,
  `position_size` ni `leverage` (comprobado sobre identificadores del AST, no
  sobre la prosa de los docstrings).
- `connectors/etoro/auto_executor.py` no lee el puente causal ni `learning_id`.
- **Sin convergencia, un aprendizaje no hace pasar un símbolo**: con
  aprendizajes en estado LEARNED y sin convergencia canónica,
  `_convergence_evidence("AAPL")["matched"]` sigue siendo `False`.
- Los `learning_ids` se calculan DESPUÉS de fijar `matched` (comprobado por
  posición en el AST), así que no participan en la decisión.
- Ninguna condición `if`/`while` del validador depende de un aprendizaje.

La decisión de entrada es el mismo booleano de siempre: misma consulta, mismo
orden, mismos umbrales `hits>=5` y `cc_score>=0.3`. Lo nuevo son los ids que la
acompañan como evidencia.

---

## 7. Qué sucederá en el primer ciclo después del despliegue

1. El observador llama a `record_convergence_snapshot()` como siempre.
2. Tras el commit del registro, el puente recibe **solo** las convergencias que
   ese ciclo tocó: creadas, confirmadas, reaparecidas y disueltas.
3. `ensure_production_policies()` registra las cuatro políticas (idempotente).
4. Cada convergencia se evalúa una vez por dominio participante con política.
5. Una convergencia cuyos **dos** patrones ya superen N≥15, WR≥55 % y E>0, y
   que alcance `combined_cc≥0.4` y `combined_hits≥5`, pasa a `LEARNED` **en ese
   mismo ciclo** y entra en el criterio inmediatamente.
6. Las que no, quedan en `CONVERGED_CANDIDATE` diciendo exactamente qué les
   falta. No por estar en sombra: por no cumplir la evidencia.

**Expectativa realista, y conviene que la tengas antes del despliegue**: el
cuello de botella será `min_sample_size=15` medido sobre `outcome_history`, que
`gravity_engine.MAX_OUTCOME_HISTORY` limita a **20 entradas por estrella**. Un
patrón necesita 15 de sus últimos 20 outcomes graduados como win/loss. Es
alcanzable, pero estrecho. Si en el primer ciclo no aparece ningún aprendizaje,
la causa más probable es esa — y la respuesta correcta **no** es bajar el
umbral, sino mirar cuántas estrellas tienen historia graduada suficiente.
`causal_candidates()` te lo dirá literalmente, patrón por patrón.

---

## 8. Cuántas convergencias vivas podrían evaluarse sin backfill

No puedo darte el número real desde aquí y no voy a inventarlo: el registro
canónico de este clon está **vacío** (`vault/convergence_history.db`, 40 KB, 0
filas en `convergences`). Las 121.206 convergencias y la base de 25,4 GB están
en el Mac.

Lo que sí está acotado por diseño:

- Se evalúa **lo que el escaneo de ese ciclo produjo**, no la tabla. El tamaño
  del trabajo depende de la salida del detector por ciclo, no del histórico.
- El tope es `MAX_EVALUATIONS_PER_CYCLE = 50` evaluaciones por ciclo.
- Una convergencia cruzada consume una evaluación por dominio participante.

Para el número exacto, en el Mac:
`len(get_canonical_convergences(status="active"))`.

---

## 9. Límite de trabajo por ciclo para no bloquear meta_loop

`MAX_EVALUATIONS_PER_CYCLE = 50`. Lo que no entra en un ciclo se evalúa en el
siguiente sin perderse: reencontrar la misma convergencia es idempotente por
`(convergence_id, evidence_revision_hash)`.

**Las disoluciones tienen prioridad.** Detecté que, ordenadas como venían, con
más de 50 convergencias vivas una disolución podía no evaluarse nunca, y el
criterio seguiría apoyándose en evidencia que ya no existe. Dejar de afirmar
algo falso es más urgente que afirmar algo nuevo, así que las disoluciones se
procesan primero (probado).

---

## 10. Comportamiento ante fallo parcial del almacén causal

Tres niveles, todos probados:

1. **Fallo total del puente**: `record_convergence_snapshot` conserva su
   resultado y lo que ya escribió. El puente corre DESPUÉS del commit del
   registro. El error queda en `result["causal"]["errors"]` — no se traga en
   silencio.
2. **Fallo de una convergencia**: se salta esa y continúa con las demás.
   Queda contabilizada en `errors`.
3. **Fallo que deja algo a medias**: no ocurre. Una evaluación fallida no deja
   ni traza de aprendizaje ni fila de decisión (probado).

En el criterio, si el almacén causal no se puede leer se registra a nivel
**warning**, no `debug` como las otras tres fuentes: perder aprendizajes que
deberían estar afectando al criterio tiene que verse.

En el Núcleo, un fallo del almacén se responde como `UNAVAILABLE` con el motivo
real. Nunca como un cero, y nunca como una afirmación.

---

## Anexo — El subsistema "shadow" preexistente

No es uno, son **tres** cosas que comparten la palabra. Ninguna se toca en este
PR.

### A) `core/shadow_mode.py` — dormido, pero NO borrable

| | |
|---|---|
| Quién lo invoca | **Nadie invoca la clase.** El único import vivo es `core/proposal_engine.py:37`, que toma una función: `compute_confidence()` |
| Qué deja sin efecto | **Nada.** Por contrato nunca bloquea; y como nadie lo activa, tampoco observa |
| Qué registra | Escribiría `shadow_log.jsonl` y `reports/shadow_report.json` — solo si se activara |
| Si puedes verlo | **No.** Sin CLI, sin ruta API, sin Telegram |
| Qué pasa al eliminarlo | **`core/proposal_engine.py` se rompe al importar**; `compute_confidence` alimenta `classify_proposal` (zona de autonomía) |
| ¿Sombra o sandbox? | Ninguna: una función dormida + una función estadística **viva** aparcada en el archivo equivocado |

`compute_confidence` es `1 − (desviación típica de las señales de riesgo / 0.5)`.
No tiene relación con observar en sombra.

### B) `constitutional_guard.shadow_check()` — sombra real y VIVA

Llamada desde `external_gateway.py:499`, `idea_store.py:503` y
`learning_integrator.py:250`. Evalúa el filtro constitucional y registra, pero
su contrato prohíbe usar el veredicto para decidir. **Es la que merece tu
decisión sobre activación.**

### C) `core/sandbox_runner.py` — no es sombra

Invocado desde `cli/vx_main.py:756`, un comando explícito. Sandbox de políticas
candidatas, correctamente acotado.

**Recomendación**: para (A), extraer `compute_confidence` a `core/risk_engine.py`
—donde viven sus entradas— antes de decidir nada sobre el resto del archivo. Así
la decisión sobre la sombra deja de estar acoplada a una función viva que no es
sombra. Es un PR aparte.
