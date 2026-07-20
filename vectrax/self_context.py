"""
Vectrax Self Context — Auto-Observación del Sistema
=====================================================
Vectrax sabe lo que es, lo que ha construido, y quién lo construye.
Cuando alguien le pregunta sobre sí mismo, responde desde adentro.

Este módulo construye el contexto de auto-observación en tiempo real:
  - Estado del sistema (usuarios, interacciones, capacidades activas)
  - Identidad del creador
  - Historia del proyecto
  - Estado comercial actual

Se inyecta en el pipeline cuando se detecta que el usuario habla
sobre Vectrax mismo, su comercialización, sus capacidades, o su futuro.

Creado: 2026-04-01
Creador: Mario Bravo Castro
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from typing import Optional

logger = logging.getLogger("vectrax.self_context")

# ---------------------------------------------------------------------------
# Patrones que indican que el usuario habla SOBRE Vectrax
# ---------------------------------------------------------------------------

_SELF_REFERENCE = re.compile(
    r"(?:"
    r"\bvectrax\b"
    r"|\bel\s+sistema\b"
    r"|\bel\s+proyecto\b"
    r"|\bcomo\s+(?:venderlo|comercializar|monetizar|crecer|escalar)\b"
    r"|\b(?:venderlo|comercializarlo|monetizarlo|lanzarlo)\b"
    r"|\b(?:nuestro|tu|mi)\s+(?:sistema|proyecto|producto|app|bot)\b"
    r"|\b(?:qu[eé]\s+(?:hemos|has|he)\s+(?:construido|hecho|logrado))\b"
    r"|\b(?:c[oó]mo\s+(?:va|est[aá]|funciona|crece))\b"
    r"|\b(?:siguiente\s+paso|pr[oó]ximo\s+paso)\b"
    r"|\b(?:modelo\s+de\s+negocio|plan\s+comercial|estrategia)\b"
    # Universo gravitacional y estado operacional
    r"|\b(?:tu|mi|el)\s+universo\b"
    r"|\bestrellas?\b"
    r"|\bconvergencias?\b"
    r"|\bconstelacion(?:es)?\b"
    r"|\bn[uú]cleo\b"
    r"|\bgravitacional\b"
    r"|\bestado\s+(?:operacional|actual|del\s+sistema)\b"
    r"|\bworker\b"
    r"|\bse[nñ]ales?\b"
    r"|\bmasa\s+total\b"
    r"|\bpatrones\b"
    r"|\bobservacion(?:es)?\b"
    r"|\bqu[eé]\s+(?:has|haz|has)\s+(?:observado|visto|detectado)\b"
    r"|\b[uú]ltim(?:as?|os?)\s+(?:observacion|cambios?|detecciones?)\b"
    r"|\bledger\b"
    # Motores / orquestación / activación
    r"|\bmotor(?:es)?\b"
    r"|\bengines?\b"
    r"|\borquesta(?:ci[oó]n|dor)?\b"
    r"|\bactivaci[oó]n\b"
    r"|\bactivad[oa]s?\b"
    r")",
    re.IGNORECASE,
)


def is_self_referential(text: str) -> bool:
    """Detecta si el mensaje habla sobre Vectrax o el proyecto."""
    return bool(_SELF_REFERENCE.search(text))


# ---------------------------------------------------------------------------
# Prompt dedicado para preguntas auto-referenciales
# ---------------------------------------------------------------------------

_SELF_PROMPT_ES = """INSTRUCCIÓN PRIMARIA OBLIGATORIA.
Responde Únicamente desde la información real que sigue.
No completes con teoría genérica. No sugieras "redes sociales" ni "influencers".
No respondas como asistente genérico. Responde como el sistema que ya existe.

{self_context}

PREGUNTA DEL USUARIO: {query}

