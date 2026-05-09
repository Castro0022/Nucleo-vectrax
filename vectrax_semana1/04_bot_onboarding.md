# Vectrax — Onboarding del Bot (v2, listo para código)

**Para:** implementar en `vectrax/telegram_gateway.py` (handler `/start` línea 638) + `core/proactive_engine.py`.
**Principio v2:** cada mensaje del bot o registra un lead, o empuja al upgrade. Nada decorativo.
**Resultado esperado:** usuario nuevo registra su primer lead en < 3 minutos desde `/start`.

---

## 1 · `/start` (primera vez)

Detección: no existe en `user_memory.db`. Guarda `first_contact_at = now()`.

### Mensaje único (sin segunda parte)

```
Hola, soy Vectrax. Hago una cosa bien: no dejo que tus leads
se enfríen.

Cuéntame de uno. Así:

"Carlos es un lead para logo. Presupuesto $500. Último
contacto: hoy."

Lo guardo y te aviso cuando necesite seguimiento.
```

**Por qué así:** un solo mensaje, una sola acción pedida, un ejemplo único que es literalmente lo que el usuario debe copiar. Cero explicación de tiers, cero "bienvenido al futuro de...".

---

## 2 · Respuesta al primer lead registrado

Disparador: el primer mensaje después de `/start` contiene patrón *"lead"* + nombre propio + cifra o servicio.

```
Lead guardado: [Nombre].

Te escribo cuando se enfríe. Mientras tanto, carga los que
tengas activos — 1 línea por cada uno.
```

**Por qué así:** confirma sin celebrar. Invita al siguiente lead sin bloquear. Si el usuario carga 3 leads en los primeros 5 min, la probabilidad de retención D7 se multiplica.

### Si el primer mensaje **no** es un lead (usuario dice otra cosa)

```
Anotado.

Para ver el valor de Vectrax en los próximos 3 días, necesito
al menos un lead cargado. Prueba con uno real:

"[Nombre] es un lead para [qué]. Último contacto: [cuándo]."
```

---

## 3 · Proactivos automáticos

Disparados por `core/proactive_engine.py` (cooldown global 6 h, ya implementado). Todos respetan el flag `onboarding_step_X_sent` para no repetirse.

### Día 1 · Fin del día (si usuario envió ≥1 mensaje pero NO cargó lead)

```
Cierre del día.

Si mañana quieres ver Vectrax en acción, carga un lead real.
En 3-4 días vas a recibir el primer aviso de seguimiento y
ahí es donde esto empieza a pagar solo.
```

### Día 3 · Primer check-in (usuario con ≥1 lead cargado)

```
Check: [Nombre del lead más antiguo] lleva X días sin actividad.

¿Le escribiste o te sugiero cómo retomar?
```

### Día 3 · Check-in (usuario activo SIN leads aún)

```
Llevas 3 días hablando conmigo sin cargar un lead.

Vectrax vale la pena cuando le pasas leads reales y te aviso
de seguimientos. Sin eso, soy un chat más.

¿Cargas uno ahora? "[Nombre] es un lead para [qué]."
```

### Día 3 · Nudge (usuario que NO volvió)

```
Hola. ¿Todo bien?

Si Vectrax no te convenció el primer día, me gustaría saber
por qué en 1 línea. Solo responde "no me sirvió porque [razón]".
Me ayuda a mejorarlo.
```

### Día 7 · Upgrade nudge (FREE con uso ≥ 70% de cuota diaria ≥ 3 días)

```
Esta semana usaste Vectrax [N] de 7 días.

PRO ($29/mes) te da:
• Mensajes ilimitados
• Seguimiento automático de leads
• Memoria completa

Un solo lead recuperado cubre 17 meses. Los primeros 10
design partners tienen 50% el primer año: código DESIGN50.

/upgrade para el link.
```

### Día 7 · Upgrade nudge (FREE con ≥2 leads cargados, independiente de uso)

```
Tienes [N] leads cargados. En FREE solo los veo, no les
hago seguimiento automático.

Para que Vectrax te avise cuándo y cómo reactivar cada uno:
PRO $29/mes. Se paga con UN lead recuperado al año.

/upgrade
```

### Día 14 · NPS (solo usuarios activos)

```
Pregunta de 1 línea: del 0 al 10, ¿qué tan probable es que
recomiendes Vectrax a alguien que conoces?

Solo el número. Si quieres, luego el porqué.
```

---

## 4 · Comandos

### `/help`

```
Lo que puedo hacer:

/upgrade  — PRO o TEAM
/status   — tu uso y tu tier
/export   — (PRO+) descarga tu historial
/cancel   — cancelar suscripción
/borrar   — borrar toda tu memoria

Pero lo mejor es hablarme normal. Soy tu asistente, no un CLI.
```

### `/status`

Dinámico, rellenar valores:

```
Tier: [FREE/PRO/TEAM]
Mensajes hoy: [N] / [límite]
Leads activos: [N]
Miembro desde: [fecha]

[Si FREE y uso ≥ 70%: "Cerca del límite diario. /upgrade quita el tope."]
[Si FREE y ≥ 2 leads: "Tus leads no tienen seguimiento automático en FREE. /upgrade lo activa."]
[Si PRO: "Sin límites. Todo activo."]
```

### `/upgrade`

```
Vectrax PRO — $29/mes o $290/año (−17%).

• Mensajes ilimitados
• Seguimiento automático de leads
• Vectrax te habla primero
• Memoria completa
• Export en 1 clic

Código DESIGN50: 50% el primer año (primeros 10).

Link seguro: [URL]

Activación inmediata después del pago.
```

