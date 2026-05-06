"""
core/sovereignty — User Sovereignty Engine.

Motor declarativo que regula CUÁLQUIER salida del sistema al usuario.

Principio: el usuario es soberano sobre los mensajes que recibe. Ningún
componente del sistema puede mandarle un mensaje a un usuario sin pasar
por la autoridad central. Default deny para razones no solicitadas
explícitamente.

Esto cierra estructuralmente la clase de bug:
  "el sistema envía mensajes proactivos / follow-ups / alertas que el
  usuario no pidió — el usuario lo percibe como ruido o como 'el bot
  habla del anterior'".

Componentes:
  - policy:     SendReason + ConsentType + reglas (qué razones requieren
                opt-in, qué razones son siempre permitidas, qué razones
                son admin-only).
  - consent:    registro persistente de opt-ins por usuario.
  - ledger:     audit log inmutable de cada envío (permitido y bloqueado).
  - authorizer: require_authorization(chat_id, reason) → Decision.
  - decorator:  @authorized_send para envolver cualquier sender.

Modos operativos (env: SOVEREIGNTY_MODE):
  observe (default): autoriza pero NO bloquea. Loggea decisiones
                     para auditar comportamiento real antes de enforce.
  enforce:           bloquea envíos no autorizados. Production-ready.
  off:               bypass total (testing/diag).

Diseñado para millones de usuarios. Thread-safe, SQLite-backed.

Creador: Mario Bravo Castro
Fecha:   2026-05-06
"""

from core.sovereignty.policy import (  # noqa: F401
    SendReason,
    ConsentType,
    Decision,
    DecisionStatus,
    ALWAYS_ALLOWED_REASONS,
    OPT_IN_REQUIRED_REASONS,
    ADMIN_ONLY_REASONS,
)
from core.sovereignty.consent import (  # noqa: F401
    grant_consent,
    revoke_consent,
    has_consent,
    list_consents,
)
from core.sovereignty.ledger import (  # noqa: F401
    record_decision,
    recent_decisions,
    audit_stats,
)
from core.sovereignty.authorizer import (  # noqa: F401
    require_authorization,
    sovereignty_mode,
)
from core.sovereignty.decorator import authorized_send  # noqa: F401

__all__ = [
    "SendReason",
    "ConsentType",
    "Decision",
    "DecisionStatus",
    "ALWAYS_ALLOWED_REASONS",
    "OPT_IN_REQUIRED_REASONS",
    "ADMIN_ONLY_REASONS",
    "grant_consent",
    "revoke_consent",
    "has_consent",
    "list_consents",
    "record_decision",
    "recent_decisions",
    "audit_stats",
    "require_authorization",
    "sovereignty_mode",
    "authorized_send",
]
