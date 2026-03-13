import os, json, time, sqlite3, asyncio
from typing import List, Dict, Any, Optional

import numpy as np
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from core.strategic_router import StrategicRouter, RoutingMode, RoutingDecision, RiskLevel

load_dotenv()

# --- CONFIG ---
DB_PATH = os.getenv("VECTRAX_DB", "vectrax.db")
EMBED_MODEL = os.getenv("VECTRAX_EMBED_MODEL", "all-MiniLM-L6-v2")

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-1.5-flash").strip()

# Autopilot DURO — sin override manual
AUTOPILOT = True  # siempre activo, no configurable

VERSION = "2.1.0"

CREDITS = {
    "openai": {
        "role": "LLM principal + fusión",
        "model": OPENAI_MODEL,
        "api": "https://platform.openai.com",
        "note": "Todas las llamadas de generación y fusión usan la API de OpenAI.",
    },
    "gemini": {
        "role": "LLM secundario + validación cruzada",
        "model": GEMINI_MODEL,
        "api": "https://ai.google.dev",
    },
    "embeddings": {
        "library": "sentence-transformers",
        "model": EMBED_MODEL,
        "source": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2",
    },
    "routing": "Strategic Router — SINGLE-first con auto-aprendizaje bayesiano",
    "engine": "Vectrax by Mario Bravo",
}

app = FastAPI(
    title="Vectrax — Orquestador Multi-LLM con Autopilot Duro",
    description=(
        "Motor de gobernanza IA con routing estratégico SINGLE-first, "
        "auto-aprendizaje bayesiano y fusión adaptativa.\n\n"
        "**Modo:** Autopilot duro (sin override manual).\n\n"
        "**Proveedores integrados:**\n"
        f"- OpenAI API ({OPENAI_MODEL}) — LLM principal + fusión\n"
        f"- Google Gemini ({GEMINI_MODEL}) — LLM secundario + validación cruzada\n\n"
        "**Créditos:**\n"
        "- LLM principal y fusión: **OpenAI API** (https://platform.openai.com)\n"
        "- LLM secundario: **Google Generative AI / Gemini** (https://ai.google.dev)\n"
        "- Embeddings: **sentence-transformers/all-MiniLM-L6-v2**\n"
        "- Arquitectura y motor: **Vectrax by Mario Bravo**"
    ),
    version=VERSION,
)
embedder = SentenceTransformer(EMBED_MODEL)
router = StrategicRouter(embedder=embedder, db_path=DB_PATH)

# --- DB ---
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with db() as conn:
        conn.executescript(open("schema.sql", "r", encoding="utf-8").read())
        # Migration: añadir columnas si no existen (DBs antiguas)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(queries)").fetchall()]
        migrations = {
            "consensus_mode":   "INTEGER DEFAULT 0",
            "routing_mode":     "TEXT DEFAULT ''",
            "routing_reason":   "TEXT DEFAULT ''",
            "confidence":       "REAL DEFAULT 0.0",
            "risk_level":       "TEXT DEFAULT ''",
            "estimated_savings": "REAL DEFAULT 0.0",
            "providers_called": "TEXT DEFAULT ''",
            "fallback_used":    "INTEGER DEFAULT 0",
        }
        for col, col_type in migrations.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE queries ADD COLUMN {col} {col_type}")
        conn.commit()

def upsert_weight(conn, provider: str, topic: str):
    conn.execute("""
      INSERT INTO provider_weights(provider, topic, alpha, beta)
      VALUES(?, ?, 1.0, 1.0)
      ON CONFLICT(provider, topic) DO NOTHING
    """, (provider, topic))

def weight_mean(provider: str, topic: str) -> float:
    with db() as conn:
        r = conn.execute(
            "SELECT alpha, beta FROM provider_weights WHERE provider=? AND topic=?",
            (provider, topic)
        ).fetchone()
        if not r:
            return 0.5
        a, b = float(r["alpha"]), float(r["beta"])
        return a / (a + b) if (a + b) > 0 else 0.5

