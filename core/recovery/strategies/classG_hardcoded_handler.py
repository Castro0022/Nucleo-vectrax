"""
Clase G strategy — propuesta de refactor cuando se detecta handler hardcoded.

Cuándo dispara
--------------
El detector runtime `hardcoded_handler_runtime` emite BROKEN cuando el mismo
hash de respuesta se repite por encima del umbral (default 5 ocurrencias en
3600s) con al menos N usuarios distintos (default 3) recibiendo el mismo
texto literal.

Precondition guard
------------------
Solo disparamos si la evidencia incluye EXPLÍCITAMENTE `top_hash` y
`top_count`. Esto evita ejecutar sobre estados transitorios donde la
evidencia está incompleta.

Action plan
-----------
1. `NotifyHuman` con una propuesta estructurada:
   * Hash y conteo del texto sospechoso
   * Prefijo anonimizado (primeros 60 chars)
   * Número de usuarios distintos afectados
   * Idiomas observados
   * Sugerencia de refactor apuntando a la capa canónica
   * Comando para correr el scanner estático y localizar el callsite exacto

La estrategia NO reescribe código. NO toca `.env`. NO reinicia contenedores.
Emite UN aviso bien formado al canal humano. La corrección queda en manos
del creador siguiendo la ley rectora ("un fallo = un motor, pero el refactor
estructural requiere revisión humana").

Rollback plan
-------------
Vacío — un `NotifyHuman` no tiene efectos reversibles.

Throttle
--------
max_attempts_per_window=1, cooldown_s=3600 → una alerta por hora máximo por
cada evento BROKEN. Evita spam si el detector sigue emitiendo BROKEN mientras
el operador evalúa la propuesta.

Human approval
--------------
False — la acción en sí es solo una notificación; no modifica estado.
"""

from __future__ import annotations

from typing import Any, Dict

from core.recovery.actions import NotifyHuman
from core.recovery.strategies import RecoveryStrategy

STRATEGY_ID = "classG_hardcoded_handler"


def _precondition(evidence: Dict[str, Any]) -> bool:
    """Only fire when the detector provides actionable evidence.

    Required keys:
      - top_hash (str, non-empty)
      - top_count (int, >= detector's duplicate_threshold)
    """
    top_hash = evidence.get("top_hash")
    top_count = evidence.get("top_count")
    if not isinstance(top_hash, str) or not top_hash:
        return False
    if not isinstance(top_count, int) or top_count <= 0:
        return False
    return True


def _build_proposal_message(evidence: Dict[str, Any]) -> str:
    """Construct a human-readable refactor proposal from detector evidence."""
    top_hash = evidence.get("top_hash", "?")
    top_count = evidence.get("top_count", 0)
    unique_users = evidence.get("top_unique_users", 0)
    languages = evidence.get("top_languages") or []
    prefix = evidence.get("top_text_prefix", "")
    length = evidence.get("top_text_length", 0)
    reason = evidence.get("reason", "")

    languages_str = ", ".join(languages) if languages else "(none recorded)"

    return (
        "[classG] HARDCODED HANDLER — REFACTOR PROPOSAL\n"
        f"  detector:     hardcoded_handler_runtime (BROKEN)\n"
        f"  strategy:     {STRATEGY_ID}\n"
        f"  reason:       {reason}\n"
        f"  hash:         {top_hash}\n"
        f"  count:        {top_count} occurrences\n"
        f"  unique_users: {unique_users}\n"
        f"  languages:    {languages_str}\n"
        f"  text_length:  {length} chars\n"
        f"  text_prefix:  {prefix!r}\n"
        "\n"
        "  diagnosis:    The same response text is being returned to\n"
        "                multiple users unchanged. This indicates a handler\n"
        "                that bypasses the cognitive pipeline\n"
        "                (memory + LLM + identity_layer).\n"
        "\n"
        "  action:       1) Run the static scanner to locate the callsite:\n"
        "                   python3 -m core.recovery.static.hardcoded_handler_scan\n"
        "                2) Route the handler through the canonical layer:\n"
        "                   from vectrax.identity_handler import respond_if_identity\n"
        "                3) If the response is intentional (e.g. public API\n"
        "                   blurb), add the file to the scanner whitelist and\n"
        "                   document the exception.\n"
        "\n"
        "  no rollback:  this is a notification only; no state modified."
    )


def build() -> RecoveryStrategy:
    """Factory — constructs the Class G strategy.

    No paths are injected because this strategy has no file side-effects;
    it only emits a NotifyHuman that writes to the alert log.
    """
    # The NotifyHuman message is built statically here with a placeholder
    # that the executor will NOT actually substitute — the strategy must
    # rebuild the message with real evidence at execute time.
    # Since RecoveryStrategy is frozen and Actions are frozen, we embed a
    # templated version that carries the instructions. The live evidence-
    # rendered version is produced by `render_action_plan(evidence)` below
    # for executors that support dynamic action plans.
    #
    # In v1 the executor runs the static plan; the Action's message is a
    # pre-built template mentioning that the concrete evidence is in the
    # recovery_ledger entry for this execution.
    static_message = (
        "[classG] HARDCODED HANDLER detected — see ledger entry for details.\n"
        "  Run `python3 -m core.recovery.static.hardcoded_handler_scan` to\n"
        "  locate offending callsites. Route responses through\n"
        "  `vectrax.identity_handler.respond_if_identity`."
    )
    action_plan = [
        NotifyHuman(message=static_message, channel="file"),
    ]
    return RecoveryStrategy(
        id=STRATEGY_ID,
        trigger_events=["hardcoded_handler_runtime"],
        precondition=_precondition,
        action_plan=action_plan,
        rollback_plan=[],  # notifications are not reversible
        max_attempts_per_window=1,
        cooldown_s=3600,  # 1 alert per hour per strategy
        escalation_target="file",
        require_human_approval=False,
        # Stay in dry-run until the I2 observation window closes
        # (~48h after 2026-04-24 17:05 UTC activation). Even if the
        # executor's global dry_run is removed to promote Class A,
        # Class G keeps observing without side-effects until its
        # own window is sealed. Flip to False manually after I2 pass.
        force_dry_run=True,
        description=(
            "Emits a refactor proposal when the runtime detector observes "
            "a response text repeated identically across multiple users. "
            "No automatic code rewrite; human owns the refactor."
        ),
    )


def render_proposal_for_evidence(evidence: Dict[str, Any]) -> str:
    """Public helper — used by tests and by dry-run tooling to produce a
    concrete, evidence-rich proposal message without going through the
    executor. The real executor uses the static message from `build()`;
    downstream we plan to extend the executor to support evidence-rendered
    actions in v2.
    """
    return _build_proposal_message(evidence)
