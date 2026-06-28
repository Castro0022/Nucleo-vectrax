"""
core/log_redaction.py — Redacción de secretos en logs.
========================================================
Evita que tokens aparezcan en claro en stdout / worker.log / gateway.log.

Caso principal: httpx loguea cada request a nivel INFO con la URL completa, y
las llamadas a Telegram incluyen el bot token en la ruta
(``https://api.telegram.org/bot<TOKEN>/getWebhookInfo``). Eso filtraba el token
en `docker compose logs` y en worker.log.

Estrategia (defensa en profundidad):
  1. Un logging.Filter que enmascara el patrón de token de Telegram (y el valor
     literal de TELEGRAM_BOT_TOKEN si está en el entorno) en CUALQUIER record.
  2. Subir httpx/httpcore a WARNING para cortar el leak en su origen (su log de
     request a nivel INFO es ruido + el vector del leak).

Best-effort: nunca lanza excepción ni rompe el logging.
"""
from __future__ import annotations

import logging
import os
import re

# Token de Telegram dentro de URLs: "bot123456789:AA<token>"
_TELEGRAM_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}")
_TELEGRAM_REDACTED = "bot<REDACTED>"


class RedactionFilter(logging.Filter):
    """Enmascara secretos en cada LogRecord antes de que se emita."""

    def __init__(self) -> None:
        super().__init__()
        # Valor literal de tokens conocidos (si están en el entorno) para
        # redactar también apariciones sin el prefijo 'bot'.
        self._literals = []
        for var in ("TELEGRAM_BOT_TOKEN",):
            val = (os.environ.get(var) or "").strip()
            if val and len(val) >= 20:
                self._literals.append(val)

    def _scrub(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        text = _TELEGRAM_TOKEN_RE.sub(_TELEGRAM_REDACTED, text)
        for lit in self._literals:
            if lit and lit in text:
                text = text.replace(lit, "<REDACTED>")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._scrub(record.msg)
            # Los args se interpolan en el mensaje al formatear; redactarlos
            # evita que un %s reintroduzca el token.
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (self._scrub(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        self._scrub(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            pass
        return True  # nunca descartamos el record, solo lo limpiamos


_FILTER_FLAG = "_vx_redaction_installed"


def install_redaction() -> None:
    """Instala la redacción en el root logger + sus handlers (idempotente).

    Debe llamarse al arrancar cada proceso (worker, core_api, supervisor),
    DESPUÉS de configurar el logging y de cargar .env, para que conozca los
    handlers existentes y el valor del token.
    """
    try:
        root = logging.getLogger()

        # Idempotencia: no duplicar el filtro si ya se instaló.
        if not getattr(root, _FILTER_FLAG, False):
            filt = RedactionFilter()
            root.addFilter(filt)
            # Los records propagados desde loggers hijos (httpx, vectrax.*) NO
            # pasan por los filtros del logger padre, pero SÍ por los de sus
            # handlers: por eso el filtro va también en cada handler del root.
            for h in list(root.handlers):
                h.addFilter(filt)
            setattr(root, _FILTER_FLAG, True)

        # Cortar el leak en su origen: httpx loguea la URL completa a INFO.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    except Exception:
        # El logging jamás debe tumbar el arranque.
        pass