# --- IO MODELS ---
class OrchestrateIn(BaseModel):
    prompt: str
    topic: str = "general"

class FeedbackIn(BaseModel):
    query_id: int
    chosen_provider: str
    reason: str = ""

# --- PROVIDERS (robustos) ---
async def call_openai(prompt: str) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        return {"provider": "openai", "content": "", "latency_ms": 0, "error": "OPENAI_API_KEY missing"}

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {"model": OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers, timeout=40)
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            return {"provider": "openai", "content": "", "latency_ms": latency, "error": f"HTTP {r.status_code}: {r.text[:300]}"}

        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return {"provider": "openai", "content": "", "latency_ms": latency, "error": f"No choices: {json.dumps(data)[:300]}"}

        text = ((choices[0].get("message") or {}).get("content") or "").strip()
        if not text:
            return {"provider": "openai", "content": "", "latency_ms": latency, "error": "Empty content"}

        return {"provider": "openai", "content": text, "latency_ms": latency, "error": ""}

    except Exception as e:
        return {"provider": "openai", "content": "", "latency_ms": 0, "error": str(e)}

async def call_gemini(prompt: str) -> Dict[str, Any]:
    if not GEMINI_API_KEY:
        return {"provider": "gemini", "content": "", "latency_ms": 0, "error": "GEMINI_API_KEY missing"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=40)
        latency = int((time.perf_counter() - t0) * 1000)

        if r.status_code != 200:
            return {"provider": "gemini", "content": "", "latency_ms": latency, "error": f"HTTP {r.status_code}: {r.text[:300]}"}

        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return {"provider": "gemini", "content": "", "latency_ms": latency, "error": f"No candidates: {json.dumps(data)[:300]}"}

        parts = (((candidates[0].get("content") or {}).get("parts")) or [])
        text = ((parts[0].get("text") if parts else "") or "").strip()
        if not text:
            return {"provider": "gemini", "content": "", "latency_ms": latency, "error": "Empty content"}

        return {"provider": "gemini", "content": text, "latency_ms": latency, "error": ""}

    except Exception as e:
        return {"provider": "gemini", "content": "", "latency_ms": 0, "error": str(e)}

# --- FUSIÓN ADAPTATIVA (pesos bayesianos + divergencia) ---
DIVERGENCE_CONSENSUS = 0.4
DIVERGENCE_CRITICAL = 0.6
WEIGHT_DOMINANCE = 0.65

def _build_answer_block(valid_results: List[Dict[str, Any]], topic: str) -> str:
    """Construye bloque de respuestas marcando proveedor preferido según pesos."""
    weights = {r["provider"]: weight_mean(r["provider"], topic) for r in valid_results}
    dominant = max(weights, key=weights.get)
    is_dominant = weights[dominant] > WEIGHT_DOMINANCE

    parts = []
    for i, r in enumerate(valid_results):
        label = f"RESPUESTA {i+1} [{r['provider'].upper()}]"
        if is_dominant and r["provider"] == dominant:
            label += " (★ PREFERIDA — historial demuestra mayor fiabilidad en este tópico)"
        parts.append(f"{label}:\n{r['content']}")
    return "\n\n---\n\n".join(parts)

def fusion_prompt(user_prompt: str, valid_results: List[Dict[str, Any]], topic: str,
                  consensus_mode: bool = False) -> str:
    answer_block = _build_answer_block(valid_results, topic)

    if consensus_mode:
        return f"""
Eres Vectrax en MODO CONSENSO.
Las respuestas de los proveedores divergen significativamente.

REGLAS ESTRICTAS:
1. Extrae SOLO los hechos en los que TODAS las respuestas coinciden.
2. Lista explícitamente los puntos de DESACUERDO.
3. NO inventes información para rellenar huecos.

Devuelve exactamente:
CONSENSO:
DESACUERDOS:
VERIFICAR: (obligatorio en modo consenso)

Consulta:
{user_prompt}

Respuestas:
{answer_block}
""".strip()

    return f"""
Eres Vectrax.
Integra todas las respuestas en UNA sola respuesta final clara y accionable.
Si una respuesta está marcada como PREFERIDA, úsala como base principal y enriquécela con las demás.
NO compares modelos. NO menciones qué modelo dijo qué. NO digas \"A es mejor que B\".

Devuelve exactamente:
RESPUESTA FINAL:
RESUMEN:
VERIFICAR: (si aplica; si no, \"N/A\")

Consulta:
{user_prompt}

Respuestas:
{answer_block}
""".strip()

