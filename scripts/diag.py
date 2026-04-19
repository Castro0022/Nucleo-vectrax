#!/usr/bin/env python3
"""Diagnóstico completo de Vectrax."""
import sys, os, time, sqlite3
sys.path.insert(0, "/app")
from dotenv import load_dotenv
load_dotenv("/app/.env")

SEP = "=" * 55

print(SEP)
print("  VECTRAX — DIAGNÓSTICO COMPLETO")
print(SEP)

# 1. SISTEMA
print("\n[1] SISTEMA")
try:
    from core.operator.system_monitor import collect_metrics
    m = collect_metrics()
    print(f"  Estado:        {m.status.upper()}")
    print(f"  RAM:           {m.memory_mb} MB")
    print(f"  Latencia avg:  {m.avg_latency_s}s | max {m.max_latency_s}s")
    print(f"  Worker:        {'VIVO' if m.worker_alive else 'MUERTO'} ({m.worker_heartbeat_age_s:.0f}s)")
    print(f"  Cola:          {m.queue_pending} pend | {m.queue_processing} proc | {m.queue_done} done | {m.queue_error} err")
except Exception as e:
    print(f"  ERROR: {e}")

# 2. MEMORIA
print("\n[2] MEMORIA")
db = "/app/vault/user_memory.db"
try:
    conn = sqlite3.connect(db)
    users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM profiles").fetchone()[0]
    interactions = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    core_total = conn.execute("SELECT COUNT(*) FROM core_memory").fetchone()[0]
    facts = conn.execute("SELECT COUNT(*) FROM user_facts WHERE status='active'").fetchone()[0]
    teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    members = conn.execute("SELECT COUNT(*) FROM team_members").fetchone()[0]
    langs = conn.execute("SELECT language, COUNT(*) FROM profiles GROUP BY language ORDER BY 2 DESC").fetchall()
    top_core = conn.execute("SELECT category, content FROM core_memory ORDER BY weight DESC LIMIT 5").fetchall()
    conn.close()
    print(f"  Usuarios:      {users}")
    print(f"  Interacciones: {interactions}")
    print(f"  Core entries:  {core_total}")
    print(f"  Facts activos: {facts}")
    print(f"  Equipos:       {teams} ({members} miembros)")
    print(f"  Idiomas:       {dict(langs)}")
    if top_core:
        print("  Top core_memory:")
        for cat, content in top_core:
            print(f"    [{cat}] {content[:60]}")
except Exception as e:
    print(f"  ERROR: {e}")

# 3. BÚSQUEDA ONLINE
print("\n[3] BÚSQUEDA ONLINE")
tavily  = bool(os.environ.get("TAVILY_API_KEY", ""))
brave   = bool(os.environ.get("BRAVE_API_KEY", ""))
gcse    = bool(os.environ.get("GOOGLE_CSE_KEY", "") or os.environ.get("GOOGLE_CSE_CX", ""))
places  = bool(os.environ.get("GOOGLE_PLACES_API_KEY", ""))
print(f"  Tavily:        {'✓ activo' if tavily else '✗ sin clave'}")
print(f"  Brave:         {'✓ activo' if brave else '✗ sin clave'}")
print(f"  Google CSE:    {'✓ activo' if gcse else '✗ sin clave'}")
print(f"  Google Places: {'✓ activo' if places else '✗ sin clave'}")
print(f"  Jina Reader:   ✓ activo (sin clave)")

# Test rápido Tavily
if tavily:
    try:
        from vectrax.resolver import _search_tavily
        r = _search_tavily("test", max_results=1)
        print(f"  Tavily test:   {'✓ responde' if r else '✗ sin resultados'}")
    except Exception as e:
        print(f"  Tavily test:   ✗ {e}")

