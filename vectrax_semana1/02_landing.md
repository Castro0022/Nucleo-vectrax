# Vectrax — Landing Page (v2, listo para publicar)

**Objetivo único de la landing:** conversión clic → `@VectraxBot` en Telegram.
**Principio:** cada palabra o mueve al clic, o sale.
**Idioma:** español. Versión EN queda fuera de esta v2 (se agrega Mes 2 cuando haya tráfico EN).

---

## `[HERO]`

### Headline

**Cada lead que se te enfría te cuesta una venta.**

### Subheadline

Vectrax vive en tu Telegram. Registra tus leads, detecta cuándo se están enfriando y te dice el mensaje exacto para reactivarlos.

### CTA (botón grande)

**Empieza gratis →**
*(→ abre t.me/VectraxBot)*

### Microcopy

Sin tarjeta. Sin registros. 30 segundos para tu primer lead.

### Hero visual

Screenshot real del chat con Vectrax mostrando un mensaje proactivo:
> *"Carlos lleva 4 días sin responder. Sugerencia: 'Hola Carlos, ¿tuviste oportunidad de revisar la propuesta del logo?'"*

---

## `[MONEY ANCHOR]` (sección corta, fondo color)

### Headline grande, centrado

**Un solo lead recuperado paga Vectrax por años.**

### Copy breve

$29/mes. Si cierras **una** venta al año que antes se te enfriaba, Vectrax se paga 17 veces. La mayoría cierra más.

---

## `[CÓMO FUNCIONA]` (3 columnas)

### Headline

**3 pasos. 5 minutos. No instalas nada.**

### Columna 1

**Cuéntale tus leads**
*"Carlos, logo, $500, último contacto hoy."*

### Columna 2

**Vectrax recuerda**
Contexto completo de cada lead. Pregúntale cuando quieras.

### Columna 3

**Te habla primero**
Si un lead se enfría, Vectrax te escribe con el mensaje exacto para reactivarlo.

---

## `[DEMO VISUAL]` (sección con 3 screenshots grandes apilados en móvil, en fila en desktop)

### Headline

**Esto es lo que ves en tu Telegram, día a día.**

### Screenshot 1 — *Registrar un lead en 1 línea*

Caption: **Hablas normal. Sin formularios, sin CRM, sin aprender nada nuevo.**

### Screenshot 2 — *Vectrax te escribe a las 9am*

Caption: **"Carlos lleva 4 días callado. Copia esto: 'Hola Carlos, ¿revisaste la propuesta?'"**

### Screenshot 3 — *Tu embudo completo en una pregunta*

Caption: **"¿Qué leads tengo activos?" → lista limpia con estado de cada uno.**

---

## `[PRECIOS]` (3 cards, la del medio destacada)

### Headline

**Gratis para siempre. PRO cuando quieras más.**

### FREE — $0

- 20 mensajes al día
- Memoria persistente
- Búsqueda web

**[Empezar gratis →]**

### PRO — $29/mes *(destacado)*

*o $290/año · **−50% el primer año** para los primeros 10 · código `DESIGN50`*

- Mensajes ilimitados
- **Seguimiento automático de leads**
- **Vectrax te habla primero**
- Memoria completa
- Voz, mapas, datos de mercado
- Export de todo en 1 clic

**[Probar PRO →]**

### TEAM — $99/mes

*o $990/año*

- Todo lo de PRO
- Hasta 5 miembros
- Memoria compartida del equipo
- Soporte prioritario
- Onboarding 1:1

**[Contactar →]**

### Microcopy debajo de los 3 cards

Cancelas cuando quieras. Tus datos siempre exportables. Pago seguro con Stripe.

---

## `[URGENCIA]` (banda delgada, fondo contrastante)

**Solo los primeros 10:** código `DESIGN50` da 50% OFF el primer año de PRO. Quedan **[N]** plazas.

*(El número [N] puede ser estático ("10") los primeros días y volverse contador dinámico cuando tengas ≥ 3 clientes).*

---

## `[FAQ]` (6 preguntas, acordeón)

**¿Necesito tarjeta para empezar?**
No. Gratis, sin tarjeta, sin registro.

**¿Por qué Telegram y no una app?**
Telegram ya lo tienes. Cero instalación. Sincroniza entre tus dispositivos. Menos fricción = más uso.

**¿Quién ve mis datos?**
Solo tú. Cifrados en servidor privado. No entrenamos modelos públicos con tu información. Nunca vendemos datos.

**¿Qué pasa si cancelo?**
Acceso PRO hasta el fin del ciclo pagado. Después vuelves a FREE. Tus datos se conservan. `/export` en cualquier momento.

**¿Funciona en inglés?**
Sí. Español e inglés con la misma calidad.

**¿Es IA?**
Sí, usa modelos de lenguaje por debajo. Tú no tienes que elegir cuál. Actualizamos al mejor según calidad y precio.

---

## `[CTA FINAL]` (banda ancha, color sólido)

### Headline

**El próximo lead que se te enfríe es el último.**

### Subheadline

30 segundos para empezar. Gratis. Sin tarjeta.

### Botón

**Abrir Vectrax en Telegram →**

---

## `[FOOTER]`

**Vectrax** · Tu operador personal.

[Privacidad] · [Términos] · [Soporte]

© 2026 · Hecho por Mario Bravo Castro

---

## Notas de implementación (ship-ready hoy)

1. **Stack sugerido:** Framer, Carrd, o Next.js + Tailwind + Vercel. Si no tienes preferencia: **Framer** te tiene online en < 2 h.
2. **Dominio:** `vectrax.app` (o el que tengas). HTTPS obligatorio.
3. **Botones de CTA (todos los 3 + el del hero):** todos apuntan a `https://t.me/VectraxBot?start=landing`. El parámetro `start=landing` te permite trackear en el bot de dónde vino el usuario.
4. **Analytics:** Plausible.io o GoatCounter (no Google Analytics — más rápido, menos fricción de cookies). Evento único: `click_open_bot`.
5. **OG image (comparte en redes):** 1200×630 px, screenshot del chat con el mensaje proactivo. Título superpuesto: *"Cada lead que se enfría te cuesta una venta."*
6. **Tiempo de carga:** < 2 s en 3G. Compresión WebP de screenshots, `loading="lazy"` en imágenes bajo el hero.
7. **Publicación hoy:** si no tienes diseñador, **Framer** con plantilla "SaaS Landing" + los textos de arriba te deja publicable en 2 h. No la pospongas buscando diseño perfecto. La v1 se itera Semana 3 con data.

---

## Cambios clave vs v1

- Headline cambió de promesa funcional ("nunca vuelvas a perder un lead...") a **costo de no actuar** ("cada lead que se enfría te cuesta una venta").
- Se agregó **sección monetaria de anclaje** (un lead = 17 meses de PRO). Ancla el precio contra el valor.
- Se cortó la sección "[DOLOR]" entera — era relleno. El dolor ya está en el headline.
- Se redujo la copy ~40%. Cada sección hace un trabajo específico y solo uno.
- FAQ pasó de 8 a 6 preguntas — cortadas las que no afectan la decisión de clic.
- Sección de urgencia agregada (plaza limitada del `DESIGN50`) — acelera conversión en visitantes indecisos.
- EN copy se elimina de esta v2: ship en ES primero, EN en Mes 2.

*Revisión sugerida: 1 lectura en móvil antes de publicar. Si el hero no cabe sin scroll en un iPhone SE, cortar 1 línea más. La v1 sale hoy; la v2 sale cuando tengas 3 testimonios reales.*