async def fuse(user_prompt: str, valid_results: List[Dict[str, Any]], topic: str,
              consensus_mode: bool = False) -> str:
    p = fusion_prompt(user_prompt, valid_results, topic, consensus_mode)

    # Preferencia para fusión: OpenAI -> Gemini -> fallback
    o = await call_openai(p)
    if o.get("content"):
        return o["content"]

    g = await call_gemini(p)
    if g.get("content"):
        return g["content"]

    return valid_results[0]["content"]

# --- DIVERGENCIA (métrica interna) ---
def divergence_of(texts: List[str]) -> float:
    if len(texts) <= 1:
        return 0.0
    V = embedder.encode(texts, normalize_embeddings=True)
    V = np.asarray(V, dtype=np.float32)
    c = V.mean(axis=0)
    c = c / (np.linalg.norm(c) + 1e-12)
    sims = V @ c
    d = float(1.0 - float(np.mean(sims)))
    return max(0.0, min(1.0, d))

# --- ENDPOINTS ---
@app.on_event("startup")
def _startup():
    init_db()

@app.get("/health")
def health():
    return {
        "ok": True,
        "version": VERSION,
        "autopilot": True,
        "override": False,
        "db": DB_PATH,
        "providers": {
            "openai": {"active": bool(OPENAI_API_KEY), "model": OPENAI_MODEL},
            "gemini": {"active": bool(GEMINI_API_KEY), "model": GEMINI_MODEL},
        },
        "embed_model": EMBED_MODEL,
        "credits": CREDITS,
    }

@app.get("/about")
def about():
    """Información del sistema y créditos."""
    return {
        "name": "Vectrax",
        "version": VERSION,
        "description": "Orquestador Multi-LLM con Autopilot Duro — routing estratégico, auto-aprendizaje y fusión adaptativa. Sin override manual.",
        "author": "Mario Bravo",
        "mode": "AUTOPILOT_DURO",
        "credits": CREDITS,
    }

@app.post("/route")
def route_debug(q: OrchestrateIn):
    """Devuelve SOLO la decisión del router (útil para debug/testing). Sin override."""
    decision = router.route(q.prompt)  # autopilot duro: sin explicit_topic
    return {
        **decision.full_dict(),
        "autopilot": True,
        "override": False,
    }


