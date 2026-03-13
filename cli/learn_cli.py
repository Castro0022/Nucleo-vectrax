"""
Vectrax Learn CLI
==================
Subcommands under ``vx learn``:
  watch           Start watcher daemon
  stop            Stop watcher
  scan            Full repository scan
  report          Show candidates + clusters
  approve <id>    Approve candidate
  ban <id>        Ban candidate
  stats           Show learning statistics
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def handle_learn_command(args: list[str]) -> None:
    """Dispatch ``vx learn <subcommand>``."""
    sub = args[0] if args else "help"

    if sub == "watch":
        _handle_watch(args[1:])
    elif sub == "stop":
        _handle_stop()
    elif sub == "scan":
        _handle_scan(args[1:])
    elif sub == "report":
        _handle_report()
    elif sub == "approve":
        if len(args) < 2:
            print("Usage: vx learn approve <candidate_id>")
            sys.exit(1)
        _handle_approve(args[1])
    elif sub == "ban":
        if len(args) < 2:
            print("Usage: vx learn ban <candidate_id>")
            sys.exit(1)
        _handle_ban(args[1])
    elif sub == "stats":
        _handle_stats()
    elif sub == "gravity":
        _handle_gravity(args[1:])
    else:
        _print_learn_help()


# ===================================================================
# Subcommand handlers
# ===================================================================

def _handle_watch(args: list[str]) -> None:
    """Start the silent watchdog daemon."""
    from core.learn.watchdog import start_watcher, is_running, watcher_status

    fg = "--foreground" in args or "--fg" in args
    no_semantic = "--no-semantic" in args

    if is_running():
        st = watcher_status()
        print(f"Watchdog already running (PID {st['pid']})")
        return

    use_semantic = not no_semantic
    print("Starting watchdog daemon...")
    pid = start_watcher(foreground=fg, use_semantic=use_semantic)
    if not fg:
        print(f"Watchdog started (PID {pid})")
        print("  Watching: core/, pipeline/, cli/, models/, services/")
        print("  Use 'vx learn stop' to stop")
        print("  Use 'vx learn report' to see results")


def _handle_stop() -> None:
    """Stop the watchdog daemon."""
    from core.learn.watchdog import stop_watcher, is_running

    if not is_running():
        print("Watchdog is not running")
        return

    if stop_watcher():
        print("Watchdog stopped")
    else:
        print("Failed to stop watchdog")


def _handle_scan(args: list[str]) -> None:
    """Run a full repository scan."""
    no_semantic = "--no-semantic" in args
    use_semantic = not no_semantic

    print("Running full repository scan...")
    from core.learn.ingestion import full_scan
    summary = full_scan(use_semantic=use_semantic)

    print(f"\nScan complete:")
    print(f"  Files scanned:  {summary['files_scanned']}")
    print(f"  Files skipped:  {summary['files_skipped']}")
    print(f"  Elapsed:        {summary['elapsed_seconds']}s")
    if summary.get("intents"):
        print(f"  Intents:")
        for intent, count in sorted(summary["intents"].items(), key=lambda x: -x[1]):
            print(f"    {intent:15s} {count}")


def _handle_report() -> None:
    """Show candidates and clusters."""
    from core.learn.ascension_gate import get_gate
    from core.learn.episodic import get_ledger

    gate = get_gate()
    ledger = get_ledger()

    counts = gate.count_by_state()
    total = sum(counts.values())

    print(f"\n{'='*55}")
    print(f"  VECTRAX LEARN REPORT")
    print(f"{'='*55}")

    # Candidate counts
    print(f"\n  Candidates: {total}")
    for state, count in counts.items():
        icon = {"QUARANTINED": "?", "APPROVED": "+", "BANNED": "x"}.get(state, " ")
        print(f"    [{icon}] {state:15s} {count}")

    # Recent 24h events
    recent = ledger.since(hours=24)
    print(f"\n  Events (last 24h): {len(recent)}")
    by_type = {}
    for ev in recent:
        et = ev.get("event_type", "?")
        by_type[et] = by_type.get(et, 0) + 1
    for et, c in sorted(by_type.items()):
        print(f"    {et:35s} {c}")

    # Show newest quarantined candidates
    quarantined = gate.list_by_state("QUARANTINED")
    if quarantined:
        print(f"\n  Newest quarantined (max 10):")
        for c in quarantined[-10:]:
            print(f"    {c['id']}  {c.get('intent', '?'):15s}  {c.get('summary', '')[:50]}")

    print(f"\n{'='*55}\n")


def _handle_approve(candidate_id: str) -> None:
    from core.learn.ascension_gate import get_gate

    gate = get_gate()
    cand = gate.get(candidate_id)
    if cand is None:
        print(f"Candidate '{candidate_id}' not found")
        sys.exit(1)

    if gate.approve(candidate_id):
        print(f"Approved candidate {candidate_id}")
        print(f"  Intent:  {cand.get('intent', '?')}")
        print(f"  Summary: {cand.get('summary', '')[:80]}")
    else:
        print(f"Failed to approve candidate '{candidate_id}'")
        sys.exit(1)


def _handle_ban(candidate_id: str) -> None:
    from core.learn.ascension_gate import get_gate

    gate = get_gate()
    cand = gate.get(candidate_id)
    if cand is None:
        print(f"Candidate '{candidate_id}' not found")
        sys.exit(1)

    if gate.ban(candidate_id):
        print(f"Banned candidate {candidate_id}")
    else:
        print(f"Failed to ban candidate '{candidate_id}'")
        sys.exit(1)


def _handle_stats() -> None:
    """Show learning subsystem statistics."""
    from core.learn.episodic import get_ledger
    from core.learn.ascension_gate import get_gate
    from core.learn.watchdog import watcher_status

    ledger = get_ledger()
    gate = get_gate()
    wd = watcher_status()

    print(f"\n  VECTRAX LEARN STATS")
    print(f"  {'─'*40}")

    # Watchdog
    wd_state = "RUNNING" if wd["running"] else "STOPPED"
    print(f"  Watchdog:     {wd_state}", end="")
    if wd["running"]:
        print(f" (PID {wd['pid']})")
    else:
        print()

    # Episodic
    total_events = ledger.count()
    events_24h = len(ledger.since(hours=24))
    print(f"  Episodic events (total): {total_events}")
    print(f"  Episodic events (24h):   {events_24h}")

    # Candidates
    counts = gate.count_by_state()
    print(f"  Candidates:")
    for state, count in counts.items():
        print(f"    {state:15s} {count}")

    # Semantic index (best-effort)
    try:
        from core.learn.semantic import get_semantic_index
        si = get_semantic_index()
        st = si.stats()
        print(f"  Semantic vectors: {st['total_vectors']}")
        print(f"  Semantic model:   {st['model']}")
    except ImportError:
        print(f"  Semantic index: not available (install sentence-transformers + faiss-cpu)")

    print()


def _handle_gravity(args: list[str]) -> None:
    """Handle ``vx learn gravity {status,rebalance,explain}``."""
    sub = args[0] if args else "status"

    if sub == "status":
        _gravity_status()
    elif sub == "rebalance":
        _gravity_rebalance()
    elif sub == "explain":
        if len(args) < 2:
            print("Usage: vx learn gravity explain <fingerprint>")
            sys.exit(1)
        _gravity_explain(args[1])
    else:
        print("Usage: vx learn gravity {status|rebalance|explain <fp>}")
        sys.exit(1)


def _gravity_status() -> None:
    from core.learn.gravity_engine import get_gravity_index
    from core.learn.constellation import list_constellations

    idx = get_gravity_index()
    counts = idx.tier_counts()
    stats = idx.tier_stats()
    total = sum(counts.values())

    print(f"\n{'='*55}")
    print(f"  GRAVITY MEMORY STATUS")
    print(f"{'='*55}")
    print(f"\n  Total records: {total}\n")
    for tier_name in ["HOT", "WARM", "COLD", "DEEP"]:
        c = counts.get(tier_name, 0)
        s = stats.get(tier_name, {})
        avg_cc = s.get("avg_cc", 0.0)
        avg_freq = s.get("avg_freq", 0.0)
        bar = "█" * min(c, 40)
        print(f"  {tier_name:5s}  {c:5d}  cc={avg_cc:.2f}  freq={avg_freq:.2f}  {bar}")

    constellations = list_constellations()
    if constellations:
        print(f"\n  Constellations: {len(constellations)}")
        for c in constellations[:5]:
            print(f"    {c.constellation_id}  {c.domain}/{c.intent}  "
                  f"events={c.total_events} freq={c.frequency}")

    print(f"\n{'='*55}\n")


def _gravity_rebalance() -> None:
    from core.learn.gravity_engine import get_gravity_index
    from core.learn.gravity_decay import apply_decay
    from core.learn.constellation import compact

    idx = get_gravity_index()

    print("Running gravity rebalance...")
    demoted = apply_decay(idx)
    if demoted:
        print(f"  Demoted {len(demoted)} records:")
        for fp, new_tier in list(demoted.items())[:10]:
            print(f"    {fp[:16]}… → {new_tier}")
    else:
        print("  No records needed demotion.")

    created = compact(idx)
    if created:
        print(f"  Created {len(created)} constellations:")
        for c in created:
            print(f"    {c.constellation_id}  {c.domain}/{c.intent}  events={c.total_events}")
    else:
        print("  No new constellations.")

    print("Rebalance complete.")


def _gravity_explain(fingerprint: str) -> None:
    from core.learn.gravity_engine import get_gravity_index
    from core.learn.resonance import compute_resonance, should_speak, SILENCE_THRESHOLD
    from core.learn.constellation import load_constellation

    idx = get_gravity_index()
    rec = idx.get(fingerprint)
    if rec is None:
        print(f"No gravity record for fingerprint '{fingerprint}'")
        sys.exit(1)

    res = compute_resonance(rec)
    speaks = should_speak(rec)

    print(f"\n  Gravity record: {rec.fingerprint}")
    print(f"  {'─'*45}")
    print(f"  Tier:       {rec.tier}")
    print(f"  Hits:       {rec.hits}")
    print(f"  Frequency:  {rec.freq:.4f} events/day")
    print(f"  CC score:   {rec.cc_score:.4f}")
    print(f"  Impact:     {rec.impact}")
    print(f"  Domain:     {rec.domain}")
    print(f"  Decay ×:    {rec.decay_factor}")
    print(f"  First seen: {rec.first_seen}")
    print(f"  Last seen:  {rec.last_seen}")
    print(f"  Resonance:  {res:.4f}  (threshold={SILENCE_THRESHOLD})")
    print(f"  Should speak: {'YES' if speaks else 'NO (silence)'}")
    if rec.constellation_id:
        c = load_constellation(rec.constellation_id)
        label = f"{c.domain}/{c.intent} ({c.total_events} events)" if c else rec.constellation_id
        print(f"  Constellation: {label}")
    print()


def _print_learn_help() -> None:
    print("""
