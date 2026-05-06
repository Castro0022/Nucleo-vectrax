"""
core/gravity/gravity_engine.py — Gate de entrada al vector store.

Función pública: should_use_deep_memory(message) -> bool.

Si devuelve False, el Router NO debe tocar la deep_memory.
Si devuelve True, vale la pena calcular embedding y consultar.

Esto evita pegarle a la DB con cada 'hola' del usuario, ahorra cómputo
y latencia, y mantiene el comportamiento conversacional ligero.

Señales que activan deep_memory (en orden de fuerza):

  1. PREGUNTAS EXPLÍCITAS DE MEMORIA
     - 'te acuerdas / acordás', 'recuerdas / recordás'
     - 'la otra vez', 'el otro día', 'aquella vez'
     - 'remember', 'do you remember'

  2. REFERENCIAS TEMPORALES PASADAS
     - 'ayer', 'anteayer', 'la semana pasada', 'hace un rato'
     - 'yesterday', 'last week', 'the other day'

  3. NOMBRES PROPIOS REGISTRADOS POR EL USER
     - face_memory devuelve nombres conocidos (Naomy, Mario, ...)
     - mención literal con mayúscula inicial: 'Naomy'

  4. INTENTS DE CONTINUIDAD CONVERSACIONAL
     - 'lo de', 'eso que dijiste', 'volver a'

  5. PRONOMBRES DEÍCTICOS DE REFERENCIA
     - 'aquello', 'eso del otro día', 'esa foto'

Cualquier match dispara la consulta. La heurística es deliberadamente
conservadora: prefiere FALSO POSITIVO (consulta gratuita) sobre FALSO
NEGATIVO (perder un recuerdo cuando el user lo invoca).

API:
    should_use_deep_memory(message, user_id=None, known_names=None) -> bool

`user_id` y `known_names` son opcionales — si se pasan, el gate puede
levantar la señal cuando el mensaje menciona a un contacto registrado
del propio user (face_memory), sin filtrar a otros usuarios.

Para el Router:

    from core.gravity import should_use_deep_memory, SQLiteVectorStore

    if should_use_deep_memory(message, user_id, known_names=face_names):
        store = SQLiteVectorStore()
        results = store.query(user_id, embedding, limit=5)
        ...
    else:
        # respuesta ligera, sin tocar la DB
        ...
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

logger = logging.getLogger("vectrax.gravity.engine")


# ===========================================================================
# Señales declarativas
# ===========================================================================

# Frases explícitas de pedido de memoria
_MEMORY_VERBS = re.compile(
    r"(?:"
    # ES
    r"\bte\s+acuerda(?:s|\u0301s)?\b"
    r"|\b(?:recuerd|acuerd)(?:as|a|an|en|o|amos)\b"
    r"|\bme\s+acuerdo\b"
    r"|\b(?:la|aquella|esa)\s+(?:otra\s+)?(?:vez|noche|tarde|ma\u00f1ana|d\u00eda)\b"
    r"|\bel\s+otro\s+d\u00eda\b"
    r"|\baquella\s+vez\b"
    # EN
    r"|\bremember\b"
    r"|\bdo\s+you\s+(?:still\s+)?remember\b"
    r"|\bthat\s+time\b"
    r"|\bthe\s+other\s+(?:day|night|time)\b"
    r")",
    re.IGNORECASE,
)

# Referencias temporales al pasado
_TEMPORAL_PAST = re.compile(
    r"(?:"
    # ES
    r"\bayer\b|\banteayer\b|\banoche\b|\banteanoche\b"
    r"|\b(?:la|el)\s+(?:semana|mes|a\u00f1o)\s+pasad[oa]\b"
    r"|\bhace\s+(?:un\s+)?(?:rato|d\u00eda|d\u00edas|semana|semanas|mes|meses|a\u00f1o|a\u00f1os|tiempo)\b"
    r"|\b(?:el|la)\s+(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+pasad[oa]?\b"
    # EN
    r"|\byesterday\b|\blast\s+(?:week|month|year|night)\b"
    r"|\b(?:a\s+)?(?:few|couple\s+of)\s+(?:days|weeks|months)\s+ago\b"
    r")",
    re.IGNORECASE,
)

# Continuidad conversacional / referencia indirecta
_CONTINUITY = re.compile(
    r"(?:"
    # ES
    r"\blo\s+de\s+(?:ayer|anoche|la\s+otra|aquella)\b"
    r"|\beso\s+que\s+(?:dijiste|hablamos|me\s+contaste)\b"
    r"|\bvolver\s+a\s+\w+\b"
    r"|\baquello\s+(?:que|de|sobre)\b"
    r"|\besa\s+(?:foto|conversaci[o\u00f3]n|charla|cosa)\b"
    # EN
    r"|\bwhat\s+you\s+said\s+about\b"
    r"|\bgo\s+back\s+to\b"
    r")",
    re.IGNORECASE,
)

# Pool combinado para el `DEEP_MEMORY_SIGNALS` exportable.
DEEP_MEMORY_SIGNALS: tuple = (
    "te acuerdas", "recuerdas", "te acordás", "el otro día",
    "la otra vez", "aquella vez", "ayer", "anoche", "la semana pasada",
    "hace un rato", "lo de ayer", "eso que dijiste",
    "remember", "the other day", "yesterday", "last week",
)


# ===========================================================================
# Heurísticas adicionales
# ===========================================================================

# Nombre propio: token que empieza con mayúscula (Unicode-aware), longitud
# >= 3, no es una palabra común inicial. Lo usamos solo si NO hay match
# de las regex anteriores (para evitar saltos sobre 'Hola').
_PROPER_NAME_RE = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b")

# Palabras que arrancan con mayúscula porque son inicio de oración pero
# no son nombres propios — lista corta para descartar comunes.
_NOT_A_NAME = frozenset({
    "Hola", "Hey", "Buenas", "Buenos", "Bueno", "Gracias",
    "Por", "Para", "Como", "Cómo", "Que", "Qué",
    "Cual", "Cuál", "Donde", "Dónde", "Cuando", "Cuándo",
    "Si", "Sí", "No", "Ok", "Okay",
    "Hi", "Hello", "Yes", "No", "Ok", "Hey",
    "Vectrax",
})


def _has_proper_name(message: str, known_names: Optional[Iterable[str]] = None) -> bool:
    """True si el mensaje menciona un nombre propio plausible.

    Si se pasa `known_names` (lista de contactos registrados por el user
    via face_memory), un match en cualquiera dispara True directamente —
    es señal fuerte. Si no, fallback a heurística de mayúscula inicial.
    """
    if known_names:
        msg_lower = message.lower()
        for n in known_names:
            if not n:
                continue
            first = (n.split()[0] if isinstance(n, str) else "").lower()
            if first and len(first) >= 3 and first in msg_lower:
                return True

    # Heurística de mayúscula inicial (defensiva, sin face_memory)
    for tok in _PROPER_NAME_RE.findall(message or ""):
        if tok in _NOT_A_NAME:
            continue
        return True
    return False


# ===========================================================================
# Gate público
# ===========================================================================

def should_use_deep_memory(
    message: str,
    user_id: Optional[str] = None,
    known_names: Optional[Iterable[str]] = None,
) -> bool:
    """¿Vale la pena tocar la deep_memory para este mensaje?

    True si hay UNA o más señales:
      - verbos/frases explícitas de memoria
      - referencias temporales al pasado
      - continuidad conversacional / deícticos
      - mención de un nombre propio (especialmente si está en
        known_names del propio user)

    False en cualquier otro caso. El Router NO debe tocar la DB.
    """
    if not message or not message.strip():
        return False

    if _MEMORY_VERBS.search(message):
        return True
    if _TEMPORAL_PAST.search(message):
        return True
    if _CONTINUITY.search(message):
        return True
    if _has_proper_name(message, known_names=known_names):
        return True

    return False


# ===========================================================================
# Router de conveniencia
# ===========================================================================

class DeepMemoryRouter:
    """Wrapper que combina el gate con el SQLiteVectorStore.

    Uso:
        router = DeepMemoryRouter(store, embed_fn)
        results = router.route(user_id, message)
        # results = [] si el gate bloqueó, sin tocar la DB.

    `embed_fn(text) -> list[float]` se invoca SOLO si el gate pasa.
    Por eso pegarle a un proveedor de embeddings (OpenAI, local) no
    cuesta nada cuando el mensaje es trivial.
    """

    def __init__(
        self,
        store,
        embed_fn,
        get_known_names_fn=None,
    ) -> None:
        self.store = store
        self.embed_fn = embed_fn
        self.get_known_names_fn = get_known_names_fn

    def route(
        self,
        user_id: str,
        message: str,
        limit: int = 5,
    ) -> List[dict]:
        if not user_id or not message:
            return []
        known: Optional[List[str]] = None
        if self.get_known_names_fn:
            try:
                known = list(self.get_known_names_fn(user_id) or [])
            except Exception:
                known = None
        if not should_use_deep_memory(message, user_id, known_names=known):
            logger.debug(
                "gravity_engine: skip deep_memory for %s (no signals)",
                user_id,
            )
            return []
        try:
            embedding = self.embed_fn(message)
        except Exception as exc:
            logger.warning("gravity_engine: embed failed: %s", exc)
            return []
        if not embedding:
            return []
        return self.store.query(user_id, embedding, limit=limit)