@app.post("/orchestrate")
async def orchestrate(q: OrchestrateIn):
    prompt = (q.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt vacío")

    init_db()

    # --- STRATEGIC ROUTER (autopilot duro: sin override) ---
    decision = router.route(prompt)  # sin explicit_topic
    topic = decision.topic
    mode  = decision.mode
    fallback_used = False

    # --- LLAMADAS SEGÚN MODO ---
    PROVIDER_CALLS = {"openai": call_openai, "gemini": call_gemini}

    if mode == RoutingMode.SINGLE:
        primary = decision.providers[0]
        result = await PROVIDER_CALLS[primary](prompt)
        results = [result]

        # Fallback automático si el primario falla
        if (not (result.get("content") or "").strip() or result.get("error")) and len(decision.providers) > 1:
            fallback = decision.providers[1]
            fb_result = await PROVIDER_CALLS[fallback](prompt)
            results.append(fb_result)
            mode = RoutingMode.FALLBACK_CHAIN
            decision.mode = mode
            decision.reason += f" → Fallback a {fallback} (primario falló)."
            fallback_used = True

    else:
        # DUAL (FUSE o CONSENSUS)
        results = list(await asyncio.gather(call_openai(prompt), call_gemini(prompt)))

        # Fallback automático en DUAL: si ambos fallan, intentar uno a uno
        valid_dual = [r for r in results if (r.get("content") or "").strip() and not r.get("error")]
        if not valid_dual:
            # Ambos fallaron → reintentar cada uno secuencialmente
            for pname in decision.providers:
                fb_result = await PROVIDER_CALLS[pname](prompt)
                if (fb_result.get("content") or "").strip() and not fb_result.get("error"):
                    results.append(fb_result)
                    mode = RoutingMode.FALLBACK_CHAIN
                    decision.mode = mode
                    decision.reason += f" → Fallback DUAL a {pname} (ambos fallaron)."
                    fallback_used = True
                    break
        elif len(valid_dual) == 1:
            # Solo uno respondió → marcar fallback
            failed_provider = [r["provider"] for r in results if r not in valid_dual][0]
            decision.reason += f" → {failed_provider} falló, usando {valid_dual[0]['provider']} solo."
            fallback_used = True

    providers_called = ",".join(r["provider"] for r in results)

    # --- PERSISTIR query + auditoría completa ---
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO queries("
            "  prompt, topic, routing_mode, routing_reason,"
            "  confidence, risk_level, estimated_savings,"
            "  providers_called, fallback_used"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (prompt, topic, mode.value, decision.reason,
             decision.confidence, decision.risk_level.value,
             decision.estimated_savings, providers_called,
             int(fallback_used))
        )
        qid = int(cur.lastrowid)
        for r in results:
            conn.execute(
                "INSERT INTO responses(query_id, provider, content, latency_ms, error) VALUES (?,?,?,?,?)",
                (qid, r["provider"], r.get("content",""), int(r.get("latency_ms",0)), r.get("error",""))
            )
        conn.commit()

    # Solo contenidos válidos
    valid = [r for r in results if (r.get("content") or "").strip() and not r.get("error")]
    if not valid:
        # Auto-learn incluso en fallo (penaliza proveedores)
        router.auto_learn(topic, results, divergence=0.0)
        return {
            "query_id": qid,
            "final": "",
            "divergence": None,
            "routing": {
                **decision.full_dict(),
                "providers_called": providers_called,
                "fallback_used": fallback_used,
            },
            "error": "Ningún proveedor devolvió contenido. Revisa tus API keys en .env."
        }

    # --- DIVERGENCIA + FUSIÓN ---
    if len(valid) == 1:
        final = valid[0]["content"]
        div = 0.0
        consensus = False
    else:
        texts = [r["content"].strip() for r in valid]
        div = divergence_of(texts)
        consensus = (mode == RoutingMode.DUAL_CONSENSUS) or (div > DIVERGENCE_CONSENSUS)
        final = await fuse(prompt, valid, topic, consensus_mode=consensus)

    with db() as conn:
        conn.execute(
            "UPDATE queries SET final=?, divergence=?, consensus_mode=? WHERE id=?",
            (final, float(div), int(consensus), qid)
        )
        conn.commit()

    # --- AUTO-APRENDIZAJE (after hook — silencioso) ---
    router.auto_learn(topic, results, divergence=div)

    response = {
        "query_id": qid,
        "final": final,
        "divergence": div,
        "consensus_mode": consensus,
        "routing": {
            **decision.full_dict(),
            "providers_called": providers_called,
            "fallback_used": fallback_used,
        },
    }

    if div > DIVERGENCE_CRITICAL:
        response["raw_responses"] = {r["provider"]: r["content"] for r in valid}

    return response

@app.get("/history")
def history(
    topic: str = "",
    q: str = "",
    limit: int = 20,
    only_divergent: int = 0,
    min_divergence: float = 0.0
):
    """
    Ledger útil:
    - topic: filtra por tópico
    - q: búsqueda simple dentro del prompt
    - only_divergent=1: solo entradas con divergencia > 0
    - min_divergence: umbral mínimo
    """
    where = []
    params = []
    if topic.strip():
        where.append("topic=?")
        params.append(topic.strip())
    if q.strip():
        where.append("prompt LIKE ?")
        params.append(f"%{q.strip()}%")
    if only_divergent:
        where.append("divergence > ?")
        params.append(max(0.0, float(min_divergence)))
    elif min_divergence > 0:
        where.append("divergence >= ?")
        params.append(float(min_divergence))

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(limit), 200))

    with db() as conn:
        rows = conn.execute(
            f"SELECT id, topic, prompt, divergence, created_at FROM queries {where_sql} ORDER BY id DESC LIMIT ?",
            (*params, limit)
        ).fetchall()

    return {"items": [dict(r) for r in rows]}

