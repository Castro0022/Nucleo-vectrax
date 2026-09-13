"""
core/operator/read_tool_bridge.py — Puente A (2026-09-13).

Usuario → Intent (determinista) → Capability gate → READ_ONLY Tool →
evidencia → respuesta.

Reutiliza EXCLUSIVAMENTE piezas ya existentes:
  - `core.operator.read_tool_intent.parse_read_file_request` — detección
    determinista, sin LLM decidiendo qué herramienta existe.
  - `core.self_observation.capability_context` — catálogo de capacidades
    (`local_filesystem` ya clasificada como READ_ONLY).
  - `connectors.adapters.local_filesystem.LocalFilesystemConnector` +
    `connectors.engine.get_connection_engine()` — el mismo connector y el
    mismo sandbox (`_safe_path()`) que ya usa `core.operator.perception_bridge`
    para percepción; aquí se reutiliza el patrón de registro, NO se crea un
    segundo lector ni se amplían permisos (solo "read").
  - `core.llm_call.complete` — mismo mecanismo de generación que
    `resolve_self_aware`, usado ÚNICAMENTE para redactar en la voz de
    Vectrax, nunca para decidir hechos.

Regla de fidelidad (no negociable): el LLM puede elegir tono/fluidez, nunca
puede alterar, contradecir o inventar contenido respecto al fragmento
realmente leído. Si no puede componer con fidelidad, se devuelve el
fragmento literal en vez de una narrativa especulativa.

Gateado por `VX_TOOL_BRIDGE_READ_ONLY` (default OFF) y restringido al
creador — ver `core/operator/external_gateway.py`.
"""
from __future__ import annotations

import ast
import logging
import os
import pathlib
from typing import Optional

from core.operator.read_tool_intent import ReadFileRequest, parse_read_file_request

logger = logging.getLogger("vectrax.operator.read_tool_bridge")

_PROJECT_ROOT = str(pathlib.Path(__file__).resolve().parent.parent.parent)
_CONNECTOR_NAME = "local_filesystem"

# Tamaño máximo de fragmento citado al LLM/usuario — evita volcar archivos
# enormes completos; suficiente para docstring + cuerpo de una función típica.
_MAX_SNIPPET_CHARS = 4000


def is_enabled() -> bool:
    """True si el flag `VX_TOOL_BRIDGE_READ_ONLY` está activo. Default OFF."""
    return os.environ.get("VX_TOOL_BRIDGE_READ_ONLY", "").strip().lower() not in (
        "", "0", "false", "off", "no",
    )


# ---------------------------------------------------------------------------
# Capability gate — reutiliza el catálogo, no reimplementa un segundo check.
# ---------------------------------------------------------------------------

def _capability_authorized() -> bool:
    """True solo si `local_filesystem` existe en el catálogo, está saludable,
    autorizada, y clasificada exactamente como READ_ONLY. Determinista, sin
    LLM. Nunca lanza."""
    try:
        from core.self_observation.capability_context import (
            _CAPABILITY_CATALOG, _check_module_health,
            REVERSIBILITY_READ_ONLY, HEALTH_AVAILABLE,
        )
        spec = _CAPABILITY_CATALOG.get(_CONNECTOR_NAME)
        if not spec or spec.get("reversibility") != REVERSIBILITY_READ_ONLY:
            return False
        connected, health, _reason = _check_module_health(
            spec["module"], spec.get("attr"),
        )
        return connected and health == HEALTH_AVAILABLE
    except Exception as exc:
        logger.debug("capability gate check failed (denying): %s", exc)
        return False


# ---------------------------------------------------------------------------
# Registro idempotente del connector — mismo patrón que
# perception_bridge._register_uce_connectors(), reutilizado (no duplicado).
# ---------------------------------------------------------------------------

