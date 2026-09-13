"""
core/operator/read_tool_intent.py — Detección determinista de intención de
lectura de archivo (Puente A, 2026-09-13).

Este módulo NO es un clasificador de propósito general ni decide qué
herramienta existe: solo reconoce el patrón explícito "abre/lee/muéstrame
<path> ... [qué hace <símbolo>]" y extrae, de forma determinista, la ruta
relativa y (opcional) el nombre de función/clase pedido. La selección de la
herramienta (`local_filesystem.read`) ya está fija en el código que consume
esto — el LLM nunca participa de esta decisión.

Fuera de alcance: cualquier operación de escritura, cualquier ruta fuera del
sandbox del repo, cualquier extensión de archivo no textual.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Extensiones de texto permitidas — nunca binarios (.db, .png, .pyc, etc.).
# Deliberadamente pequeña y explícita.
ALLOWED_TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini",
})

# Patrón: verbo de apertura + ruta. Se captura el TOKEN COMPLETO delimitado
# por espacios/comillas (sin embeber la extensión dentro del grupo de
# captura) — la validación de extensión ocurre después, en Python, contra el
# string completo. Hacerlo dentro de la regex con alternancia permitiría que
# el backtracking recorte rutas con extensión compuesta (p.ej.
# "archivo.json.tmp" matchearía como "archivo.json", ignorando el ".tmp"
# real al final) — un token completo + chequeo estricto en Python lo evita.
_OPEN_VERB = r"(?:abre|abrir|lee|leer|mu[eé]strame|muestra|ver|open|read|show\s+me)"
_PATH_CHARS = r"[\w./\-]+"

_READ_FILE_RE = re.compile(
    rf"{_OPEN_VERB}\s+[`\"']?({_PATH_CHARS})[`\"']?",
    re.IGNORECASE,
)

# Patrón opcional para el símbolo (función/clase) preguntado, en la MISMA
# oración/mensaje. "qué hace X()" / "what does X() do" / "explica X()".
_SYMBOL_RE = re.compile(
    r"(?:qu[eé]\s+hace|explica(?:me)?|what\s+does|explain)\s+[`\"']?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(?\)?[`\"']?"
    r"(?:\s+(?:hace|do))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReadFileRequest:
    """Resultado determinista del parser. `path` siempre viene relativo
    (nunca absoluto) — la validación de sandbox real ocurre después, en
    `LocalFilesystemConnector._safe_path()` (reutilizada, no reimplementada
    aquí)."""
    path: str
    symbol: Optional[str] = None


def _is_allowed_extension(path: str) -> bool:
    lowered = path.lower()
    return any(lowered.endswith(ext) for ext in ALLOWED_TEXT_EXTENSIONS)


def parse_read_file_request(text: str) -> Optional[ReadFileRequest]:
    """Detecta el patrón "abre/lee <path> [y dime qué hace <symbol>]".

    Devuelve `None` si el mensaje no matchea con confianza alta — en ese
    caso el llamador NO debe activar nada del Puente A y debe caer al
    pipeline normal, sin ningún cambio de comportamiento. Nunca lanza.
    """
    if not text:
        return None
    try:
        match = _READ_FILE_RE.search(text)
        if not match:
            return None
        # rstrip('.') defensivo: un punto final de oración ("...system_monitor.py.")
        # nunca es parte de una extensión real — ninguna extensión válida termina
        # en '.'. No afecta rutas legítimas (siempre delimitadas por espacio/comilla
        # antes de este punto en la práctica).
        path = match.group(1).strip().strip("`\"'").rstrip(".")
        if not path or not _is_allowed_extension(path):
            return None
        # Rechazo temprano de rutas absolutas o con traversal obvio — la
        # validación real y autoritativa sigue siendo _safe_path(), esto
        # es solo una señal de confianza adicional para el parser.
        if path.startswith("/") or path.startswith("~") or ".." in path:
            return None

        symbol: Optional[str] = None
        sym_match = _SYMBOL_RE.search(text)
        if sym_match:
            symbol = sym_match.group(1).strip()

        return ReadFileRequest(path=path, symbol=symbol)
    except Exception:
        return None
