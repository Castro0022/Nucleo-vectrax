# Bug Report — Truncado silencioso del bloque cognitivo en self_summary
**ID:** BUG-SUMMARY-001  
**Severidad:** Alta (impacto directo en calidad de respuestas del LLM)  
**Estado:** ✅ RESUELTO  
**Detectado:** 2026-05-22 07:37 UTC (prueba de estrés en producción)  
**Resuelto:** 2026-05-22 07:42 UTC  
**Commit fix:** `b449e56`

---

## Síntoma observado

Vectrax respondía preguntas introspectivas con lenguaje genérico de consultoría
a pesar de que el bloque `[NÚCLEO COGNITIVO]` había sido implementado con datos
reales de Observer, Learner, Router y Governor.

Ejemplo de respuesta incorrecta que desencadenó la investigación:
> _"En los flujos de datos actuales, percibo coherencia en las interacciones
> de los usuarios... para dar sentido a este universo deberíamos desplegar
> un motor de análisis de datos..."_

La prueba de estrés confirmó el bug cuantitativamente: **0/20** generaciones
de `compose_self_summary_for_prompt()` contenían los datos del módulo.

---

## Causa raíz

### Código con el bug (`compose_self_summary_for_prompt`)

```python
# ANTES — orden incorrecto
parts = []

s = compose_self_summary()        # → ~1170 chars (reflexión operacional)
if s:
    parts.append(s)

module_state = _collect_module_state()  # → ~186 chars (Observer/Router/etc.)
if module_state:
    parts.append(module_state)

out = "\n\n".join(parts)          # → ~1358 chars
# max_chars = 1200 (default)
# 1358 > 1200 → se trunca
cut = out[:1200]                  # corta en medio del texto
last_nl = cut.rfind("\n")         # último \n antes del corte → dentro de s
return cut[:last_nl]              # retorna solo s, nunca module_state
```

**El problema:** `compose_self_summary()` generaba ~1170 chars — casi el límite
de 1200. Cuando se añadía `module_state` (~186 chars) el total superaba el límite.
El truncado resultante eliminaba el bloque cognitivo por completo porque
aparecía al final y `rfind("\n")` encontraba el último salto de línea dentro de
la reflexión operacional, antes del bloque cognitivo.

### Por qué no se detectó antes

El bug era **silencioso**: `compose_self_summary_for_prompt()` retornaba un
string válido sin errores. Los tests de `test_module_context_injection.py`
pasaban en entorno local porque `compose_self_summary()` era más corto localmente
(menos commits, menos módulos registrados). En producción, con más datos en
`reflect_now()`, el output de `compose_self_summary()` superaba el umbral.

---

## Fix aplicado

### Principio del fix

Calcular el bloque de módulos cognitivos **primero** y **reservar su espacio**
del presupuesto total antes de pedir la reflexión operacional. Así el bloque
en tiempo real nunca puede ser desplazado.

### Código corregido

```python
# DESPUÉS — bloque cognitivo tiene prioridad garantizada
def compose_self_summary_for_prompt(max_chars: int = 1200) -> str:
    # 1. Calcular módulos PRIMERO para reservar su espacio
    module_state = ""
    try:
        module_state = _collect_module_state()
    except Exception:
        pass

    # 2. Reservar espacio del presupuesto para el bloque cognitivo
    module_budget = len(module_state) + 2 if module_state else 0
    reflection_budget = max(200, max_chars - module_budget)

    # 3. Truncar reflexión operacional al presupuesto restante
    s = compose_self_summary()
    if s and len(s) > reflection_budget:
        cut = s[:reflection_budget]
        last_nl = cut.rfind("\n")
        s = cut[:last_nl].rstrip() if last_nl > reflection_budget // 2 else cut.rstrip()

    # 4. Combinar: reflexión truncada + módulos completos
    parts = [p for p in [s, module_state] if p]
    if not parts:
        return ""

    out = "\n\n".join(parts)
    # Truncado final de seguridad (no debería activarse)
    if len(out) <= max_chars:
        return out
    cut = out[:max_chars]
    last_nl = cut.rfind("\n")
    return cut[:last_nl].rstrip() if last_nl > max_chars // 2 else cut.rstrip()
```

### También corregido: typo en encabezado

```
# ANTES
"[NÚCALEO COGNITIVO — estado de módulos en tiempo real]"
# DESPUÉS
"[NÚCLEO COGNITIVO — estado de módulos en tiempo real]"
```

---

## Verificación del fix

### Local (antes del deploy)
```
_collect_module_state() → Observer + Learner + Router + Governor presentes
compose_self_summary_for_prompt():
  len=1195 (dentro de 1200)
  'Observer' in output: True
  'Router'   in output: True
33/33 tests integration PASS
```

### Producción (post-deploy `b449e56`)
```
self_summary: 20/20 con módulos | avg=110ms p95=151ms
Bloque final (últimas 5 líneas):
  Observer:   mode=ACTIVE enforced=True evaluadas=0
  Learner:    phase=observe outcomes=0 recomendaciones=0
  Router:     últimos=200 regex_fallback=47% intent_top=online
    conflicto: memory→online x88
  Governor:   mode=act risk=LOW streak=18
```

---

## Impacto del fix

**Antes del fix:** El LLM nunca recibía datos reales de Observer, Router ni Learner.
Respondía preguntas introspectivas desde su base de entrenamiento → lenguaje genérico.

**Después del fix:** El LLM recibe en cada mensaje del creador:
```
[PERCEPCIÓN OPERACIONAL — núcleo Vectrax] · branch=main head=b449e56
Estabilidad: ...
Modos: ...
Hoy cambió: ...

[NÚCLEO COGNITIVO — estado de módulos en tiempo real]
Observer:   mode=ACTIVE enforced=True evaluadas=N
Learner:    phase=observe outcomes=N recomendaciones=0
Router:     últimos=200 regex_fallback=X% intent_top=memory
Governor:   mode=act risk=LOW streak=N
```

---

## Tests relacionados

`tests/integration/test_module_context_injection.py` — casos relevantes:

- `TestComposeSelfSummaryForPrompt::test_incluye_bloque_nucleo_cognitivo`
- `TestComposeSelfSummaryForPrompt::test_respeta_max_chars`
- `TestComposeSelfSummaryForPrompt::test_datos_numericos_reales`
- `TestCreatorContextIntrospection::test_compose_creator_context_con_percepcion`

---

## Lecciones aprendidas

1. **Tests de entorno local ≠ comportamiento en producción** cuando el tamaño de
   datos variables (commits, módulos registrados) afecta al output de funciones.
2. **Orden de composición importa** cuando hay presupuesto de caracteres. El dato
   más crítico (tiempo real) debe reservar su espacio antes que el histórico.
3. **Los bugs silenciosos son los más peligrosos.** Este devolvía un resultado
   válido sin error, pero incompleto. Solo la prueba de estrés cuantitativa lo expuso.

---

*Vectrax — Núcleo Cognitivo. Creado por Mario Bravo Castro.*  
*Bug cerrado: 2026-05-22 07:42 UTC*
