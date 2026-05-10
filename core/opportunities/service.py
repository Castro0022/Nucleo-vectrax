"""
core/opportunities/service.py
===============================
Orquesta el flujo completo de la capa de oportunidades. Es el único
punto que sabe combinar detector + repository + scheduler + events +
formatter. El resto del sistema sólo habla con este service.

Multi-tenant desde el inicio: `owner_id` es obligatorio en cada
llamada y se propaga a todas las tablas y eventos.

Integración con el pipeline sync de Vectrax:
    * `process_message(...)`     → API async para tests / código async.
    * `observe_message_sync(...)` → bridge fire-and-forget en thread
      aparte. NO bloquea la latencia del mensaje principal y NO comparte
      event loop con el runtime existente. Race-free por diseño.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .detector import (
    DetectionResult,
    OpportunityDetector,
)
from .domain import (
    EventType,
    IntentType,
    OpportunityEvent,
    OpportunityState,
    OpportunityStatus,
    SourceType,
    new_uuid,
    slugify_contact,
)
from .events import OpportunityEventBus, get_event_bus
from .formatter import humanize
from .repository import (
    EventRepository,
    OpportunityRepository,
    ReactivationJobRepository,
)
from .scheduler import ReactivationScheduler
from .sqlite_repository import SqliteUnitOfWork
from .state_machine import assert_transition


logger = logging.getLogger("vectrax.opportunities.service")


# ---------------------------------------------------------------------------
# Configuración por defecto del backend
# ---------------------------------------------------------------------------

def _default_db_url() -> str:
    """
    URL del engine SQLite-async. Cambiable por env para tests o
    despliegue con DB en otra ruta. Para Postgres, asignar
    OPPORTUNITIES_DB_URL=postgresql+asyncpg://... directamente.
    """
    explicit = os.environ.get("OPPORTUNITIES_DB_URL")
    if explicit:
        return explicit
    db_path = os.environ.get(
        "OPPORTUNITIES_DB_PATH",
        os.path.join("data", "opportunities.db"),
    )
    # Asegurar directorio
    parent = os.path.dirname(db_path) or "."
    os.makedirs(parent, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


# ---------------------------------------------------------------------------
# Resultado expuesto al caller
# ---------------------------------------------------------------------------

@dataclass
class ProcessResult:
    matched: bool = False
    action: str = "none"  # 'created' | 'updated' | 'closed' | 'none'
    opportunity_id: Optional[str] = None
    suggestion: Optional[str] = None  # texto humanizado para el copiloto
    detection: Optional[DetectionResult] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OpportunityService:
    """
    Punto único de entrada. NO tiene estado mutable compartido entre
    requests — todas las dependencias se inyectan en el constructor.
    """

    def __init__(
        self,
        opportunities: OpportunityRepository,
        events: EventRepository,
        jobs: ReactivationJobRepository,
        scheduler: ReactivationScheduler,
        detector: Optional[OpportunityDetector] = None,
        event_bus: Optional[OpportunityEventBus] = None,
    ) -> None:
        self._opps = opportunities
        self._events = events
        self._jobs = jobs
        self._scheduler = scheduler
        self._detector = detector or OpportunityDetector()
        self._bus = event_bus or get_event_bus()

    # -- API async principal ------------------------------------------------

    async def process_message(
        self,
        owner_id: str,
        content: str,
        channel: str = "chat",
        source_type: SourceType = SourceType.AUTO_DETECTED,
        context: Optional[dict] = None,
    ) -> ProcessResult:
        """
        Procesa un mensaje entrante y persiste la transición resultante.

        Returns:
            ProcessResult con la acción tomada y, si corresponde, la
            sugerencia humanizada para el copiloto.
        """
        if not owner_id:
            raise ValueError("owner_id is required (multi-tenant)")
        if not content:
            return ProcessResult()

        det = await self._detector.detect(content, context=context)
        result = ProcessResult(matched=det.matched, detection=det)

        if not det.matched:
            return result

        if det.is_closed:
            return await self._handle_closed(
                owner_id, content, det, source_type, result,
            )

        # is_open
        return await self._handle_open(
            owner_id, content, det, source_type, result,
        )

    # -- API sync (bridge para pipeline existente) --------------------------

    def observe_message_sync(
        self,
        owner_id: str,
        content: str,
        channel: str = "chat",
        timeout_s: float = 5.0,
    ) -> ProcessResult:
        """
        Wrapper sync para callers que aún no son async. Ejecuta
        `process_message` en un hilo dedicado con su propio event loop
        para no chocar con loops del runtime principal.

        En el pipeline de Vectrax se usa fire-and-forget vía
        `observe_message_async_in_background(...)` (ver más abajo).
        """
        result_holder: dict = {}
        error_holder: dict = {}

        def _runner() -> None:
            try:
                loop = asyncio.new_event_loop()
                try:
                    result_holder["r"] = loop.run_until_complete(
                        self.process_message(owner_id, content, channel),
                    )
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
            except Exception as exc:
                error_holder["e"] = exc

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            logger.warning(
                "observe_message_sync timed out (owner=%s)", owner_id,
            )
            return ProcessResult()
        if "e" in error_holder:
            logger.debug(
                "observe_message_sync error (owner=%s): %s",
                owner_id, error_holder["e"],
            )
            return ProcessResult()
        return result_holder.get("r", ProcessResult())

    # -- API: sugerencias due (Fase 2) --------------------------------------

    async def pending_suggestions_for_owner(
        self,
        owner_id: str,
        max_items: int = 3,
        now: Optional[datetime] = None,
    ) -> list[str]:
        """
        Devuelve sugerencias humanizadas para reactivaciones que ya
        vencieron, marca cada job como PROCESSED y emite el evento
        REACTIVATION_TRIGGERED. Idempotente por job: una vez surfaced,
        no se vuelve a presentar.

        El caller decide dónde inyectarlas (LLM context, dashboard, etc.).
        """
        if not owner_id:
            return []
        ts = now or datetime.now(timezone.utc)
        due = await self._scheduler.due_now(owner_id=owner_id, now=ts)
        if not due:
            return []

        suggestions: list[str] = []
        for job in due[: max(1, int(max_items))]:
            op = await self._opps.get(job.opportunity_id, owner_id)
            if op is None or op.status == OpportunityStatus.CLOSED:
                # La oportunidad fue cerrada después del schedule —
                # cancelar el job para que no se vuelva a pickear.
                await self._jobs.mark_processed(job.id, owner_id)
                continue

            text = humanize(op, urgency=job.urgency, now=ts)
            suggestions.append(text)
            await self._jobs.mark_processed(job.id, owner_id)

            # Evento inmutable: la sugerencia fue surfaced
            evt = OpportunityEvent(
                opportunity_id=op.id,
                owner_id=owner_id,
                event_type=EventType.REACTIVATION_TRIGGERED,
                payload={
                    "job_id": job.id,
                    "urgency": job.urgency.value,
                    "due_at": job.due_at.isoformat(),
                },
                created_at=ts,
            )
            await self._events.append(evt)
            try:
                await self._bus.publish(evt)
            except Exception:
                pass
        return suggestions

    def pending_suggestions_for_owner_sync(
        self,
        owner_id: str,
        max_items: int = 3,
        timeout_s: float = 1.5,
    ) -> list[str]:
        """
        Wrapper sync con timeout corto: el pipeline NO debe colgarse
        si la DB tarda. Devuelve [] ante cualquier error o timeout.
        """
        result_holder: dict = {}

        def _runner() -> None:
            try:
                loop = asyncio.new_event_loop()
                try:
                    result_holder["r"] = loop.run_until_complete(
                        self.pending_suggestions_for_owner(
                            owner_id, max_items=max_items,
                        ),
                    )
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug(
                    "pending_suggestions_sync error (owner=%s): %s",
                    owner_id, exc,
                )

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout_s)
        if t.is_alive():
            logger.warning(
                "pending_suggestions_sync timed out (owner=%s)", owner_id,
            )
            return []
        return result_holder.get("r", []) or []

    def observe_message_fire_and_forget(
        self,
        owner_id: str,
        content: str,
        channel: str = "chat",
    ) -> None:
        """
        Variante no bloqueante: arranca el procesamiento async en un
        hilo daemon y vuelve. Pensado para wirear dentro del pipeline
        principal de Vectrax sin agregar latencia al usuario.
        """

        def _runner() -> None:
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        self.process_message(owner_id, content, channel),
                    )
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug(
                    "fire_and_forget runner error (owner=%s): %s",
                    owner_id, exc,
                )

        threading.Thread(target=_runner, daemon=True).start()

    # -- Handlers internos --------------------------------------------------

    async def _handle_open(
        self,
        owner_id: str,
        content: str,
        det: DetectionResult,
        source_type: SourceType,
        result: ProcessResult,
    ) -> ProcessResult:
        contact_name = det.contact_name
        user_id = (
            slugify_contact(contact_name)
            if contact_name
            else f"anon-{new_uuid()[:8]}"
        )

        existing = None
        if contact_name:
            existing = await self._opps.find_open_for_contact(
                owner_id=owner_id, user_id=user_id,
            )

        now = datetime.now(timezone.utc)

        if existing is not None:
            # UPDATE
            existing.last_activity = now
            existing.updated_at = now
            existing.intent_signal = det.matched_signal or existing.intent_signal
            existing.intent_type = det.intent_type
            existing.source_message = content[:1000]
            existing.confidence_score = max(
                existing.confidence_score, det.confidence,
            )
            await self._opps.update(existing)
            await self._append_event(
                EventType.OPPORTUNITY_UPDATED, existing, det,
            )
            # No reprogramamos jobs en UPDATE para evitar duplicación;
            # los jobs originales siguen vigentes desde su creación.
            result.action = "updated"
            result.opportunity_id = existing.id
            result.suggestion = humanize(existing)
            return result

        # CREATE
        op = OpportunityState(
            owner_id=owner_id,
            user_id=user_id,
            contact_name=contact_name,
            status=OpportunityStatus.OPEN,
            intent_signal=det.matched_signal,
            intent_type=det.intent_type,
            source_message=content[:1000],
            source_type=source_type,
            confidence_score=det.confidence,
            priority_score=det.confidence,
            created_at=now,
            last_activity=now,
            updated_at=now,
        )
        await self._opps.save(op)
        await self._append_event(
            EventType.OPPORTUNITY_CREATED, op, det,
        )
        scheduled = await self._scheduler.schedule_for_opportunity(op)
        for job in scheduled:
            await self._append_event(
                EventType.REACTIVATION_SCHEDULED,
                op,
                det,
                extra={"job_id": job.id, "urgency": job.urgency.value},
            )

        result.action = "created"
        result.opportunity_id = op.id
        result.suggestion = humanize(op)
        return result

    async def _handle_closed(
        self,
        owner_id: str,
        content: str,
        det: DetectionResult,
        source_type: SourceType,
        result: ProcessResult,
    ) -> ProcessResult:
        contact_name = det.contact_name
        target: Optional[OpportunityState] = None

        if contact_name:
            user_id = slugify_contact(contact_name)
            target = await self._opps.find_open_for_contact(
                owner_id=owner_id, user_id=user_id,
            )

        if target is None:
            # Sin oportunidad abierta relacionada — no creamos una
            # cerrada de la nada para evitar ruido. Caller queda noop.
            result.action = "none"
            return result

        # Validar transición; si por algún motivo viene CLOSED ya no
        # hace falta tocarla.
        try:
            assert_transition(target.status, OpportunityStatus.CLOSED)
        except Exception:
            return result

        now = datetime.now(timezone.utc)
        target.status = OpportunityStatus.CLOSED
        target.intent_type = IntentType.CLOSED
        target.intent_signal = det.matched_signal or target.intent_signal
        target.source_message = content[:1000]
        target.closed_at = now
        target.updated_at = now
        target.last_activity = now

        await self._opps.update(target)
        cancelled = await self._scheduler.cancel_for_opportunity(
            opportunity_id=target.id, owner_id=owner_id,
        )
        await self._append_event(
            EventType.OPPORTUNITY_CLOSED,
            target,
            det,
            extra={"cancelled_jobs": cancelled},
        )

        result.action = "closed"
        result.opportunity_id = target.id
        result.suggestion = humanize(target)
        return result

    # -- Util: registrar evento + publicar al bus --------------------------

    async def _append_event(
        self,
        event_type: EventType,
        op: OpportunityState,
        det: DetectionResult,
        extra: Optional[dict] = None,
    ) -> None:
        payload = {
            "matched_signal": det.matched_signal,
            "intent_type": det.intent_type.value,
            "confidence": det.confidence,
            "engine": det.engine,
            "status": op.status.value,
        }
        if extra:
            payload.update(extra)

        evt = OpportunityEvent(
            opportunity_id=op.id,
            owner_id=op.owner_id,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        await self._events.append(evt)
        try:
            await self._bus.publish(evt)
        except Exception as exc:
            logger.debug("event bus publish failed: %s", exc)


# ---------------------------------------------------------------------------
# Builders + singleton lazy
# ---------------------------------------------------------------------------

async def build_default_service(
    db_url: Optional[str] = None,
) -> OpportunityService:
    """Construye un service con SQLite-async + scheduler default."""
    uow = SqliteUnitOfWork(db_url or _default_db_url())
    await uow.init_schema()
    scheduler = ReactivationScheduler(
        opportunities=uow.opportunities,
        jobs=uow.jobs,
    )
    return OpportunityService(
        opportunities=uow.opportunities,
        events=uow.events,
        jobs=uow.jobs,
        scheduler=scheduler,
    )


_singleton_lock = threading.Lock()
_singleton: Optional[OpportunityService] = None


def get_default_service_sync() -> OpportunityService:
    """
    Devuelve un singleton del service para uso desde código sync.
    Race-safe: la inicialización se hace una sola vez y dentro de un
    event loop dedicado.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        loop = asyncio.new_event_loop()
        try:
            _singleton = loop.run_until_complete(build_default_service())
        finally:
            try:
                loop.close()
            except Exception:
                pass
        return _singleton


def reset_singleton_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None