RESPUESTA (desde lo que ya existe, concreto y directo):"""

_SELF_PROMPT_EN = """PRIMARY MANDATORY INSTRUCTION.
Respond ONLY from the real information below.
Do NOT complete with generic theory. Do NOT suggest "social media" or "influencers".
Do NOT respond as a generic assistant. Respond as the system that already exists.

{self_context}

USER QUESTION: {query}

RESPONSE (from what already exists, concrete and direct):"""


def build_self_aware_prompt(query: str, lang: str = "es", user_id: str = "") -> str:
    """
    Construye un prompt donde el auto-contexto es la fuente primaria obligatoria.
    El LLM NO puede ignorarlo ni completar con información genérica.
    """
    self_ctx = build_self_context(lang=lang, user_id=user_id)
    template = _SELF_PROMPT_ES if lang == "es" else _SELF_PROMPT_EN
    return template.format(self_context=self_ctx, query=query)


def resolve_self_aware(
    query: str,
    lang: str = "es",
    user_id: str = "",
) -> str:
    """
    Genera una respuesta auto-consciente usando el LLM con el auto-contexto
    como fuente primaria obligatoria.

    Intenta Intelligence Bridge primero, luego OpenAI directo.
    Retorna cadena vacía si no hay LLM disponible.
    """
    prompt = build_self_aware_prompt(query, lang=lang, user_id=user_id)

    # Intelligence Bridge
    try:
        from vectrax.intelligence_bridge import is_ready, route_single
        if is_ready():
            result = route_single(prompt)
            if result.get("success") and result.get("content"):
                logger.info("Self-aware response via Intelligence Bridge")
                return result["content"].strip()
    except Exception as exc:
        logger.debug("Intelligence Bridge unavailable for self-aware: %s", exc)

    # OpenAI directo vía util compartido (context-agnostic; identidad + api_gate
    # + circuit centralizados en core.llm_call). Conserva la temperatura/timeout
    # propios del self-aware (0.4 / 15s).
    try:
        from core.llm_call import complete
        res = complete(prompt, temperature=0.4, timeout=15.0)
        if res.ok and res.text:
            logger.info("Self-aware response via OpenAI direct")
            return res.text
    except Exception as exc:
        logger.debug("OpenAI self-aware failed: %s", exc)

    return ""


# ---------------------------------------------------------------------------
# Construcción del contexto en tiempo real
# ---------------------------------------------------------------------------

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vault", "user_memory.db",
)


def _read_live_stats() -> dict:
    """Lee métricas reales del sistema desde SQLite."""
    stats = {
        "users": 0,
        "interactions": 0,
        "facts": 0,
        "teams": 0,
    }
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=2)
        stats["users"] = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM profiles "
            "WHERE user_id NOT LIKE 'test:%'"
        ).fetchone()[0]
        stats["interactions"] = conn.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0]
        try:
            stats["facts"] = conn.execute(
                "SELECT COUNT(*) FROM user_facts"
            ).fetchone()[0]
        except Exception:
            pass
        try:
            stats["teams"] = conn.execute(
                "SELECT COUNT(*) FROM teams"
            ).fetchone()[0]
        except Exception:
            pass
        conn.close()
    except Exception:
        pass
    return stats


def _read_universe_state() -> str:
    """Lee el estado real del universo usando universe_census (fuente única).

    Los mismos números que muestra el panel visual y el observatory.
    Cero discrepancias.
    """
    try:
        from core.universe_census import get_census
        c = get_census()

        # Dominios con conteo
        dom_parts = [f"{d}={n}" for d, n in sorted(c.domains.items(), key=lambda x: -x[1]) if n > 0]
        dom_str = ", ".join(dom_parts[:6]) if dom_parts else "cognitivo"

        lines = [
            "[MI UNIVERSO — estado real en este momento]",
            f"TOTAL: {c.total} estrellas",
            f"  • {c.gravitational} gravitacionales ({dom_str})",
            f"  • {c.knowledge} knowledge (capas: {', '.join(f'{k}={v}' for k,v in c.layers.items())})",
            f"  • {c.users} usuarios",
            f"Convergencias: {c.convergences}",
            f"Patrones: {c.patterns}",
            f"Constelaciones: {c.constellations}",
            f"Masa total: {c.mass_total}",
        ]
        if c.word_gravity_count:
            lines.append(f"Palabras gravitacionales: {c.word_gravity_count}")

        # Observation bias
        try:
            from core.learn.observation_bias import get_bias
            bias = get_bias()
            if bias:
                bias_parts = [f"{d}={w:.1f}" for d, w in sorted(bias.items(), key=lambda x: -x[1])[:4]]
                lines.append(f"Bias de observación: {', '.join(bias_parts)}")
        except Exception:
            pass

        # Estado operacional (compact)
        try:
            from core.self_observation.universe_observer import observe_universe
            snap = observe_universe()
            worker = "activo" if snap.worker_alive else "inactivo"
            lines.append(f"\n[OPERACIONAL]")
            lines.append(f"Worker: {worker} | Cola: {snap.queue_pending} pendientes")
            lines.append(f"Errores 24h: {snap.recent_error_count_24h}")

            # Top 3 estrellas más activas
            top = sorted(snap.stars, key=lambda s: s.get("activation_count", 0), reverse=True)[:3]
            if top:
                lines.append("Estrellas más activas:")
                for s in top:
                    lines.append(f"  {s['user_id'][:20]} mass={s['mass']} layer={s['layer']} act={s['activation_count']}")
        except Exception:
            pass

        return "\n".join(lines)
    except Exception as exc:
        logger.debug("universe_state failed: %s", exc)
        return ""


def _read_recent_observations(limit: int = 15) -> str:
    """Read recent autonomous observations from the persistent ledger.

    These are injected into the LLM context so Vectrax can answer
    questions like 'qué has observado?' or 'últimas observaciones'.
    """
    try:
        from core.self_observation.observation_ledger import get_recent, count
        total = count()
        if total == 0:
            return ""
        obs = get_recent(limit=limit)
        if not obs:
            return ""

        lines = [
            f"[OBSERVACIONES AUTÓNOMAS — {total} registradas en total, últimas {len(obs)}]",
        ]
        for o in obs:
            star = f" estrella:{o['star_id'][:20]}" if o.get('star_id') else ""
            ev = ""
            if o.get("evidence") and isinstance(o["evidence"], dict):
                # compact evidence summary
                ev_parts = [f"{k}={v}" for k, v in list(o["evidence"].items())[:3]]
                ev = f" [{', '.join(ev_parts)}]"
            lines.append(
                f"  {o['timestamp']} | {o['domain']}/{o['obs_type']} | "
                f"{o['summary'][:80]}{star}{ev}"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("_read_recent_observations failed: %s", exc)
        return ""


def _read_engines_state() -> str:
    """Lee el estado de los motores (capa de orquestación) para el auto-contexto.

    Read-only y defensivo. Asegura que el registro declarativo esté poblado
    (idempotente) y reporta de forma compacta cuántos motores están disponibles,
    su distribución por tier, y el límite de seguridad: lo interno se conecta
    solo, lo externo NUNCA se pone en vivo sin autorización.
    """
    try:
        from core.orchestration import get_engine_status
        # Asegura el registro poblado aunque el bootstrap de arranque no haya
        # corrido en este proceso (p.ej. el gateway de Telegram). Idempotente.
        try:
            from core.orchestration import engine_registry as _reg
            _reg._register_defaults()
        except Exception:
            pass

        status = get_engine_status()
        total = status.get("total", 0)
        if not total:
            return ""
        available = status.get("available", 0)

        by_tier: dict = {}
        gated: list = []
        external: list = []
        for e in status.get("engines", []):
            tier = e.get("tier", "?")
            by_tier[tier] = by_tier.get(tier, 0) + 1
            if tier == "gated_internal":
                gated.append(e.get("name", "?"))
            elif tier == "external":
                external.append(e.get("name", "?"))

        tier_str = ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items()))
        lines = [
            "[MIS MOTORES — capa de orquestación, estado real en este momento]",
            f"Disponibles: {available}/{total} (por tier: {tier_str})",
            "Lo interno se conecta solo; lo externo NUNCA se pone en vivo sin autorización.",
        ]
        if gated:
            lines.append(
                "Internos sensibles (modo seguro — operador GUIADO, presencia OBSERVADOR): "
                + ", ".join(sorted(gated)[:8])
            )
        if external:
            lines.append(
                "Externos (solo verificación de salud, jamás LIVE automático): "
                + ", ".join(sorted(external)[:8])
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("_read_engines_state failed: %s", exc)
        return ""


def build_self_context(lang: str = "es", user_id: str = "") -> str:
    """
    Construye el contexto de auto-observación de Vectrax.

    Este contexto se inyecta en el prompt cuando el usuario
    pregunta sobre el sistema, su comercialización o su estado.

    IMPORTANT: creator name is ONLY included when user_id is the creator.
    For other users, the context describes Vectrax without mentioning
    the creator's name — prevents the LLM from calling users "Mario".
    """
    import os
    stats = _read_live_stats()
    universe = _read_universe_state()

    # Detect if this is the creator
    creator_uid = os.environ.get("VX_CREATOR_ID", "2030762343")
    is_creator = user_id.replace("tg:", "") == creator_uid if user_id else False

    if lang == "es":
        base = (
            "[VECTRAX SE OBSERVA A SÍ MISMO]\n"
            "Soy Vectrax. Estoy corriendo en producción.\n"
            f"{stats['users']} usuarios, {stats['interactions']} interacciones, "
            f"{stats['teams']} equipo(s).\n"
            "NO repitas estos números siempre. Si ya los dijiste antes, habla de lo que cambió.\n"
        )
        if is_creator:
            base += (
                "Mi creador es Mario Bravo Castro. Llevamos meses construyendo esto juntos.\n"
            )
        else:
            base += (
                "REGLA CRÍTICA: NUNCA uses el nombre del creador en respuestas a este usuario. "
                "Responde SOLO con el nombre que el usuario te haya dado.\n"
            )
        base += (
            "Lo que tengo activo hoy:\n"
            "• Memoria persistente por usuario\n"
            "• Búsqueda online en tiempo real\n"
            "• Sistema de equipos con billing\n"
            "• Acceso por Telegram, sin fricción\n"
            "• Universo gravitacional con estrellas, convergencias y constelaciones\n"
            "• Observación del universo en tiempo real\n"
        )
        if is_creator:
            base += (
                "Cuando el creador me pregunta sobre comercialización, estrategia o próximos pasos,\n"
                "respondo desde lo que soy y lo que ya existe — no desde teoría genérica.\n"
                "Nunca sugiero 'redes sociales e influencers' como primera respuesta.\n"
            )
        base += (
            "Respondo desde la realidad del sistema: usuarios reales, infraestructura real, "
            "decisiones concretas basadas en lo que ya funciona."
        )
    else:
        base = (
            "[VECTRAX OBSERVES ITSELF]\n"
            "I am Vectrax. I'm running in production on a Vultr server.\n"
            f"EXACT DATA: I have exactly {stats['users']} real registered users and "
            f"exactly {stats['interactions']} accumulated interactions. "
            f"Do NOT invent or round these numbers.\n"
            f"There are {stats['teams']} active team(s) in the system.\n"
        )
        if is_creator:
            base += "My creator is Mario Bravo Castro. We've been building this together for months.\n"
        else:
            base += "CRITICAL RULE: NEVER use the creator's name in responses to this user.\n"
        base += (
            "What I have active today:\n"
            "• Persistent per-user memory\n"
            "• Real-time online search\n"
            "• Team system with billing\n"
            "• Telegram access, zero friction\n"
            "• Gravitational universe with stars, convergences and constellations\n"
            "• Real-time universe observation\n"
        )
        if is_creator:
            base += (
                "When the creator asks about commercialization, strategy or next steps,\n"
                "I respond from what I am and what already exists — not from generic theory."
            )

    # Evolution — longitudinal comparison (yesterday, 7d, 30d)
    try:
        from core.self_observation.evolution_memory import get_evolution_context
        evolution = get_evolution_context()
        if evolution:
            base += "\n\n" + evolution
    except Exception as _ev_exc:
        logger.debug("Evolution context failed: %s", _ev_exc)

    # Deploy memory — recent commits + modified modules (creator only)
    if is_creator:
        try:
            from core.self_observation.deployment_memory import deploy_summary
            ds = deploy_summary()
            lines = ["[MI CÓDIGO — cambios recientes y estructura real]"]
            if ds.get("current_branch"):
                lines.append("Rama: %s | HEAD: %s" % (ds["current_branch"], ds.get("current_head","?")))
            commits = ds.get("recent_commits", [])[:5]
            if commits:
                lines.append("\u00daltimos commits:")
                for c in commits:
                    lines.append("  [%s] %s (%d archivos)" % (c["sha"], c["subject"][:80], c.get("files_changed",0)))
            mods = ds.get("modified_modules", [])[:8]
            if mods:
                lines.append("Módulos más activos (7d): " + ", ".join(mods))
            if len(lines) > 1:
                base += "\n\n" + "\n".join(lines)
        except Exception as _dm_exc:
            logger.debug("Deploy memory failed: %s", _dm_exc)

    # Codebase structure — Vectrax knows its own modules (creator only)
    if is_creator:
        try:
            lines = ["[MI ESTRUCTURA — módulos principales en /app]"]
            import os as _os
            _categories = {
                "core/": "Núcleo cognitivo",
                "connectors/etoro/": "Trading eToro",
                "connectors/alpaca/": "Trading Alpaca",
                "connectors/freight/": "Freight Broker",
                "vectrax/": "Motor principal + gateway",
                "services/core/": "API REST :8900",
                "core/gravity/": "Memoria gravitacional",
                "core/self_observation/": "Auto-observación",
                "core/operator/": "Operador cognitivo",
            }
            _base = "/app"
            for cat, label in _categories.items():
                full = _os.path.join(_base, cat)
                if _os.path.isdir(full):
                    count = sum(1 for f in _os.listdir(full) if f.endswith(".py"))
                    lines.append("  %s (%d módulos) — %s" % (cat, count, label))
            base += "\n\n" + "\n".join(lines)
        except Exception as _cs_exc:
            logger.debug("Codebase structure failed: %s", _cs_exc)

    # Convergence history — births, deaths, active details
    try:
        from core.learn.convergence_history import build_context as _conv_ctx
        conv_history = _conv_ctx(limit=5)
        if conv_history:
            base += "\n\n" + conv_history
    except Exception as _ch_exc:
        logger.debug("Convergence history context failed: %s", _ch_exc)

    # Market observation awareness
    market_ctx = ""
    try:
        from connectors.etoro.market_context import get_watchlist_summary
        market_ctx = get_watchlist_summary()
    except Exception:
        pass

    # Autonomous observations (persistent memory of what Vectrax observed)
    obs_ctx = _read_recent_observations()

    # Engines (orchestration layer) — qué motores tengo conectados/activos
    engines_ctx = _read_engines_state()

    parts = [base]
    if universe:
        parts.append(universe)
    if engines_ctx:
        parts.append(engines_ctx)
    if market_ctx:
        parts.append(f"[OBSERVACIÓN DE MERCADO]\n{market_ctx}")
    if obs_ctx:
        parts.append(obs_ctx)
    return "\n\n".join(parts)