vx learn — Hybrid Memory & Silent Watchdog

COMMANDS:
    vx learn watch             Start watcher daemon
    vx learn watch --fg        Start watcher in foreground
    vx learn watch --no-semantic  Skip semantic indexing
    vx learn stop              Stop watcher daemon
    vx learn scan              Full repository scan
    vx learn scan --no-semantic   Skip semantic indexing
    vx learn report            Show candidates + clusters
    vx learn approve <id>      Approve a quarantined candidate
    vx learn ban <id>          Ban a candidate
    vx learn stats             Show learning statistics
    vx learn gravity status    Gravity memory tier overview
    vx learn gravity rebalance Run decay + constellation compaction
    vx learn gravity explain <fp>  Explain a fingerprint's gravity state
""")


# ===================================================================
# Status extension for vx status
# ===================================================================

def learn_status_summary() -> str:
    """Return a brief status string for embedding in ``vx status``."""
    lines = []
    try:
        from core.learn.watchdog import watcher_status
        wd = watcher_status()
        state = "RUNNING" if wd["running"] else "STOPPED"
        lines.append(f"  Watchdog: {state}")
    except Exception:
        lines.append("  Watchdog: unknown")

    try:
        from core.learn.episodic import get_ledger
        events_24h = len(get_ledger().since(hours=24))
        lines.append(f"  Observed changes (24h): {events_24h}")
    except Exception:
        pass

    try:
        from core.learn.ascension_gate import get_gate
        counts = get_gate().count_by_state()
        parts = [f"{s}={c}" for s, c in counts.items() if c > 0]
        lines.append(f"  Candidates: {', '.join(parts) if parts else 'none'}")
    except Exception:
        pass

    return "\n".join(lines)
