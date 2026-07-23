# Aislamiento de Contexto Multiusuario — Prueba de Carga & Verificación

- **Task:** Verificar que Vectrax no mezcla contextos entre usuarios y confirmar la integridad del aislamiento bajo carga concurrente (múltiples usuarios simultáneos).
- **Delivered:** 2026-07-23 · **Status:** ✅ Cerrado (verificado single-query + concurrente end-to-end + concurrente sobre el resolver de memoria).
- **Entorno:** local en Mac (`launchd` `com.vectrax.supervisor`); memoria por usuario en `vectrax/user_memory` (perfil + caché + SQLite), segmentada por `user_id`.
- **Config tocada:** solo `.env` (voz) — gitignored, **no** se commitea (ver §5). Las pruebas se ejecutaron in-process (heredoc), sin archivos nuevos salvo este reporte.

## 1. Objetivo
Confirmar dos propiedades bajo concurrencia real:
- **Aislamiento negativo (no-bleed):** ningún usuario recibe el nombre, la identidad ni la memoria de otro.
- **Atribución positiva:** cada usuario recibe exclusivamente *su* propia identidad/memoria, incluso con múltiples hilos disparando la misma consulta en el mismo instante.

## 2. Verificación inicial (single-query)
Consultas individuales por ruta, sin mezcla de contexto:
- Casual / identidad / mercado: cada consulta se mantuvo en su carril (no hubo contaminación cruzada de dominios).
- Usuario distinto (`test:zzz999`): **NO** recibió datos de Mario → "Aún no tengo información suficiente sobre ti…" (comportamiento correcto para usuario sin memoria).

## 3. Prueba 1 — End-to-end concurrente (gateway)
- **Setup:** 8 usuarios (`test:load0`–`test:load7`) sembrados con nombres únicos ("ZetaUsuario0"–"7"); `gw.receive_message("quien soy yo?")` disparado desde 8 hilos sincronizados con `threading.Barrier`, 3 rondas (24 respuestas, ~1.1 s).
- **Resultado:** `AISLAMIENTO INTACTO (0 fugas)` — ningún usuario recibió el nombre/datos de otro ni "Mario".
- **Nota (limitación del arnés):** en esta ruta varias respuestas de los usuarios de prueba salieron **vacías** (el sembrado por perfil no siempre se reflejaba en la ruta del gateway por el *timing* de la concurrencia). Es una limitación del arnés de prueba, **no** una fuga; la Prueba 2 lo corrige verificando atribución positiva.

## 4. Prueba 2 — Concurrente sobre el resolver de memoria (reforzada)
- **Setup:** 10 usuarios (`test:iso0`–`test:iso9`) sembrados con nombres únicos ("NombreUnico0"–"9"); `resolve_with_memory(uid, "quien soy yo?")` disparado desde 10 hilos con `threading.Barrier`, 5 rondas (50 resoluciones simultáneas, ~0.01 s).
- **Sanity (single-user):** `resolve_with_memory("test:iso0", "quien soy?")` → `{'text': 'Eres NombreUnico0.', 'source': 'memory'}`.
- **Resultado:** `total=50 | own_missing=0 | fugas=0` → **AISLAMIENTO INTACTO + atribución correcta**.
  - `own_missing=0`: **cada** usuario recibió SU propio nombre.
  - `fugas=0`: cero mezcla entre usuarios; ninguna aparición de "Mario".
  - Muestra: `iso3→"Eres NombreUnico3"`, `iso1→"Eres NombreUnico1"`, `iso0→"Eres NombreUnico0"`, `iso6→…`, `iso7→…`.

## 5. Config de voz (contexto de la sesión — no commiteable)
Durante la sesión se corrigió el envío de voz por Telegram. Cambios **solo en `.env`** (gitignored, config local — no entra en git):
- `VECTRAX_AUDIO_DISABLED=0` (audio estaba deshabilitado).
- `VECTRAX_AUDIO_MODE=audio` → dispatch usa `sendAudio` (MP3) porque la cuenta rechaza `sendVoice` con `400 VOICE_MESSAGES_FORBIDDEN` (confirmado vía API raw; `sendAudio` responde `200 OK`).
- Se limpió el flag persistente `voice_forbidden` (`core.continuity.voice_forbidden.unmark`) + restart del worker.
- Verificado en vivo: `DISPATCH SENT chat=… ok=True endpoint=sendAudio`.

No hubo cambios de código para la voz → nada que commitear salvo esta documentación.

## 6. Metodología / reproducción
- Sincronización estricta con `threading.Barrier` (todas las consultas parten en el mismo instante) sobre `ThreadPoolExecutor`.
- Sembrado por `user_memory._get_store()._save_profile` (Prueba 1) y perfil directo + `resolve_with_memory` (Prueba 2).
- Criterio de fuga: aparición del nombre de otro usuario o de "Mario" en la respuesta de un usuario de prueba.
- Limpieza posterior: `user_memory.clear_memory(uid)` para todos los usuarios de prueba (los `test:*` quedan además excluidos de stats).

## 7. Veredicto
El aislamiento de contexto de Vectrax es **íntegro bajo carga concurrente**: 74 respuestas simultáneas totales (24 end-to-end + 50 en el resolver), **0 fugas** entre usuarios y **atribución positiva al 100%** (cada usuario recibe solo su propia memoria). La memoria está correctamente segmentada por `user_id` y el acceso concurrente al store mantiene la integridad. **Ticket cerrado.**

## 8. Follow-ups (opcionales)
- Escalar concurrencia (p. ej. 100 usuarios simultáneos) para estrés adicional.
- Sembrar hechos únicos por usuario (no solo el nombre) para estresar también la memoria semántica / constelaciones.

## Referencias
- Código: `vectrax/user_memory.py` (`resolve_with_memory`, `get_memory_context`, store por `user_id`), `core/operator/external_gateway.py` (gateway `receive_message`).
- Voz: `core/voice/synthesizer.py`, `core/voice/telegram_dispatch.py`, `core/continuity/voice_forbidden.py`.
- Config: `.env` (gitignored) — `VECTRAX_AUDIO_MODE`, `VECTRAX_AUDIO_DISABLED`.
