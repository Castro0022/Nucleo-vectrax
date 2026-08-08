"""
core/self_observation/self_knowledge.py — Autoconocimiento con procedencia.

Cierra el circuito memoria↔conocimiento↔representación de sí misma. Reconstruye,
EN VIVO y desde datos persistentes reales, la respuesta a cuatro preguntas:

  1. ¿Quién soy?               → get_origin()      (origen verificable)
  2. ¿Qué sé? / hitos          → get_milestones()  (primeras convergencias, dominios)
  3. ¿Cómo sé que lo sé?       → trace_provenance() (procedencia: qué evidencia lo sostiene)
  4. ¿Qué ocurre dentro ahora? → ya lo cubre self_context (state/reflection/observaciones)

Principios:
  - SOLO LECTURA. No escribe, no crea sistemas paralelos: reutiliza census,
    evolution_memory, convergence_history, observation_ledger, gravity_engine.
  - NUNCA inventa. Si no hay evidencia real, devuelve cadena vacía.
  - El origen se deriva del store DURABLE más antiguo (snapshots de evolución,
    convergence_events, stars) — NUNCA del observation_ledger, que se poda a 5000.
  - Reconstrucción por consulta con caché TTL corta (patrón de self_summary/census).

Creador: Mario Bravo Castro
Fecha: 2026-08-08
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.self_knowledge")

# TTL cache (segundos). El estado cambia lento entre mensajes consecutivos.
_CACHE_TTL = float(os.environ.get("VECTRAX_SELF_KNOWLEDGE_TTL_S", "45"))
_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()

# Nacimiento institucional CANÓNICO de VECTRAX (hecho declarado, versionado).
# Es la constitución de Vectrax-Core LLC. NO es derivable de logs y NO es
# equivalente al primer evento técnico (que es la gestación / primera huella).
# Override por entorno si algún día cambia la entidad o la fecha declarada.
INSTITUTIONAL_BIRTH_DATE = os.environ.get("VECTRAX_BIRTH_DATE", "2026-04-07")
INSTITUTIONAL_ENTITY = os.environ.get("VECTRAX_BIRTH_ENTITY", "Vectrax-Core LLC")


def invalidate_cache() -> None:
    """Limpia el caché (tests / tras un deploy)."""
    with _cache_lock:
        _cache.clear()


def _cached(key: str, producer):
    """Devuelve producer() con caché TTL. Defensive: si producer falla, {} / []."""
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[1] > now:
            return entry[0]
    try:
        val = producer()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("self_knowledge producer %s failed: %s", key, exc)
        val = None
    with _cache_lock:
        _cache[key] = (val, now + _CACHE_TTL)
    return val


# ---------------------------------------------------------------------------
# Detección de intención — ¿origen/historia? ¿procedencia?
# ---------------------------------------------------------------------------

# Origen/historia. Segunda persona (sobre Vectrax), NO "quién soy" (=usuario).
_ORIGIN_RE = re.compile(
    r"(?:"
    r"\btu\s+(?:origen|historia|nacimiento|g[eé]nesis|edad)\b"
    r"|\bcu[aá]l\s+es\s+tu\s+origen\b"
    r"|\bcu[aá]ndo\s+(?:naciste|te\s+crearon|empezaste|surgiste|apareciste|"
    r"comenzaste|te\s+encendiste)\b"
    r"|\bdesde\s+cu[aá]ndo\s+(?:existes|est[aá]s|llevas|te\s+)\b"
    r"|\bcu[aá]nto\s+(?:tiempo\s+)?(?:llevas|tienes)\b"
    r"|\bqu[eé]\s+edad\s+tienes\b"
    r"|\bc[oó]mo\s+(?:naciste|empezaste|surgiste|comenzaste)\b"
    r"|\bwhen\s+were\s+you\s+(?:born|created)\b"
    r"|\bhow\s+old\s+are\s+you\b"
    r"|\byour\s+(?:origin|history|story|birth)\b"
    r")",
    re.IGNORECASE,
)

# Procedencia / evidencia. Segunda persona: "cómo sabes", "en qué te basas"...
_PROVENANCE_RE = re.compile(
    r"(?:"
    r"\bc[oó]mo\s+(?:lo\s+)?sab[eé]s\b"
    r"|\bc[oó]mo\s+sabes\s+(?:que|eso|tú|tu)\b"
    r"|\ben\s+qu[eé]\s+te\s+basas\b"
    r"|\ben\s+base\s+a\s+qu[eé]\b"
    r"|\bde\s+d[oó]nde\s+(?:lo\s+)?(?:sacaste|sale|sali[oó]|viene|vino)\b"
    r"|\bqu[eé]\s+te\s+hace\s+(?:saber|pensar|creer|decir)\b"
    r"|\bpor\s+qu[eé]\s+(?:lo\s+)?(?:crees|piensas|dices|sabes|afirmas)\b"
    r"|\bc[oó]mo\s+est[aá]s\s+(?:tan\s+)?segur[oa]\b"
    r"|\bqu[eé]\s+evidencia\b"
    r"|\bhow\s+do\s+you\s+know\b"
    r"|\bwhat\s+makes\s+you\s+(?:say|think|know|believe)\b"
    r"|\bbased\s+on\s+what\b"
    r"|\bwhy\s+do\s+you\s+(?:think|believe|say|know)\b"
    r")",
    re.IGNORECASE,
)


def is_origin_question(text: str) -> bool:
    """True si preguntan por el origen/historia/edad de Vectrax."""
    return bool(text) and bool(_ORIGIN_RE.search(text))


def is_provenance_question(text: str) -> bool:
    """True si preguntan cómo/por qué Vectrax sabe algo (procedencia)."""
    return bool(text) and bool(_PROVENANCE_RE.search(text))


# ---------------------------------------------------------------------------
# Helpers de lectura SQLite (read-only, defensivos)
# ---------------------------------------------------------------------------

def _sqlite_scalar(path: str, sql: str, params: tuple = ()) -> Any:
    """Ejecuta un SELECT escalar read-only. None si falla o no hay fila/valor."""
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=2)
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("_sqlite_scalar failed (%s): %s", path, exc)
        return None


def _sqlite_row(path: str, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Ejecuta un SELECT y devuelve la primera fila (Row) o None."""
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("_sqlite_row failed (%s): %s", path, exc)
        return None