# 4. APIs EXTERNAS
print("\n[4] APIs EXTERNAS")
openai_k  = bool(os.environ.get("OPENAI_API_KEY", ""))
stripe_k  = bool(os.environ.get("STRIPE_SECRET_KEY", ""))
stripe_t  = bool(os.environ.get("STRIPE_TEAM_PRICE_ID", ""))
stripe_p  = bool(os.environ.get("STRIPE_PRICE_ID", ""))
tg_k      = bool(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
print(f"  OpenAI:        {'✓' if openai_k else '✗ CRÍTICO — sin LLM'}")
print(f"  Telegram:      {'✓' if tg_k else '✗ CRÍTICO'}")
print(f"  Stripe PRO:    {'✓ $15/mes' if stripe_p else '✗'}")
print(f"  Stripe TEAM:   {'✓ $99/mes' if stripe_t else '✗'}")

# 5. CICLO OPERATIVO
print("\n[5] CICLO OPERATIVO")
try:
    from core.operational_cycle import get_cycle_stats
    s = get_cycle_stats(days=7)
    print(f"  Ciclos 7d:     {s['total']} ({s['success_rate']}% exitosos)")
    print(f"  Vacíos:        {s['empty']} | Reescritos: {s['rewritten']}")
    print(f"  Latencia avg:  {s['avg_latency_ms']:.0f}ms")
    print(f"  Ruta dominante:{s['top_route']}")
    print(f"  Intent difícil:{s['hard_intent']}")
    if s['routes_latency']:
        print("  Latencia por ruta:")
        for r in s['routes_latency']:
            print(f"    {r['route']}: {r['avg_ms']:.0f}ms ({r['count']}x)")
except Exception as e:
    print(f"  ERROR: {e}")

# 6. FALLBACKS
print("\n[6] FALLBACKS 7d")
try:
    from core.fallback_intents import get_summary, get_top_fallbacks
    fb = get_summary()
    top = get_top_fallbacks(limit=5)
    print(f"  Total:         {fb['total']}")
    print(f"  Intent más dif:{fb['top_intent']}")
    print(f"  Razón top:     {fb['top_reason']}")
    if top:
        print("  Top fallbacks:")
        for f in top:
            print(f"    {f['intent_category']}: {f['count']}x")
except Exception as e:
    print(f"  ERROR: {e}")

# 7. INTENCIÓN PROYECTADA
print("\n[7] INTENCIÓN PROYECTADA")
try:
    conn4 = sqlite3.connect(db)
    uids = conn4.execute("SELECT DISTINCT user_id FROM profiles WHERE user_id LIKE 'tg:%'").fetchall()
    conn4.close()
    from core.intention_projector import project_user_intention
    for (uid,) in uids[:5]:
        proj = project_user_intention(uid)
        if proj:
            print(f"  {uid[:20]}: [{proj.confidence}] {proj.label}")
        else:
            print(f"  {uid[:20]}: sin patrón claro")
except Exception as e:
    print(f"  ERROR: {e}")

# 8. MOTOR PROACTIVO
print("\n[8] MOTOR PROACTIVO")
try:
    db2 = "/app/vault/proactive_sent.db"
    conn2 = sqlite3.connect(db2)
    sent = conn2.execute("SELECT COUNT(*) FROM proactive_sent").fetchone()[0]
    recent = conn2.execute(
        "SELECT user_id, sent_at FROM proactive_sent ORDER BY sent_at DESC LIMIT 3"
    ).fetchall()
    conn2.close()
    print(f"  Mensajes enviados: {sent}")
    for uid, ts in recent:
        elapsed = (time.time() - ts) / 3600
        print(f"    {uid[:20]} — hace {elapsed:.1f}h")
except Exception:
    print("  Sin actividad aún")

# 9. TIERS
print("\n[9] TIERS")
try:
    conn3 = sqlite3.connect(db)
    tiers = conn3.execute("SELECT tier, COUNT(*) FROM user_tiers GROUP BY tier").fetchall()
    conn3.close()
    if tiers:
        for t, c in tiers:
            print(f"  {t}: {c}")
    else:
        print("  Todos en FREE (sin tabla user_tiers poblada)")
except Exception as e:
    print(f"  ERROR: {e}")

# 10. MÓDULOS CRÍTICOS
print("\n[10] MÓDULOS CRÍTICOS")
checks = [
    ("telegram_gateway",    "vectrax.telegram_gateway"),
    ("external_gateway",    "core.operator.external_gateway"),
    ("intake_filter",       "core.intake_filter"),
    ("smart_router",        "core.smart_router"),
    ("resolver",            "vectrax.resolver"),
    ("response_auditor",    "vectrax.response_auditor"),
    ("self_context",        "vectrax.self_context"),
    ("intention_projector", "core.intention_projector"),
    ("proactive_engine",    "core.proactive_engine"),
    ("operational_cycle",   "core.operational_cycle"),
    ("team_memory",         "vectrax.team_memory"),
    ("place_search",        "vectrax.integrations.place_search"),
    ("core_memory",         "vectrax.core_memory"),
    ("identity_layer",      "vectrax.identity_layer"),
    ("load_governor",       "core.operator.load_governor"),
]
ok = 0
fail = 0
for name, mod in checks:
    try:
        __import__(mod)
        print(f"  {name}: ✓")
        ok += 1
    except Exception as e:
        print(f"  {name}: ✗  {str(e)[:60]}")
        fail += 1

print(f"\n  Resultado: {ok}/{ok+fail} módulos OK")

print("\n" + SEP)
estado = "HEALTHY" if fail == 0 else f"DEGRADADO ({fail} módulos con errores)"
print(f"  Estado final: {estado}")
print(SEP)