@app.get("/weights")
def weights(topic: str = ""):
    with db() as conn:
        if topic.strip():
            rows = conn.execute(
                "SELECT provider, topic, alpha, beta, (alpha*1.0)/(alpha+beta) AS mean, updated_at FROM provider_weights WHERE topic=? ORDER BY mean DESC",
                (topic.strip(),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT provider, topic, alpha, beta, (alpha*1.0)/(alpha+beta) AS mean, updated_at FROM provider_weights ORDER BY topic, mean DESC"
            ).fetchall()
    return {"items": [dict(r) for r in rows]}

@app.post("/feedback")
def feedback(payload: FeedbackIn):
    chosen = payload.chosen_provider.strip().lower()
    if chosen not in ("openai", "gemini"):
        raise HTTPException(400, "chosen_provider debe ser 'openai' o 'gemini'")

    with db() as conn:
        qrow = conn.execute("SELECT topic FROM queries WHERE id=?", (payload.query_id,)).fetchone()
        if not qrow:
            raise HTTPException(404, "query_id no existe")
        topic = qrow["topic"]

        # registra feedback
        conn.execute(
            "INSERT INTO feedback(query_id, topic, chosen_provider, reason) VALUES (?,?,?,?)",
            (payload.query_id, topic, chosen, payload.reason or "")
        )

        # refuerzo bayesiano: elegido alpha++, el otro beta++
        upsert_weight(conn, "openai", topic)
        upsert_weight(conn, "gemini", topic)

        conn.execute(
            "UPDATE provider_weights SET alpha=alpha+1, updated_at=CURRENT_TIMESTAMP WHERE provider=? AND topic=?",
            (chosen, topic)
        )
        other = "gemini" if chosen == "openai" else "openai"
        conn.execute(
            "UPDATE provider_weights SET beta=beta+1, updated_at=CURRENT_TIMESTAMP WHERE provider=? AND topic=?",
            (other, topic)
        )

        conn.commit()

    return {"ok": True, "topic": topic, "chosen_provider": chosen}

@app.get("/routing-stats")
def routing_stats():
    """Estadísticas del Strategic Router: distribución de modos y ahorro."""
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM queries WHERE routing_mode != ''").fetchone()["c"]
        if total == 0:
            return {"total_routed": 0, "modes": [], "single_savings_pct": 0.0}

        modes = conn.execute(
            "SELECT routing_mode, COUNT(*) AS cnt FROM queries "
            "WHERE routing_mode != '' GROUP BY routing_mode ORDER BY cnt DESC"
        ).fetchall()
        mode_dist = [{"mode": r["routing_mode"], "count": int(r["cnt"]),
                      "pct": round(int(r["cnt"]) / total * 100, 1)} for r in modes]

        # Ahorro: queries SINGLE o FALLBACK_CHAIN que evitaron llamada dual
        single_count = conn.execute(
            "SELECT COUNT(*) AS c FROM queries WHERE routing_mode IN ('SINGLE', 'FALLBACK_CHAIN')"
        ).fetchone()["c"]
        savings = round(single_count / total * 100, 1) if total else 0.0

        # Tópicos más ruteados
        topics = conn.execute(
            "SELECT topic, COUNT(*) AS cnt, "
            "       GROUP_CONCAT(DISTINCT routing_mode) AS modes_used "
            "FROM queries WHERE routing_mode != '' "
            "GROUP BY topic ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        topic_dist = [{"topic": r["topic"], "count": int(r["cnt"]),
                       "modes_used": r["modes_used"]} for r in topics]

    return {
        "total_routed": total,
        "modes": mode_dist,
        "single_savings_pct": savings,
        "top_topics": topic_dist,
    }


