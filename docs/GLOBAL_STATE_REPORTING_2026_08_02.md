# Reporte de estado global — narrativa casual y grounded por dominio
Fecha: 2026-08-02
Creador: Mario Bravo Castro
## Qué es
Vectrax ahora habla de forma **casual y espontánea** — como quien comenta su día — sobre **qué está observando en cada dominio** (freight_logistics, market, ai_provider, …), con **datos reales y precisos**, sin que el usuario tenga que usar ningún comando. Se activa por la detección de intención en lenguaje natural (`is_self_referential`), no por un comando.
Como herramienta secundaria del creador existe además `/vx global`, un reporte determinista del estado global.
## Objetivo de diseño
- La vía de lenguaje natural debe sonar humana y espontánea, no como un panel ni un reporte.
- Los números deben ser reales y exactos (sin inventar ni redondear): se reutiliza el censo (SSOT) y los motores ya existentes.
- No romper nada: todo es read-only y defensivo (si una fuente falla, las demás siguen).
## Componentes
### 1. `core/system_report.py` (nuevo, read-only, defensivo)
Capa de presentación/agregación. No calcula nada nuevo ni persiste; compone piezas existentes.
- `get_global_state() -> dict`: snapshot estructurado (estrellas, convergencias, motores, dominios, crecimiento, extra, private). Cada fuente en su propio try/except.
- `build_global_report(lang="es", scope="full"|"public", state=None) -> str`: texto determinista HTML-friendly con 5 secciones (estrellas, convergencias, dominios, motores, crecimiento). `scope="public"` omite conteos privados (usuarios/interacciones/hechos/equipos).
- `build_domain_observations(max_domains=4, per_domain=2, lang="es") -> str`: digest **grounded por dominio** — insumo para la narrativa casual. Por cada dominio con más masa: conteo de estrellas, activaciones, intents más activos y estrellas nuevas de la semana. Salta dominios de ruido (`unknown`, `tests`, `user_interest`).
- `is_global_status_request(text) -> bool`: detector **estricto** de petición explícita de estado (solo usado por el gate público).
Fuentes reutilizadas (todas read-only): `core.universe_census.get_census`, `core.orchestration.get_engine_status`, `core.learn.gravity_engine` (`domain_stats`, `top_stars`, `growth_trends`), `core.learn.convergence_history` (`count_events`, `get_active`), `core.domain_knowledge.list_domains`.
### 2. `vectrax/self_context.py` (modificado) — la vía casual
- `build_self_context()` inyecta el digest de `build_domain_observations()` de forma prominente (aplica a todos: son datos del universo, no conteos privados).
- `_SELF_PROMPT_ES` / `_SELF_PROMPT_EN` ajustados: primera persona, tono casual y espontáneo, mencionar 1-3 dominios y **qué** observa en ellos con los números reales del contexto (sin inventar ni redondear), sin formato de reporte/lista, pocas frases. Se conserva el anti-genérico previo.
Flujo: `external_gateway` STEP 4.2b → `is_self_referential(content)` → `resolve_self_aware()` → `build_self_aware_prompt` → `build_self_context` + prompt casual → LLM.
### 3. `vectrax/telegram_gateway.py` (modificado) — herramienta del creador
- Nuevo subcomando `/vx global` (alias `/vx estado`), solo creador (el gate de `/vx` ya existía): responde `build_global_report(scope="full")`. Añadida su línea a `/vx help`.
### 4. `core/operator/external_gateway.py` (modificado) — acceso no-creador opt-in
- STEP 4.2a4: para **no-creadores**, ante una petición explícita (`is_global_status_request`) y **solo** si la env `VECTRAX_PUBLIC_UNIVERSE_STATUS` está activa, responde `build_global_report(scope="public")` de forma determinista. Por defecto **OFF** → el comportamiento no cambia (lo maneja el LLM en 4.2b).
## Alcances (scopes)
- `full`: incluye conteos privados (usuarios/interacciones/hechos/equipos). Usado por `/vx global` (creador).
- `public`: solo la vista del universo (estrellas, dominios, convergencias, motores, crecimiento); sin conteos privados. Usado por el gate no-creador.
- La narrativa casual por-dominio no expone conteos privados y nunca filtra el nombre del creador a otros usuarios.
## Configuración
- `VECTRAX_PUBLIC_UNIVERSE_STATUS` (default OFF): habilita el reporte público-reducido para no-creadores ante petición explícita. Valores activos: cualquiera distinto de ``/`0`/`false`/`off`/`no`.
## Ejemplo real (vía lenguaje natural)
Pregunta: "¿Cómo va tu universo? Cuéntame qué estás observando en tus dominios ahora mismo."
Respuesta:
> Estoy viendo que mi universo tiene un total de 1545 estrellas, con 712 gravitacionales y 798 de conocimiento. En el dominio de freight_logistics, tengo 623 estrellas activas y 9744 activaciones, lo que muestra un crecimiento notable. En el mercado, hay 11 estrellas, con TSLA y NVDA siendo las más activas, cada una con 66 hits. La observación de mercado está activa, con 1075 señales totales. La tendencia sigue siendo de crecimiento. ¿Tú cómo vas?
## Tests
`tests/test_system_report.py` (9 tests): composición del snapshot, secciones del reporte, `public` oculta conteos privados, robustez defensiva ante fallo de cada fuente, detector estricto, y digest por-dominio (grounded, vacío-sin-stats, defensivo).
## Garantías
- Read-only y defensivo: nunca lanza; no crea ni migra datos.
- Determinismo en `build_global_report` (mismo estado → mismo texto).
- Reutiliza el SSOT (censo) y los motores existentes; no duplica lógica de conteo.
## Antigüedad / duración temporal (core/trend_reader.py)
Añadido 2026-08-02. Permite fundamentar referencias temporales ("llevo observando esto desde hace X días") SOLO en datos verificables; el LLM nunca infiere duraciones.
### Anclas reutilizadas (read-only, sin nueva persistencia)
- `GravityRecord.first_seen`/`last_seen` (ISO) — antigüedad por patrón/dominio (`core/learn/schemas.py:50-54`).
- `convergence_events.timestamp` (event='birth') — edad de cada convergencia activa (`core/learn/convergence_history.py`).
- `Outcome.resolved_ts` por dominio — span de resultados verificados (`core/learn/verification_ledger.py`).
### API de `core/trend_reader.py`
- `days_since(ts)`: acepta epoch (convergencias/verification) e ISO (gravity).
- `get_pattern_duration(fingerprint)`, `top_pattern_durations(domain, n)`.
- `active_convergence_ages(domain=None)`, `domain_outcome_span(domain)`.
- `get_domain_duration(domain)`: compone todas las anclas.
- `domain_observing_since_days(domain)`: ligero (solo gravity), usado por `/vx global`.
- `build_duration_digest(domain=None, lang)`: bloque grounded «DESDE CUÁNDO» para inyección.
### Integración
- Narrativa casual (lenguaje natural): `build_self_context` inyecta `build_duration_digest()`; `_SELF_PROMPT_ES/EN` exige usar SOLO las cifras de la sección «DESDE CUÁNDO» y nunca estimar fechas/duraciones.
- `/vx global`: `build_global_report` añade la línea «🕒 Antigüedad: <dominio> Nd · …» (top dominios, salta ruido) vía `domain_observing_since_days`.
### Limitación conocida
- La antigüedad refleja el `first_seen` del `gravity_index` actual (~7 días tras la última migración/reset del índice), no la edad del proyecto. Para "desde el origen" habría que derivarla de otra fuente (p. ej. primer snapshot de `evolution_memory`).
### Tests
- `tests/test_trend_reader.py` (10) y el caso de antigüedad en `tests/test_system_report.py`.