### Post-pago (webhook Stripe dispara esto)

```
PRO activado. Ya está.

Desde este momento:
✓ Mensajes ilimitados
✓ Seguimiento automático de tus leads
✓ Memoria completa
✓ /export disponible

Si quieres, empecemos fuerte: pásame tus leads activos y
los proceso ahora.
```

### `/borrar` (confirmación)

```
Esto borra TODO: conversaciones, leads, memoria, config.
Irreversible.

Confirmar: escribe exactamente CONFIRMO BORRAR

Cualquier otra cosa cancela.
```

### `/cancel`

```
Entiendo.

Antes de cancelar: ¿en 1 frase, qué no te sirvió? Me ayuda.

Para confirmar: escribe CANCELAR PRO.

Tu acceso PRO sigue hasta [fecha_fin_ciclo]. Después vuelves
a FREE. Datos conservados.
```

---

## 5 · Casos borde

### Usuario excede cuota FREE del día

```
20/20 mensajes hoy en FREE. Mañana 00:00 se renueva.

O /upgrade quita el tope ahora. $29/mes, pagado con un solo
lead recuperado.
```

### Error interno del bot

```
Algo falló de mi lado. No fuiste tú.

Reintenta en 30 segundos. Si pasa de nuevo, mándame
"bug: [qué pasó]" y llega a mi equipo.
```

### Usuario pide función PRO estando en FREE

```
Eso es PRO: [feature].

$29/mes, activación en 30 segundos, se paga con un lead
recuperado. /upgrade
```

---

## 6 · Mapeo a código

**`vectrax/telegram_gateway.py`** línea 637 (`# === /start — siempre muestra bienvenida ===`)
→ reemplazar cuerpo con Mensaje único de sección 1.

**`core/proactive_engine.py`** — añadir eventos:
- `ONBOARDING_DAY1_NO_LEAD` — disparar fin día 1 si `leads_count == 0`.
- `ONBOARDING_DAY3_CHECKIN_WITH_LEADS`
- `ONBOARDING_DAY3_CHECKIN_NO_LEADS`
- `ONBOARDING_DAY3_NUDGE_INACTIVE`
- `ONBOARDING_DAY7_UPGRADE_USAGE`
- `ONBOARDING_DAY7_UPGRADE_LEADS`
- `ONBOARDING_DAY14_NPS`

Cada uno: trigger en `user_memory.db`, flag `step_sent` para idempotencia, log a `observability/event_schema` con `Severity.INFO`.

**`services/core/routes/billing.py`** — el webhook `checkout.session.completed` dispara:
1. `set_tier(user_id, 'pro')` en `user_memory.db`.
2. Envía mensaje post-pago vía `telegram_gateway.send_message(user_id, POST_PAGO_TEXT)`.
3. Escribe en `vault/leads.db → lead_activities` un evento `tier_upgrade` *(también resuelve el hallazgo del inventario v0.2: `lead_activities` vacía).*

---

## 7 · Orden de implementación en Semana 1

| Día | Ítem | Tiempo |
|---|---|---|
| D2 | Reemplazar `/start` + respuesta al primer lead | 40 min |
| D2 | `/help`, `/status`, mensaje de `/upgrade` + post-pago | 1 h |
| D3 | Proactivo Día 1 + Día 3 (ambas ramas con/sin lead) | 1 h |
| D4 | Proactivo Día 7 upgrade (usage + leads) | 45 min |
| D5 | Casos borde (cuota, error, feature gateada) | 30 min |
| D6 | Pruebas internas: tú como usuario nuevo desde cero | 1 h |
| D7 | Ajustes post-test | 30 min |

**Total:** ~5.5 h de código. Sobre `proactive_engine` existente, sin infraestructura nueva.

---

## 8 · Qué NO incluir en v2

- **Menús con botones inline** (`InlineKeyboardMarkup`): fricción visual, conversión marginal. Mes 2.
- **Emojis**: uno por mensaje máximo, y solo si reemplaza una palabra. Tono profesional-directo, no emoji-ridden.
- **Traducción a inglés**: primeros 5-10 clientes son hispano-hablantes. EN llega cuando haya tráfico EN real.
- **Animaciones "escribiendo…"** artificiales: el bot responde rápido porque es rápido.
- **Formularios / pedir email, empresa, rol**: cero. Si algo es necesario, se pide cuando se necesita.
- **Gamificación** (streaks, badges, puntos): matan el tono profesional.

---

## Cambios clave vs v1

- `/start` pasó de 2 mensajes (bienvenida + 3 ejemplos + FREE tier) a **1 mensaje con 1 acción pedida** (cargar un lead con formato exacto). Reduce fricción a menos de 30 segundos para primera acción de valor.
- Eliminado Mensaje 3 (recordatorio de "¿ahí andas?" a los 10 min). Se sentía automatizado y pushy. La nueva estrategia es confiar en los proactivos Día 1+.
- Proactivos Día 3 ahora tienen **3 ramas** en vez de 2, según estado real del usuario (con lead vs sin lead vs inactivo). Cada rama lleva a una acción específica.
- Upgrade nudges Día 7 ahora **incluyen la matemática monetaria** ("un lead recuperado cubre 17 meses"). Ancla el precio al valor.
- Post-pago mensaje redujo celebración, aumentó call-to-action ("pásame tus leads activos, los proceso ahora").
- Total mensajes: v1 tenía ~22, v2 tiene ~17. Más carga en menos mensajes.

---

*Fin del onboarding v2. Cada texto es copy-paste directo al código. Si algo suena raro al leerlo en voz alta, cámbialo antes de shippearlo.*