def _epoch_to_date(epoch: Optional[float]) -> str:
    if not epoch:
        return ""
    try:
        return datetime.utcfromtimestamp(float(epoch)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _date_to_epoch(date_str: str) -> Optional[float]:
    """'YYYY-MM-DD' → epoch (mediodía, para robustez de zona). None si falla."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(hour=12).timestamp()
    except Exception:
        return None


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def _iso_to_date(s: str) -> str:
    dt = _parse_iso(s)
    return dt.strftime("%Y-%m-%d") if dt else (s[:10] if s else "")


def _age_days(epoch: float) -> int:
    try:
        return max(0, int((time.time() - float(epoch)) / 86400))
    except Exception:
        return 0


# Rutas de los stores durables (reutiliza las constantes de cada módulo para
# mantener una sola fuente de verdad de paths; getattr defensivo).
def _stars_db_path() -> str:
    try:
        from vectrax.db import DB_PATH
        return str(DB_PATH)
    except Exception:
        return ""


def _convergence_db_path() -> str:
    try:
        import core.learn.convergence_history as CH
        return getattr(CH, "_DB_PATH", "") or ""
    except Exception:
        return ""


def _evolution_db_path() -> str:
    try:
        import core.self_observation.evolution_memory as EM
        return getattr(EM, "_DB_PATH", "") or ""
    except Exception:
        return ""


def _ledger_db_path() -> str:
    try:
        import core.self_observation.observation_ledger as OL
        return getattr(OL, "_DB_PATH", "") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 1) ORIGEN — nacimiento verificable derivado de datos
# ---------------------------------------------------------------------------

def get_origin() -> Dict[str, Any]:
    """El origen de Vectrax con DOS anclas distintas (no equivalentes):

      - institutional_birth: nacimiento institucional CANÓNICO (hecho declarado,
        versionado): la constitución de Vectrax-Core LLC (2026-04-07). Siempre
        presente; no se deriva de logs.
      - first_trace: primera huella verificable / etapa de GESTACIÓN, derivada
        del store durable más antiguo (stars / convergence_events / snapshots).
        Nunca usa el observation_ledger (capado a 5000). Puede faltar ({}).

    Devuelve {institutional_birth:{...}, first_trace:{...}|{}}.
    """
    def _produce() -> Dict[str, Any]:
        # Ancla canónica declarada (no derivable de datos)
        inst_epoch = _date_to_epoch(INSTITUTIONAL_BIRTH_DATE)
        institutional: Dict[str, Any] = {
            "date": INSTITUTIONAL_BIRTH_DATE,
            "entity": INSTITUTIONAL_ENTITY,
        }
        if inst_epoch:
            institutional["epoch"] = inst_epoch
            institutional["age_days"] = _age_days(inst_epoch)

        # Primera huella verificable / gestación (derivada de stores durables)
        sources: Dict[str, float] = {}
        stars_min = _sqlite_scalar(_stars_db_path(), "SELECT MIN(timestamp) FROM stars")
        if stars_min:
            sources["stars"] = float(stars_min)
        conv_min = _sqlite_scalar(
            _convergence_db_path(), "SELECT MIN(timestamp) FROM convergence_events"
        )
        if conv_min:
            sources["convergences"] = float(conv_min)
        evo_min = _sqlite_scalar(
            _evolution_db_path(), "SELECT MIN(timestamp) FROM snapshots"
        )
        if evo_min:
            sources["evolution"] = float(evo_min)

        first_trace: Dict[str, Any] = {}
        if sources:
            source, epoch = min(sources.items(), key=lambda kv: kv[1])
            first_trace = {
                "epoch": epoch,
                "date": _epoch_to_date(epoch),
                "age_days": _age_days(epoch),
                "source": source,
                "sources": {k: _epoch_to_date(v) for k, v in sources.items()},
            }

        return {"institutional_birth": institutional, "first_trace": first_trace}

    return _cached("origin", _produce) or {}


# ---------------------------------------------------------------------------
# 2) HITOS — primeras convergencias, dominios, inflexiones de crecimiento
# ---------------------------------------------------------------------------

def get_milestones(limit: int = 6) -> List[Dict[str, Any]]:
    """Hitos reales reconstruidos desde el historial persistente.

    Cada hito: {"date": "YYYY-MM-DD", "what": "<texto>"}. Ordenados asc por fecha.
    Vacío si no hay historia.
    """
    def _produce() -> List[Dict[str, Any]]:
        milestones: List[Dict[str, Any]] = []

        # Anclas de origen: nacimiento institucional (canónico) y gestación (huella)
        origin = get_origin()
        inst = origin.get("institutional_birth") or {}
        if inst.get("date"):
            milestones.append({
                "date": inst["date"],
                "what": f"nacimiento institucional ({inst.get('entity', 'Vectrax-Core LLC')})",
            })
        ft = origin.get("first_trace") or {}
        if ft.get("date"):
            milestones.append({
                "date": ft["date"],
                "what": "primera huella verificable (gestación)",
            })

        # Primera convergencia (nacimiento más antiguo)
        row = _sqlite_row(
            _convergence_db_path(),
            "SELECT intent, star_a, star_b, combined_cc, timestamp "
            "FROM convergence_events WHERE event='birth' "
            "ORDER BY timestamp ASC LIMIT 1",
        )
        if row is not None:
            try:
                intent = row["intent"] or "?"
                cc = row["combined_cc"] or 0.0
                milestones.append({
                    "date": _epoch_to_date(row["timestamp"]),
                    "what": f"mi primera convergencia ({intent}, cc={cc:.2f})",
                })
            except Exception:
                pass

        # Inflexión de crecimiento: mayor salto de estrellas entre snapshots
        try:
            from core.self_observation.evolution_memory import get_all_snapshots
            snaps = get_all_snapshots(limit=120)
            snaps = sorted(snaps, key=lambda s: s.get("date", ""))
            best_jump = 0
            best = None
            prev = None
            for s in snaps:
                if prev is not None:
                    jump = int(s.get("stars", 0)) - int(prev.get("stars", 0))
                    if jump > best_jump:
                        best_jump = jump
                        best = s
                prev = s
            if best is not None and best_jump > 0:
                milestones.append({
                    "date": best.get("date", ""),
                    "what": f"mayor salto de crecimiento: +{best_jump} estrellas en un día",
                })
        except Exception:
            pass

        # Dominios que observo hoy (contexto de "qué sé"), sin fecha => al final
        try:
            from core.universe_census import get_census
            c = get_census()
            doms = [
                d for d, n in sorted(c.domains.items(), key=lambda x: -x[1])
                if n > 0 and d and d != "unknown"
            ]
            if doms:
                milestones.append({
                    "date": "",
                    "what": "dominios que observo hoy: " + ", ".join(doms[:6]),
                })
        except Exception:
            pass

        # Orden: con fecha primero (asc), luego los sin fecha
        dated = sorted([m for m in milestones if m.get("date")], key=lambda m: m["date"])
        undated = [m for m in milestones if not m.get("date")]
        return (dated + undated)[:limit]

    return _cached(f"milestones:{limit}", _produce) or []


# ---------------------------------------------------------------------------
# 3) PROCEDENCIA — ¿cómo sé lo que sé?
# ---------------------------------------------------------------------------

def trace_provenance(
    topic: Optional[str] = None,
    domain: Optional[str] = None,
    topic_tokens: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Traza la evidencia que sostiene lo que Vectrax afirma saber.

    Reúne, reutilizando fuentes existentes:
      - estrellas gravitacionales (fingerprint, hits, cc, freq, desde cuándo)
      - observaciones autónomas (conteo + desde) por dominio
      - convergencias activas relevantes (intent, cc, hits)
      - conteos del censo (estrellas del dominio, constelaciones)

    Devuelve {"evidence": {...}, "domain": d, "text": "<bloque>"}.
    text == "" cuando NO hay evidencia real (no inventa).
    """
    key = f"prov:{domain or ''}:{'|'.join(topic_tokens or [])}:{(topic or '')[:40]}"

    def _produce() -> Dict[str, Any]:
        resolved_domain = domain
        if resolved_domain is None and topic:
            try:
                from core.learn.criterion import detect_domain
                resolved_domain = detect_domain(topic)
            except Exception:
                resolved_domain = None

        tokens = [t.lower() for t in (topic_tokens or []) if t]

        evidence: Dict[str, Any] = {
            "domain": resolved_domain,
            "stars": [],
            "observations": {},
            "convergences": [],
            "census": {},
        }

        def _matches(text_val: str) -> bool:
            if not tokens:
                return True
            low = (text_val or "").lower()
            return any(tok in low for tok in tokens)

        # --- Estrellas gravitacionales ---
        try:
            from core.learn.gravity_engine import get_gravity_index
            gi = get_gravity_index()
            recs = gi.top_stars(n=12, domain=resolved_domain) if resolved_domain \
                else gi.top_stars(n=12)
            picked = [r for r in recs if _matches(getattr(r, "intent", "") or "")
                      or _matches(getattr(r, "fingerprint", "") or "")
                      or _matches(getattr(r, "summary", "") or "")]
            if not picked:
                picked = recs  # sin match léxico: usa las más pesadas del dominio
            for r in picked[:5]:
                evidence["stars"].append({
                    "fingerprint": getattr(r, "fingerprint", "")[:32],
                    "domain": getattr(r, "domain", ""),
                    "hits": getattr(r, "hits", 0),
                    "cc": round(getattr(r, "cc_score", 0.0), 3),
                    "freq": round(getattr(r, "freq", 0.0), 3),
                    "since": _iso_to_date(getattr(r, "first_seen", "")),
                })
        except Exception as exc:
            logger.debug("provenance stars failed: %s", exc)

        # --- Observaciones autónomas (conteo + desde), por dominio si aplica ---
        try:
            import core.self_observation.observation_ledger as OL
            path = _ledger_db_path()
            if resolved_domain:
                cnt = _sqlite_scalar(
                    path,
                    "SELECT COUNT(*) FROM autonomous_observations WHERE domain=?",
                    (resolved_domain,),
                )
                since = _sqlite_scalar(
                    path,
                    "SELECT MIN(timestamp) FROM autonomous_observations WHERE domain=?",
                    (resolved_domain,),
                )
                latest = OL.get_by_domain(resolved_domain, limit=1)
            else:
                cnt = OL.count()
                since = _sqlite_scalar(
                    path, "SELECT MIN(timestamp) FROM autonomous_observations"
                )
                latest = OL.get_recent(limit=1)
            if cnt:
                evidence["observations"] = {
                    "count": int(cnt),
                    "since": _iso_to_date(since) if since else "",
                    "latest": (latest[0].get("summary", "")[:80] if latest else ""),
                }
        except Exception as exc:
            logger.debug("provenance observations failed: %s", exc)

        # --- Convergencias activas relevantes ---
        try:
            from core.learn.convergence_history import get_active
            active = get_active()
            picked = []
            for a in active:
                blob = f"{a.get('intent','')} {a.get('domains','')}"
                if resolved_domain and resolved_domain not in blob and not _matches(blob):
                    continue
                if not resolved_domain and not _matches(blob):
                    continue
                picked.append(a)
            for a in (picked or active)[:3]:
                evidence["convergences"].append({
                    "intent": a.get("intent", "") or "?",
                    "cc": round(a.get("combined_cc", 0.0), 3),
                    "hits": a.get("combined_hits", 0),
                    "since": _epoch_to_date(a.get("timestamp")),
                })
        except Exception as exc:
            logger.debug("provenance convergences failed: %s", exc)

        # --- Censo (estrellas del dominio + constelaciones) ---
        try:
            from core.universe_census import get_census
            c = get_census()
            evidence["census"] = {
                "domain_stars": int(c.domains.get(resolved_domain, 0)) if resolved_domain else int(c.total),
                "constellations": int(c.constellations),
                "convergences_total": int(c.convergences),
            }
        except Exception as exc:
            logger.debug("provenance census failed: %s", exc)

        text = _render_provenance(evidence, resolved_domain, lang="es")
        return {"evidence": evidence, "domain": resolved_domain, "text": text}

    return _cached(key, _produce) or {"evidence": {}, "domain": domain, "text": ""}


# ---------------------------------------------------------------------------
# Renderizado (primera persona, sin lenguaje genérico, cifras reales)
# ---------------------------------------------------------------------------

def _render_origin(origin: Dict[str, Any], lang: str = "es") -> str:
    if not origin:
        return ""
    inst = origin.get("institutional_birth") or {}
    ft = origin.get("first_trace") or {}
    if not inst.get("date") and not ft.get("date"):
        return ""
    ft_srcs = ", ".join(f"{k}:{v}" for k, v in (ft.get("sources") or {}).items())

    if lang == "en":
        lines = ["[ORIGIN — my verifiable history]"]
        if inst.get("date"):
            age = f" ({inst['age_days']} days ago)" if inst.get("age_days") else ""
            lines.append(
                f"My institutional birth is {inst['date']}{age}, with the "
                f"constitution of {inst.get('entity', 'Vectrax-Core LLC')}."
            )
        if ft.get("date"):
            lines.append(
                f"Before that, my earliest verifiable trace (gestation) is "
                f"{ft['date']} — from: {ft_srcs}."
            )
        return "\n".join(lines)

    lines = ["[ORIGEN — mi historia verificable]"]
    if inst.get("date"):
        age = f" (hace {inst['age_days']} días)" if inst.get("age_days") else ""
        lines.append(
            f"Mi nacimiento institucional es el {inst['date']}{age}, con la "
            f"constitución de {inst.get('entity', 'Vectrax-Core LLC')}."
        )
    if ft.get("date"):
        lines.append(
            f"Antes de eso, mi primera huella verificable (gestación) es del "
            f"{ft['date']} — desde: {ft_srcs}."
        )
    return "\n".join(lines)


def _render_milestones(milestones: List[Dict[str, Any]], lang: str = "es") -> str:
    if not milestones:
        return ""
    head = "[MILESTONES]" if lang == "en" else "[HITOS]"
    lines = [head]
    for m in milestones:
        when = m.get("date") or ("—" if lang == "en" else "—")
        lines.append(f"  {when} · {m.get('what','')}")
    return "\n".join(lines)


def _render_provenance(evidence: Dict[str, Any], domain: Optional[str], lang: str = "es") -> str:
    stars = evidence.get("stars") or []
    obs = evidence.get("observations") or {}
    convs = evidence.get("convergences") or []
    census = evidence.get("census") or {}

    # Sin evidencia real → vacío (no inventa)
    has_any = bool(stars or obs.get("count") or convs or census.get("domain_stars"))
    if not has_any:
        return ""

    if lang == "en":
        head = "[PROVENANCE — how I know what I know]"
        if domain:
            head += f" · domain: {domain}"
        lines = [head]
        if obs.get("count"):
            since = f" since {obs['since']}" if obs.get("since") else ""
            lines.append(f"I observed this {obs['count']} times{since}.")
        for s in stars:
            lines.append(
                f"  star {s['fingerprint']} [{s['domain']}] hits={s['hits']} "
                f"cc={s['cc']} since {s['since']}"
            )
        for cv in convs:
            lines.append(
                f"  convergence {cv['intent']} cc={cv['cc']} hits={cv['hits']} "
                f"since {cv['since']}"
            )
        if census.get("domain_stars"):
            lines.append(
                f"  backed by {census['domain_stars']} stars, "
                f"{census.get('constellations',0)} constellations."
            )
        return "\n".join(lines)

    head = "[PROCEDENCIA — cómo sé lo que sé]"
    if domain:
        head += f" · dominio: {domain}"
    lines = [head]
    if obs.get("count"):
        since = f" desde {obs['since']}" if obs.get("since") else ""
        lines.append(f"Lo observé {obs['count']} veces{since}.")
    for s in stars:
        lines.append(
            f"  estrella {s['fingerprint']} [{s['domain']}] hits={s['hits']} "
            f"cc={s['cc']} desde {s['since']}"
        )
    for cv in convs:
        lines.append(
            f"  convergencia {cv['intent']} cc={cv['cc']} hits={cv['hits']} "
            f"desde {cv['since']}"
        )
    if census.get("domain_stars"):
        lines.append(
            f"  sostenido por {census['domain_stars']} estrellas, "
            f"{census.get('constellations',0)} constelaciones."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orquestador — inyectable en build_self_context()
# ---------------------------------------------------------------------------

def build_self_knowledge_context(
    query: str,
    lang: str = "es",
    user_id: str = "",
) -> str:
    """Devuelve SOLO los bloques pertinentes a la pregunta (origen/hitos/procedencia).

    Reconstruye en vivo desde estado real. Cadena vacía si el query no implica
    ninguna de estas preguntas o si no hay evidencia.
    """
    if not query:
        return ""
    parts: List[str] = []

    try:
        if is_origin_question(query):
            ob = _render_origin(get_origin(), lang)
            if ob:
                parts.append(ob)
            mb = _render_milestones(get_milestones(), lang)
            if mb:
                parts.append(mb)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("self_knowledge origin block failed: %s", exc)

    try:
        if is_provenance_question(query):
            tokens = None
            domain = None
            try:
                from core.learn.criterion import extract_topic_tokens, detect_domain
                tokens = extract_topic_tokens(query)
                domain = detect_domain(query)
            except Exception:
                pass
            prov = trace_provenance(topic=query, domain=domain, topic_tokens=tokens)
            if prov.get("text"):
                parts.append(prov["text"])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("self_knowledge provenance block failed: %s", exc)

    return "\n\n".join(parts)
