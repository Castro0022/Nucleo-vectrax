# VECTRAX — Asset Evidence Map

> **Documento vivo (`living_document`).** Mapa de evidencia y valor del propio
> sistema: qué capacidades ya dejaron huella **verificable** y cuáles todavía
> necesitan cruzar el puente de evidencia.

- **status:** `living_document`
- **domain:** `company / architecture / evidence`
- **name:** `VECTRAX_ASSET_EVIDENCE_MAP`

---

## ⚠️ Nota de completitud
Este archivo se creó a partir de **fragmentos** aportados en sesión (§14 y §15)
más un principio de arquitectura. Las secciones **§1–§13 quedan pendientes de
contenido**: pegar el cuerpo completo del Asset Evidence Map para reemplazar el
placeholder de abajo.

---

## Principio de arquitectura — Las 7 Leyes Fundamentales
> Las 7 leyes **no son contenido del universo**. Son **sesgo de decisión
> pre-universo / gravedad de selección**. No deben tratarse como estrellas,
> patrones ni contenido de respuesta observable, salvo que se pida
> explícitamente.

**Evidencia (verificado 2026-06-28 — cumple ✅):**
- **Sesgo de selección pre-decisión:** `core/operator/law_enforcement.py` →
  `core/nucleus/law_signal.py` (`build_law_signal`) →
  `core/nucleus/presencia_pura.py` (`PresenciaObserver.evaluate`) ajusta
  `convergence / sovereignty / noise` **antes** de decidir
  `PERMIT / PAUSE / SILENCE / BLOCK`. Las leyes pesan; no responden.
- **Registro aislado:** las violaciones se registran **solo** en el *audit
  ledger* (`core/operator/ledger_bridge.py` → `core/audit_ledger`,
  `category=operator.reasoning`).
- **El universo no las observa:** `core/self_observation/quality_entities.py`
  (capa que materializa estrellas/fenómenos) lee **únicamente** el
  `observation_ledger` (`autonomous_observations`, dominios
  `quality | test | code | runtime`); **nunca** lee el audit ledger ni los
  eventos `law_violation:*`.
- **Sin estrellas ni respuesta:** las leyes no escriben en el `gravity_index` ni
  inyectan texto en respuestas al usuario.

> Invariante a preservar: cualquier cambio futuro debe mantener esta separación
> (leyes = sesgo + auditoría; universo = `gravity_index` + `observation_ledger`).

---

## §1–§13 — (pendiente de contenido)
_Pegar aquí el cuerpo completo del Asset Evidence Map (capacidades, evidencia
por capacidad, criterios de validación, madurez, etc.)._

---

## §14 — Frase de cierre
VECTRAX no se mide por promesas. Se mide por qué capacidades ya dejaron huella
verificable en el sistema y cuáles todavía necesitan cruzar el puente de
evidencia.

---

## §15 — Ubicación recomendada
- **Notas personales** → `VECTRAX — Asset Evidence Map`
  (uso: visión · socios · inversionistas · estrategia · valor del proyecto).
- **Repositorio** → `docs/VECTRAX_ASSET_EVIDENCE_MAP.md`
  (uso: documentación viva · historia del sistema · madurez técnica ·
  evidencia de evolución · criterios de validación).
- **Sistema VECTRAX (opcional)** → registrar una estrella documental:
  - `type: strategic_asset`
  - `name: VECTRAX_ASSET_EVIDENCE_MAP`
  - `domain: company / architecture / evidence`
  - `status: living_document`
  - Propósito: que VECTRAX sepa que este documento es un **mapa de evidencia y
    valor del propio sistema**, no una nota cualquiera.

---

## Riesgos registrados
- **domain dominance / over-gravity** — respuestas arrastradas al dominio
  dominante cuando el eje local pertenece a otro dominio.
  - *Medición requerida:* tasa de respuestas arrastradas al dominio dominante
    cuando el eje local pertenece a otro dominio.
