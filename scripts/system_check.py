#!/usr/bin/env python3
"""Quick system health check — run inside the container."""
import time, os, sys, sqlite3
sys.path.insert(0, "/app")

print("=" * 60)
print("  VECTRAX SYSTEM ANALYSIS")
print("=" * 60)

# 1. Universe
print("\n[1] UNIVERSE")
from vectrax.db import init_db, get_universe_status, get_all_user_stars, get_pattern_count
init_db()
u = get_universe_status()
print(f"  Stars: {u['stars']} | Patterns: {u['patterns']} | Mass: {u['total_mass']:.4f}")
print(f"  Layers: {u['layers']}")

# 2. Top stars
print("\n[2] TOP STARS")
for s in get_all_user_stars()[:5]:
    pc = get_pattern_count(s.user_id)
    print(f"  {s.layer:5s} mass={s.mass:.4f} pat={pc:3d} act={s.activation_count:3d} | {s.role:7s} | {s.user_id}")

# 3. Temporal
print("\n[3] TEMPORAL")
from vectrax.temporal_context import build_temporal_context
print(f"  {build_temporal_context('es').split(chr(10))[1]}")

# 4. Market
print("\n[4] MARKET")
try:
    from services.market_router import get_crypto_spot
    r = get_crypto_spot("BTCUSDT")
    print(f"  BTC: ${r['price']:,.0f} | source={r.get('source','?')} | ok={r['success']}")
except Exception as e:
    print(f"  ERROR: {e}")

# 5. Memory evaluator
print("\n[5] MEMORY EVALUATOR")
from vectrax.memory_evaluator import evaluate_memory_value
for t, label in [("hola","trivial"),("tengo reunion manana a las 10","fact"),("estoy cansado","emotion")]:
    st, sc = evaluate_memory_value(t)
    print(f"  {label:8s} -> {st:10s} ({sc:.2f})")

# 6. Intake
print("\n[6] INTAKE FILTER")
from core.intake_filter import evaluate_intake
for t in ["hola vectrax","precio del btc","estoy triste","recuerdame llamar a Carlos","ok"]:
    r = evaluate_intake(t, "test")
    print(f"  {r.action.value:8s} {r.reason:25s} | {t}")

# 7. Nucleus
print("\n[7] NUCLEUS")
from vectrax.nucleus_resolver import NUCLEUS_RESOLVE_THRESHOLD, MIN_PATTERNS_FOR_SYNTHESIS
print(f"  Threshold: {NUCLEUS_RESOLVE_THRESHOLD} | Min patterns: {MIN_PATTERNS_FOR_SYNTHESIS}")

# 8. Pipeline E2E
print("\n[8] PIPELINE E2E")
from core.operator.external_gateway import ExternalGateway
gw = ExternalGateway()
for msg, label in [("hola vectrax","greet"),("precio del bitcoin","market"),("estoy cansado","emotion"),("que hora es","time")]:
    t0 = time.time()
    r = gw.receive_message(user_id="tg:2030762343", content=msg, channel="telegram")
    ms = (time.time() - t0) * 1000
    resp = r.response[:60] if r.response else "(EMPTY!)"
    print(f"  {ms:5.0f}ms | {label:7s} | {resp}")

# 9. DB
print("\n[9] DB HEALTH")
conn = sqlite3.connect(os.path.expanduser("~/.vectrax/vectrax.db"))
for t in ["user_stars","patterns","stars","queries"]:
    try:
        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {c}")
    except:
        print(f"  {t}: N/A")
conn.close()

print("\n" + "=" * 60)
