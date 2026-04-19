#!/usr/bin/env python3
import sys, sqlite3
from datetime import datetime
sys.path.insert(0, "/app")
from dotenv import load_dotenv; load_dotenv("/app/.env")

print("Hoy:", datetime.now().strftime("%A %Y-%m-%d"), "| weekday:", datetime.now().weekday())
print()

db = "/app/vault/user_memory.db"
conn = sqlite3.connect(db)
rows = conn.execute("SELECT user_id, fact_type, subject, value, detail FROM user_facts WHERE status='active'").fetchall()
print("=== user_facts activos ===")
for r in rows: print(" ", r)
print()
conn.close()

from core.proactive_engine import _days_until
for text in ["jueves", "viernes", "sabado", "mañana", "hoy", "lunes", "martes", "miercoles"]:
    print(f"  _days_until({text!r}) = {_days_until(text)}")
print()

from core.proactive_engine import _scan_date_facts, _scan_goal_facts, _scan_connections
conn = sqlite3.connect(db)
uids = conn.execute("SELECT DISTINCT user_id FROM profiles WHERE user_id LIKE 'tg:%'").fetchall()
conn.close()
print("=== Scan por usuario ===")
for (uid,) in uids[:5]:
    dates = _scan_date_facts(uid)
    goals = _scan_goal_facts(uid)
    conns = _scan_connections(uid)
    total = len(dates) + len(goals) + len(conns)
    print(f"  {uid[:25]}: dates={len(dates)} goals={len(goals)} conns={len(conns)}")
    for t in dates + goals + conns:
        print(f"    -> days_until={t.get('days_until','?')} | {str(t)[:80]}")