@app.get("/daily-report")
def daily_report(date: str = ""):
    """Reporte Diario de Estado consolidado.
    - Sin parámetros: genera reporte del día actual.
    - ?date=YYYY-MM-DD: carga reporte guardado o genera para esa fecha.
    """
    from core.daily_report import generate_report, save_report, load_saved_report

    target_date = date.strip() if date.strip() else None

    # Si piden fecha específica, intentar cargar reporte guardado
    if target_date:
        saved = load_saved_report(target_date)
        if saved:
            return saved

    report = generate_report(target_date=target_date)
    save_report(report)
    return report


@app.get("/analytics")
def analytics(topic: str = ""):
    """Dashboard analítico: divergencia, dominancia, latencia, tendencia."""
    topic_filter = topic.strip()
    t_where = "WHERE topic=?" if topic_filter else ""
    t_params = (topic_filter,) if topic_filter else ()

    with db() as conn:
        # --- Divergencia global ---
        row = conn.execute(
            f"SELECT COUNT(*) AS total, AVG(divergence) AS avg_div FROM queries {t_where}",
            t_params
        ).fetchone()
        total_queries = int(row["total"] or 0)
        avg_divergence = round(float(row["avg_div"] or 0), 4)

        # --- Ratio alta divergencia ---
        hd = conn.execute(
            f"SELECT COUNT(*) AS c FROM queries {('WHERE topic=? AND' if topic_filter else 'WHERE')} divergence > ?",
            (*t_params, DIVERGENCE_CONSENSUS)
        ).fetchone()
        high_div_count = int(hd["c"] or 0)
        high_divergence_ratio = round(high_div_count / total_queries, 4) if total_queries else 0.0

        # --- Divergencia por día (últimos 30 días) ---
        day_sql = f"""
            SELECT DATE(created_at) AS date, AVG(divergence) AS avg_div, COUNT(*) AS count
            FROM queries
            {t_where + (' AND' if topic_filter else 'WHERE')} created_at >= DATE('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """
        day_rows = conn.execute(day_sql, t_params).fetchall()
        divergence_by_day = [
            {"date": r["date"], "avg_div": round(float(r["avg_div"] or 0), 4), "count": int(r["count"])}
            for r in day_rows
        ]

        # --- Dominancia por proveedor (feedback) ---
        fb_sql = f"""
            SELECT chosen_provider, COUNT(*) AS wins
            FROM feedback {('WHERE topic=?' if topic_filter else '')}
            GROUP BY chosen_provider ORDER BY wins DESC
        """
        fb_rows = conn.execute(fb_sql, t_params).fetchall()
        total_fb = sum(int(r["wins"]) for r in fb_rows) or 1
        provider_dominance = [
            {"provider": r["chosen_provider"], "wins": int(r["wins"]), "ratio": round(int(r["wins"]) / total_fb, 4)}
            for r in fb_rows
        ]

        # --- Latencia promedio por proveedor ---
        lat_sql = """
            SELECT r.provider, AVG(r.latency_ms) AS avg_ms, COUNT(*) AS calls
            FROM responses r
            JOIN queries q ON r.query_id = q.id
            {where}
            GROUP BY r.provider
        """.format(where=f"WHERE q.topic=?" if topic_filter else "")
        lat_rows = conn.execute(lat_sql, t_params).fetchall()
        avg_latency = [
            {"provider": r["provider"], "avg_ms": round(float(r["avg_ms"] or 0), 1), "calls": int(r["calls"])}
            for r in lat_rows
        ]

    return {
        "topic": topic_filter or "(all)",
        "total_queries": total_queries,
        "avg_divergence": avg_divergence,
        "high_divergence_ratio": high_divergence_ratio,
        "divergence_by_day": divergence_by_day,
        "provider_dominance": provider_dominance,
        "avg_latency": avg_latency
    }

if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="127.0.0.1", port=8000)
