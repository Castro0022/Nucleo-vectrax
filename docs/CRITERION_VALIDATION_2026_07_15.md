# Motor de Criterio Aprendido — Validation & Closure Report

- **Task:** Confirmar que las métricas que VECTRAX reporta para el patrón `freight_logistics:empty_miles` coinciden con la base de datos de producción, y cerrar el ticket de validación del Motor de Criterio Aprendido.
- **Delivered:** 2026-07-15 · **Status:** ✅ Closed (implementado, testeado, desplegado, verificado, documentado)
- **Branches/merges:** `feat/motor-criterio-aprendido` → PR #31 → `main` (`86b4aa8`); `feat/register-criterion-engine` → PR #32 → `main` (`19d43e6`, motor #48); `docs/criterion-component-registry` → PR #33 → `main` (`4cd5ee3`)

## 1. Solicitud de validación
Consultar los valores exactos en producción del patrón `freight_logistics:empty_miles` (WR real, expectancy real, número de activaciones, número de observaciones) y confirmar si coinciden con lo reportado por VECTRAX: **WR 90%, expectancy +49.73, 71 activaciones**.

## 2. Hallazgos — producción (2026-07-15)
Consulta en vivo dentro de `vectrax-core` sobre las dos fuentes reales que usa el motor:

- **WR real: 90.0%** — `domain_library` (`get_domain_priors`)
- **Expectancy real: +49.727** — `domain_library`
- **Observaciones (sample_size): 56** — `domain_library` (confianza HIGH, contributing_tenants=2)
- **Activaciones (hits): 71** — `gravity_index` (`by_domain`; cc_score 0.9, tier HOT)
- Derivados por el motor: Wilson LB **78.5%**, score **46.2859**

Vista mergeada (`criterion.rank_domain_evidence`):
`{"name":"empty_miles","win_rate":90.0,"expectancy":49.727,"sample_size":56,"confidence":"HIGH","hits":71,"wilson_lb":78.5,"tier":"HOT","score":46.2859}`

## 3. Confirmación vs. lo reportado
- WR 90% → ✅ exacto (90.0%)
- Expectancy +49.73 → ✅ coincide (+49.727 redondeado)
- 71 activaciones → ✅ exacto (gravity hits = 71)

Los tres números reportados son **correctos**. No hay discrepancia.

## 4. Aclaración clave — dos métricas distintas
- **Activaciones = 71** (`gravity_index` hits): cuántas veces se activó la estrella (masa/frecuencia).
- **Observaciones = 56** (`domain_library` sample_size): muestra decisiva sobre la que se calculan WR (90%) y expectancy (+49.727).

El patrón se **activó 71 veces**, pero el WR/expectancy se computan sobre **56 observaciones** decisivas. Ambos números son correctos; miden cosas diferentes. La confusión "71 = observaciones" es incorrecta: 71 son activaciones.

## 5. Estado del motor (contexto)
- Registrado como **motor #48** en `core/orchestration/engine_registry.py` (tier OBSERVE, grupo `aprendizaje`).
- `GET /v1/engines` y `GET /v1/universe` reportan `total: 48`; tarjeta «Motores» del dashboard 48/48.
- Read-only sobre la evidencia; nunca fabrica. Verificado en prod: opinión grounded sobre NVDA (market) y abstención constructiva ante `route_A/B/C` (freight).

## 6. Método de verificación
- `core.domain_knowledge.get_domain_priors("freight_logistics")` → priors de empty_miles (WR/E/N/confianza).
- `core.learn.gravity_engine.get_gravity_index().by_domain("freight_logistics")` → hits/cc/tier de empty_miles.
- `core.learn.criterion.rank_domain_evidence("freight_logistics")` → merge determinista de ambas fuentes.

## 7. Estado
- **Cerrado.** Métricas reportadas validadas contra producción; sin discrepancia. Sin ítems abiertos.

## Referencias
- Código: `core/learn/criterion.py`, `core/orchestration/engine_registry.py`.
- Docs: `README.md` → "🧭 Motor de Criterio Aprendido"; `docs/ENGINES.md`; `ARCHITECTURE.md` + `docs/ARCHITECTURE.md`.
- PRs: #31 (motor), #32 (registro #48), #33 (registro de componentes).
