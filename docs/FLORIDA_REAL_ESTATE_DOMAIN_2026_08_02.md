# Dominio florida_real_estate — modelo + conector
Fecha: 2026-08-02
Creador: Mario Bravo Castro
## Qué es
Nuevo dominio para que VECTRAX observe el mercado inmobiliario de **todo el estado de Florida**, aprenda y **dé criterio** igual que market o freight. Es **isomorfo** a los dominios existentes: encaja en el núcleo invariante (gravity → OutcomeAdapter → verification_ledger → elevación domain_knowledge → criterio) sin tocarlo. **Agnóstico a la fuente**: la fuente de datos es un `FeedProvider` intercambiable.
### Sin prioridad geográfica
No hay lista de Miami/Orlando/Tampa. La **zona es una condición** del `fingerprint`/`conditions_signature`; la atención hacia una zona **emerge** por masa gravitacional (`hits × cc × freq`) y promoción de tier — igual que el mercado prioriza el ticker más activo.
## Modelo del dominio
- **Entidades**: propiedad/listing, zona multi-resolución (ZIP→ciudad→condado→MSA→estado), segmento (`tipo × price_tier × zona`), actor (agente/brokerage), comunidad/HOA.
- **Eventos**: `new_listing`, `price_change` (cut/increase), `status_change` (pending/under_contract/off_market), `sale_closed`, `expired`/`withdrawn`/`cancelled`, `open_house`, `inventory_update`, `rent_listed`/`rent_closed`, `new_construction`/`permit`, `external_shock`.
- **Outcomes (verdad objetiva)**: `sale_closed`→WIN (score = sold/list − 1), `expired`/`withdrawn`/`cancelled`→LOSS, resto→PENDING. Puntúa con el núcleo invariante (`score_outcomes` → `DomainScore`).
- **Observaciones**: new_hot_zone, price_cut_cluster, absorption_shift, dom_trend, sale_to_list_shift, inventory spike/drought, cross_zone_convergence, external_shock_impact, criterion_ready.
- **Métricas**: (A) calidad del criterio: win_rate/accuracy/expectancy/lift por segmento (verification_ledger). (B) estado del mercado: mediana precio y $/sqft, DOM, sale-to-list, meses de inventario/absorción, flujo listings vs cierres, frecuencia/magnitud de recortes, yield/cap-rate. Evolución vía `gravity.growth_trends()` + `trend_reader`.
## Conector (connectors/real_estate/)
- `base.py` — `RealEstateEvent` + contrato `RealEstateFeedProvider`.
- `__init__.py` — `get_provider()` (env `REAL_ESTATE_FEED_PROVIDER`: `simulator`|`attom`|`rentcast`, default `simulator`).
- `simulator_adapter.py` — eventos sintéticos statewide sin prioridad geográfica (piloto sin credenciales).
- `attom_provider.py` — cliente REST de ATTOM (gated por `ATTOM_API_KEY`).
- `rentcast_provider.py` — cliente REST de RentCast (gated por `RENTCAST_API_KEY`); `/listings/sale` (actividad) + `/properties` (verdad de cierre, opt-in).
- `real_estate_outcome_adapter.py` — verdad objetiva → WIN/LOSS/PENDING.
- `verification_cycle.py` — `verify_events` (subject = `zona|tipo|tier`) → `DomainScore` + persistencia.
- `learning_cycle.py` — `run_learning_cycle` (ingesta → gravity, elevación, verificación). **Standalone: NO cableado al pipeline_worker.**
Todos los providers son **read-only y defensivos**; sin key son **inertes** (no tocan producción).
## Variables de entorno
- `REAL_ESTATE_FEED_PROVIDER` = simulator | attom | rentcast (default simulator)
- `ATTOM_API_KEY`, `ATTOM_FL_POSTALCODES`, `ATTOM_HTTP_TIMEOUT`
- `RENTCAST_API_KEY`, `RENTCAST_FL_POSTALCODES`, `RENTCAST_INCLUDE_SOLD`, `RENTCAST_HTTP_TIMEOUT`
- `REAL_ESTATE_EVENTS_PER_CYCLE`, `REAL_ESTATE_LEARN_ENABLED`, `REAL_ESTATE_VERIFY_ENABLED`, `REAL_ESTATE_TENANT_ID`
## Validación (sandbox aislado, sin tocar producción)
Con `VECTRAX_VAULT_DIR` temporal + índice de gravity y domain_library redirigidos: 600 eventos del simulador → 28 estrellas (21 maduras), 21 patrones elevados a la librería, verificación con `DomainScore` real, y `build_criterion("florida_real_estate")` produjo una **opinión grounded** citando evidencia real. Flujo aprendizaje→elevación→verificación→criterio confirmado. Además: `tests/test_real_estate_domain.py` (10 tests herméticos, sin red).
## Fuentes: pilot vs ideal
- **Piloto rápido**: RentCast o ATTOM (self-serve; RentCast tiene listings + properties + markets por ZIP). Requiere key/suscripción activa.
- **Ideal a largo plazo**: RESO Web API agregando los MLS de Florida (Stellar + Miami + otros) para cobertura estatal y ciclo completo, complementado con ATTOM/CoreLogic + registros públicos de condado para verificación de cierres.
Gracias al `FeedProvider` agnóstico, cambiar de piloto a MLS es escribir un provider nuevo — sin tocar el modelo del dominio.
## Estado
Conector construido y probado; no cableado al worker; providers externos inertes hasta configurar su key. Siembra en producción (que crearía estrellas reales del dominio en el universo) es un paso deliberado posterior.