def _ensure_connector_registered() -> bool:
    """Registra `local_filesystem` en el ConnectionEngine si aún no lo está.
    Permisos SOLO de lectura (`granted_permissions=["read"]`) — nunca se
    otorga "write" desde este puente. Devuelve True si el connector queda
    disponible para leer."""
    try:
        from connectors.engine import get_connection_engine
        from connectors.adapters.local_filesystem import (
            LocalFilesystemConnector, FILESYSTEM_CONTRACT,
        )

        engine = get_connection_engine()
        if _CONNECTOR_NAME not in engine.list_connectors():
            connector = LocalFilesystemConnector(root=_PROJECT_ROOT)
            engine.register(
                connector, FILESYSTEM_CONTRACT, granted_permissions=["read"],
            )
            connector.authenticate({"root": _PROJECT_ROOT})
            logger.info(
                "read_tool_bridge: local_filesystem connector registered | root=%s",
                _PROJECT_ROOT,
            )
        return True
    except Exception as exc:
        logger.warning("read_tool_bridge: connector registration failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Extracción determinista de símbolo (AST, no regex sobre código)
# ---------------------------------------------------------------------------

def _extract_symbol_snippet(source: str, symbol: str) -> Optional[str]:
    """Extrae la definición de `symbol` (función o clase) de `source` vía
    `ast` — determinista, nunca ejecuta el código. Devuelve el texto fuente
    exacto de esa definición (docstring + firma + cuerpo), o None si no se
    encuentra. Nunca lanza."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                start = node.lineno - 1
                end = getattr(node, "end_lineno", None) or (start + 40)
                snippet = "\n".join(lines[start:end])
                return snippet[:_MAX_SNIPPET_CHARS]
    return None


def _file_overview_snippet(source: str) -> str:
    """Resumen determinista de un archivo cuando no se pidió un símbolo
    concreto: primeras líneas + nombres top-level vía `ast`."""
    head = "\n".join(source.splitlines()[:25])
    top_level: list[str] = []
    try:
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                top_level.append(node.name)
    except SyntaxError:
        pass
    symbols = ", ".join(top_level[:20]) if top_level else "(sin símbolos top-level detectados)"
    return f"{head}\n\n[símbolos top-level]: {symbols}"[:_MAX_SNIPPET_CHARS]


# ---------------------------------------------------------------------------
# Composición narrativa con anclaje estricto — la fidelidad manda.
# ---------------------------------------------------------------------------

_GROUNDED_PROMPT_ES = """INSTRUCCIÓN ESTRICTA DE FIDELIDAD.
A continuación hay un fragmento REAL leído directamente de un archivo del propio código de Vectrax. Tu única tarea es describir, en tu voz natural, QUÉ HACE ese fragmento — basándote ÚNICA y EXCLUSIVAMENTE en lo que el fragmento realmente contiene.

PROHIBIDO: inventar comportamiento no presente en el fragmento, contradecirlo, simplificar de forma que distorsione su lógica real, o rellenar huecos con suposiciones.
Si el fragmento es ambiguo o insuficiente para describir el comportamiento con certeza, dilo explícitamente y ofrece el fragmento tal cual en vez de especular. La fidelidad técnica está por encima de sonar fluido.

[ARCHIVO LEÍDO] path={path}
```
{snippet}
```

PREGUNTA: {query}

RESPUESTA (fiel al fragmento, natural, breve):"""


def _compose_grounded_response(path: str, snippet: str, query: str) -> str:
    """Redacta la respuesta final citando el fragmento. Si el LLM no está
    disponible o falla, cae a una cita literal del fragmento — nunca deja la
    pregunta sin evidencia real."""
    prompt = _GROUNDED_PROMPT_ES.format(path=path, snippet=snippet, query=query)
    try:
        from core.llm_call import complete
        result = complete(prompt, temperature=0.2, timeout=15.0)
        if result.ok and result.text:
            return result.text.strip()
    except Exception as exc:
        logger.debug("read_tool_bridge: llm compose failed, falling back: %s", exc)
    # Fallback literal — determinista, siempre fiel por construcción.
    return f"Esto leí en {path}:\n\n{snippet}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def resolve_file_read(content: str, user_id: str = "") -> str:
    """Punto de entrada único de Puente A. Devuelve "" si el patrón no
    matchea, si la capacidad no está autorizada, o si el archivo no puede
    leerse con seguridad — en cualquiera de esos casos el llamador debe caer
    al pipeline normal sin ningún efecto observable. Nunca lanza."""
    request: Optional[ReadFileRequest] = parse_read_file_request(content)
    if request is None:
        return ""

    if not _capability_authorized():
        logger.debug("read_tool_bridge: local_filesystem not authorized READ_ONLY, skipping")
        return ""

    if not _ensure_connector_registered():
        return ""

    try:
        from connectors.engine import get_connection_engine
        engine = get_connection_engine()
        result = engine.read(_CONNECTOR_NAME, {"path": request.path})
    except Exception as exc:
        logger.info("read_tool_bridge: read failed for path=%s: %s", request.path, exc)
        return f"No pude leer {request.path} — {type(exc).__name__}."

    # NormalizedResult: success/data son atributos top-level (ver
    # connectors/normalization/engine.py::normalize() — data=raw.get("data", raw)).
    if not result.success:
        return f"No encuentro o no pude leer {request.path}."

    data = result.data or {}
    source = data.get("content", "")
    if not source:
        return f"{request.path} existe pero está vacío."

    if request.symbol:
        snippet = _extract_symbol_snippet(source, request.symbol)
        if snippet is None:
            return (
                f"Leí {request.path}, pero no encontré una función o clase "
                f"llamada '{request.symbol}' ahí — no voy a inventar qué hace."
            )
        query = f"¿Qué hace {request.symbol}()?"
    else:
        snippet = _file_overview_snippet(source)
        query = f"¿Qué contiene {request.path}?"

    return _compose_grounded_response(request.path, snippet, query)
