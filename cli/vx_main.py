#!/usr/bin/env python3
"""
vx - Vectrax CLI Tool
Main entry point for all CLI commands
"""
import os
import signal
import sys
import asyncio
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.abstraction import GenerateRequest
from core.providers import OllamaProvider
from core.proposal_engine import ProposalEngine


async def handle_generate(prompt: str, model: str = "llama3.2:3b"):
    """Generate response using local LLM"""
    provider = OllamaProvider()
    
    # Check health first
    if not await provider.health_check():
        print("❌ Error: Ollama is not running")
        print("Start it with: brew services start ollama")
        return
    
    request = GenerateRequest(
        prompt=prompt,
        model=model,
        temperature=0.7,
    )
    
    # Streaming response
    async for chunk in provider.stream(request):
        print(chunk, end="", flush=True)
    print()  # newline at end
    
    await provider.close()


async def handle_list_models():
    """List available models"""
    provider = OllamaProvider()
    models = await provider.list_models()
    
    print("📦 Available models:")
    for model in models:
        print(f"  • {model}")
    
    await provider.close()


async def handle_status():
    """Check system status"""
    provider = OllamaProvider()
    healthy = await provider.health_check()
    
    if healthy:
        print("✅ Vectrax is running")
        print(f"   Provider: Ollama")
        print(f"   Endpoint: http://localhost:11434")
    else:
        print("❌ Vectrax is not running")
        print("   Start Ollama with: brew services start ollama")
    
    await provider.close()


def print_help():
    """Print help message"""
    print("""
🚀 vx - Vectrax CLI

USAGE:
    vx <command> [arguments]

COMMANDS:
    activate              Activate the cognitive operator runtime
    run                   Activate operator & run continuous cycles
                              --interval <N>  Seconds between cycles (default: 5)
    watch                 Observe operator state (read-only)
                              --live          Continuous refresh every 3s
                              --cycle         Run one observation cycle
                              --json          JSON output
    <prompt>              Generate response from AI
    propose <description> Propose system changes (shows diff, risk, waits for approval)
    propose --remote <d>  Propose via Core Central Service
    learn watch           Start silent watchdog daemon
    learn stop            Stop watchdog
    learn scan            Full repository scan
    learn report          Show learned candidates + clusters
    learn approve <id>    Approve a quarantined candidate
    learn ban <id>        Ban a candidate
    learn stats           Show learning statistics
    learn cycle            Run one learning pipeline cycle
    learn cycle feed       Feed an event into the learning pipeline
    learn cycle-status     Show learning cycle pipeline status
    agent start           Start the local agent daemon
    agent status          Check agent status and Core connectivity
    route status          Show intelligence router health and model registry
    route test            Run routing test suite
    router-report         Show router self-learning report:
                              intents, quality, fallbacks, ambiguity, suggestions
    router-learn activate Activate continuous router learning cycle
    router-learn deactivate  Deactivate learning cycle
    router-learn cycle    Run one learning cycle (observe + propose)
    router-learn status   Show learning cycle status + pending proposals
    mode status           Show current operational mode
    mode set <MODE>       Set base mode (HOME_AUTO|BUSINESS_GOVERNED|MISSION_STRICT)
    policy status         Show active policy version
    policy history        Show policy version history
    policy propose        Create candidate policy
    policy promote <id>   Promote candidate to active
    policy rollback <id>  Rollback active policy
    sandbox run           Run policy sandbox simulation
    inject-mass <id> --mass <v>  Inject gravitational mass into a star
                                       Triggers natural evolution: distance,
                                       layer transition, bus propagation.
    up                    Arranca TODOS los motores de Vectrax (un solo comando)
    down                  Para todos los motores
    check                 Verifica que todo esté corriendo
    start                 Start Vectrax services
    status                Check system status
    models                List available models
    help                  Show this help message

EXAMPLES:
    vx activate
    vx run
    vx run --interval 10
    vx watch
    vx watch --live
    vx watch --cycle
    vx "Explain quantum computing"
    vx propose "Add logging to SmartRouter class"
    vx inject-mass abc123 --mass 0.85
    vx inject-mass abc123de --mass 1.0
    vx mode status
    vx mode set BUSINESS_GOVERNED
    vx policy status
    vx sandbox run --policy abc123 --ops 20
    vx route status
    vx route test
    vx router-report
    vx router-learn activate
    vx router-learn cycle
    vx router-learn status
    vx status
    vx models

OPTIONS:
    --model <model>       Specify model to use (default: llama3.2:3b for prompts,
                          qwen2.5-coder:7b for proposals)
    --remote              Use Core Central Service for proposals

For more information, visit: https://github.com/vectrax/vectrax
""")


async def handle_start():
    """Start Ollama service"""
    import subprocess
    
    print("🚀 Starting Vectrax services...")
    try:
        result = subprocess.run(
            ["brew", "services", "start", "ollama"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("✅ Ollama started successfully")
            # Wait a moment for service to start
            await asyncio.sleep(2)
            await handle_status()
        else:
            print(f"❌ Failed to start Ollama: {result.stderr}")
    except FileNotFoundError:
        print("❌ Homebrew not found. Install Ollama manually:")
        print("   Visit: https://ollama.ai/download")


async def handle_propose_remote(description: str, model: str = "qwen2.5-coder:7b"):
    """Generate a proposal via Core Central Service."""
    print(f"\n🌐 Requesting remote proposal: {description}\n")

    from agent.client import AgentClient
    from agent.config import load_agent_config

    cfg = load_agent_config()
    client = AgentClient(cfg)

    try:
        reachable = await client.health_check()
        if not reachable:
            print("❌ Core Central Service is not reachable.")
            print(f"   URL: {cfg.core_url}")
            print("   Start it with: make run-core")
            return

        print("⏳ Generating proposal via Core...\n")
        result = await client.fetch_proposal(description, model=model)

        print(f"📋 Proposal: {result.get('description', description)}")
        print(f"   Files affected: {result.get('files_affected', 0)}")
        print(f"   Risk score: {result.get('risk_score', 'N/A')}")
        print(f"   Risk level: {result.get('risk_level', 'N/A')}")
        print(f"   Allowed: {'✅' if result.get('allowed') else '❌'}")
        print(f"   Requires confirmation: {result.get('requires_confirmation', True)}")

        if result.get('diff'):
            print(f"\n{'='*70}")
            print(result['diff'])
            print(f"{'='*70}")

        if result.get('rationale'):
            print(f"\n   Rationale: {result['rationale']}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        await client.close()


async def handle_propose(description: str, model: str = "qwen2.5-coder:7b"):
    """Generate a proposal for system changes without applying them."""
    print(f"\n🔍 Analyzing proposal: {description}\n")
    
    engine = ProposalEngine()
    
    try:
        # Generate proposal
        print("⏳ Generating proposal (this may take a moment)...\n")
        proposal = await engine.propose(description, model=model)
        
        # Display summary
        print(proposal.summary())
        print("\n" + "="*70)
        
        # Show risk breakdown if available
        if proposal.risk_breakdown:
            print("\n📊 Risk Breakdown:")
            for signal in proposal.risk_breakdown.get("signals", []):
                print(f"  • {signal['name']}: {signal['value']:.3f} "
                      f"(weight: {signal['weight']:.3f}, "
                      f"contribution: {signal['contribution']:.3f})")
                if signal.get('details'):
                    print(f"    └─ {signal['details']}")
        
        # Show autonomy zone classification
        if proposal.autonomy_classification:
            ac = proposal.autonomy_classification
            print("\n🔒 Autonomy Zone Classification:")
            for pf in ac.get("per_file", []):
                zone_icon = {"SACRED_CORE": "🔴", "SEMI_SAFE": "🟡", "FLEXIBLE": "🟢"}.get(pf["zone"], "⚪")
                auto_icon = "✅" if pf["auto_apply"] else "❌"
                print(f"  {zone_icon} {pf['path']}: {pf['zone_label']} — auto-apply: {auto_icon}")
            print(f"\n  📋 Decisión global: {'✅ Auto-apply permitido' if proposal.auto_apply_allowed else '❌ Requiere confirmación humana'}")
            print(f"  📝 Razón: {proposal.auto_apply_reason}")

        # Display diffs
        print("\n" + "="*70)
        print("\n📝 Proposed Changes:")
        print(proposal.full_diff())
        print("\n" + "="*70)
        
        # Always require human confirmation (auto-apply off by default)
        if not proposal.allowed:
            print(f"\n⚠️  This proposal is NOT allowed:")
            print(f"   {proposal.rationale}")
            print(f"\n💡 The system will not apply these changes automatically.")
        else:
            print(f"\n✅ This proposal is allowed by the Governor.")
            print(f"\n⚠️  IMPORTANT: These changes have NOT been applied yet.")
            print(f"   Review the changes carefully before proceeding.\n")
            
            # Ask for confirmation (always, even if auto-apply is theoretically allowed)
            try:
                response = input("Apply these changes? (yes/no): ").strip().lower()
                
                if response in ('yes', 'y'):
                    print("\n⏳ Applying changes...")
                    result = await engine.apply(proposal)
                    
                    if result["success"]:
                        print("\n✅ Changes applied successfully!")
                        print(f"   Applied: {', '.join(result['applied'])}")
                    else:
                        print("\n❌ Some changes failed:")
                        for failed in result.get("failed", []):
                            print(f"   • {failed['path']}: {failed['error']}")
                else:
                    print("\n❌ Changes NOT applied. Proposal discarded.")
            except (EOFError, KeyboardInterrupt):
                print("\n\n❌ Cancelled by user. Changes NOT applied.")
    
    except Exception as e:
        print(f"\n❌ Error generating proposal: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await engine.close()

# ===================================================================
# inject-mass command — controlled gravitational mass injection
# ===================================================================

def handle_inject_mass(node_id_prefix: str, mass_value: float) -> None:
    """
    Inject gravitational mass into a star by prefix or full ID.

    Flow:
      1. Resolve star (exact ID or unique prefix)
      2. Guard: reject creator-channel stars (immutable nucleus)
      3. Clamp mass to [MIN_MASS, MAX_MASS]
      4. Capture before state
      5. Set mass → recompute distance_to_core, gravity_score, layer
      6. Persist to DB
      7. Wire BusReactor + emit UNIVERSE_MASS_INJECTED to live.universe
         (BusReactor._on_universe → OPERATOR_MEMORY_UPDATED → operator.out)
      8. Display before/after table + bus propagation stats
    """
    from vectrax import db as _db
    from vectrax.gravity import (
        assign_layer,
        compute_distance_from_mass,
        compute_star_gravity,
    )
    from vectrax.models import MAX_MASS, MIN_MASS
    from vectrax.identity import CHANNEL_CREATOR
    from core.operator.bus_reactor import EventTypes, LiveChannels, get_bus_reactor
    from core.operator.universal_bus import get_universal_bus

    _db.init_db()

    # ── 1. Resolve star ───────────────────────────────────────────────
    star = _db.get_star(node_id_prefix)  # exact match first
    if star is None:
        all_stars = _db.get_all_stars()
        matches = [s for s in all_stars if s.id.startswith(node_id_prefix)]
        if not matches:
            print(f"❌ No star found with id (prefix): {node_id_prefix}")
            sys.exit(1)
        if len(matches) > 1:
            print(
                f"❌ Ambiguous prefix '{node_id_prefix}' — "
                f"{len(matches)} stars match. Provide more characters:"
            )
            for m in matches[:6]:
                print(f"   {m.id[:16]}  layer={m.layer}  {m.content[:55]}")
            sys.exit(1)
        star = matches[0]

    # ── 2. Guard: creator nucleus immutable ───────────────────────────
    if star.channel == CHANNEL_CREATOR:
        print(
            f"❌ Star {star.id[:8]} belongs to the creator nucleus (CHANNEL_CREATOR). "
            "Cannot inject mass into the immutable core."
        )
        sys.exit(1)

    # ── 3. Clamp mass ─────────────────────────────────────────────────
    new_mass = max(MIN_MASS, min(MAX_MASS, mass_value))
    if round(new_mass, 8) != round(mass_value, 8):
        print(
            f"⚠️  Mass clamped to [{MIN_MASS}, {MAX_MASS}]: "
            f"{mass_value} → {new_mass}"
        )

    # ── 4. Capture before state ───────────────────────────────────────
    before = {
        "mass": star.mass,
        "gravity_score": star.gravity_score,
        "layer": star.layer,
        "distance_to_core": star.distance_to_core,
    }
    old_layer = star.layer

    # ── 5. Inject mass + recompute derived fields ─────────────────────
    #
    # NOTE: We do NOT call cognitive_gravity.update_mass() here because
    # it recomputes mass from graph connections and would override the
    # injected value. Instead we set mass directly and let the universe
    # visual (distance_to_core) reflect the physical movement.
    #
    # gravity_score and layer are recomputed from current star stats
    # (repetition_count + success_rate) — these drive the "natural"
    # layer assignment without forcing it.
    star.mass = new_mass
    star.distance_to_core = compute_distance_from_mass(new_mass)
    star.gravity_score = compute_star_gravity(star)
    star.layer = assign_layer(star.gravity_score)

    # ── 6. Persist ────────────────────────────────────────────────────
    _db.update_star(star)

    # ── 7. Wire reactor + emit events ────────────────────────────────
    # get_bus_reactor() auto-wires BusReactor + UniverseEvolutionEngine.
    # Emit UNIVERSE_MASS_INJECTED to live.universe:
    #   BusReactor._on_universe → OPERATOR_MEMORY_UPDATED → operator.out
    #   UniverseEvolutionEngine._on_operator_out → _maybe_run_decay
    reactor = get_bus_reactor()
    bus = get_universal_bus()

    bus.emit(
        channel=LiveChannels.UNIVERSE,
        event_type=EventTypes.UNIVERSE_MASS_INJECTED,
        source_layer=4,
        payload={
            "star_id": star.id,
            "star_content": star.content[:80],
            "channel": star.channel,
            "owner": star.owner,
            "before": before,
            "after": {
                "mass": star.mass,
                "gravity_score": star.gravity_score,
                "layer": star.layer,
                "distance_to_core": star.distance_to_core,
            },
            "layer_transitioned": old_layer != star.layer,
            "stars_updated": 1,
            "constellations_updated": 0,
        },
    )

    # ── 8. Display results ────────────────────────────────────────────
    layer_icon = {"core": "★", "mid": "☆", "outer": "·"}.get(star.layer, "?")
    before_icon = {"core": "★", "mid": "☆", "outer": "·"}.get(before["layer"], "?")

    print(f"\n⚡ Gravitational Mass Injection")
    print(f"   Star ID: {star.id[:16]}{'...' if len(star.id) > 16 else ''}")
    print(f"   Content: {star.content[:70]}")
    print(f"   Channel: {star.channel}   Owner: {star.owner or '—'}")

    SEP = "─" * 64
    print(f"\n{SEP}")
    print(f"  {'Field':<24} {'Before':>13}  {'After':>13}  {'Δ':>9}")
    print(SEP)

    d_mass = star.mass - before["mass"]
    d_grav = star.gravity_score - before["gravity_score"]
    d_dist = star.distance_to_core - before["distance_to_core"]

    print(
        f"  {'mass':<24} {before['mass']:>13.6f}  {star.mass:>13.6f}  "
        f"{d_mass:>+9.6f}"
    )
    print(
        f"  {'gravity_score':<24} {before['gravity_score']:>13.4f}  "
        f"{star.gravity_score:>13.4f}  {d_grav:>+9.4f}"
    )
    print(
        f"  {'layer':<24} {before_icon + ' ' + before['layer']:>13}  "
        f"{layer_icon + ' ' + star.layer:>13}"
    )
    print(
        f"  {'distance_to_core':<24} {before['distance_to_core']:>13.6f}  "
        f"{star.distance_to_core:>13.6f}  {d_dist:>+9.6f}"
    )
    print(SEP)

    if old_layer != star.layer:
        print(
            f"\n  🌟 LAYER TRANSITION: "
            f"{before_icon} {old_layer} → {layer_icon} {star.layer}  "
            "(natural consequence of gravity recomputation)"
        )
    else:
        print(
            f"\n  Layer unchanged: {layer_icon} {star.layer}  "
            f"(gravity_score={star.gravity_score:.4f} — "
            f"core≥0.6, mid≥0.3, outer<0.3)"
        )

    # Bus propagation stats
    try:
        stats = reactor.get_stats()
        bus_stats = stats.get("bus_stats", {})
        by_channel = bus_stats.get("by_channel", {})
        evo = stats.get("evolution_stats", {})

        univ_pub = by_channel.get(LiveChannels.UNIVERSE, {}).get("published", 0)
        out_pub  = by_channel.get(LiveChannels.OPERATOR_OUT, {}).get("published", 0)

        print(f"\n  📡 Bus propagation:")
        print(f"     live.universe published : {univ_pub}")
        print(f"     operator.out published  : {out_pub}")
        if evo:
            print(
                f"     evolution wired        : "
                f"{'✓ yes' if evo.get('wired') else '✗ no'} "
                f"({evo.get('subscriptions', 0)} subscriptions)"
            )
            print(f"     mass_updates total     : {evo.get('mass_updates', 0)}")
            print(f"     layer_transitions total: {evo.get('layer_transitions', 0)}")
    except Exception:
        pass

    print(f"\n  ✅ Persisted. Observable via:")
    print(f"     vx watch             — operator state + bus events")
    print(f"     vx watch --live      — continuous evolution monitor")
    print(f"     GET /v1/gravitational/universe  — visual universe API")
    print()


# ===================================================================
# Activate command — sovereign operator activation
# ===================================================================

def handle_activate():
    """
    Activate the cognitive operator runtime.
    Single path: core.operator.activation.activate_operator()
    """
    from core.operator.activation import activate_operator, get_runtime
    from core.operator.identity import OperatorMode

    runtime = get_runtime()

    if runtime.is_active:
        print(
            f"\n\u2705 Operator already ACTIVE | mode={runtime.mode.value} | "
            f"cycles={runtime.cycle_count}"
        )
        return

    print("\n\U0001f680 Activating Vectrax Cognitive Operator...")
    try:
        result = activate_operator(mode=OperatorMode.GUIDED)
        print(f"\u2705 Operator ACTIVE")
        print(f"   Identity : {result['identity']}")
        print(f"   Creator  : {result['creator']}")
        print(f"   Mode     : {result['mode']}")
        print(f"   Layers   : {result['layers_active']} active")
        print(f"   Boot     : {result['boot_time_ms']:.0f}ms")
        print(f"   Domains  : {len(result['authorized_domains'])} authorized, "
              f"{len(result['restricted_domains'])} restricted")
        print()
        print("   Activation recorded in ledger.")
        print("   Run 'vx watch' to observe state.")
        print("   Run 'vx run' to start continuous cycles.")
    except Exception as exc:
        print(f"\u274c Activation failed: {exc}")
        sys.exit(1)


# ===================================================================
# Watch command — sovereign read-only observation
# ===================================================================

def handle_watch(args):
    """
    Observe operator state (read-only).
    Delegates entirely to scripts.watch_operator.main(args).
    State source: core.operator.activation.get_operator_status()
    """
    from scripts.watch_operator import main as watch_main
    watch_main(args)


# ===================================================================
# Run command — continuous cognitive operator cycle
# ===================================================================

def handle_run(interval: float = 5.0):
    """
    Activate the operator and run continuous cognitive cycles.
    Delegates to core.operator.activation.run_operator_forever().
    """
    from core.operator.activation import activate_operator, get_runtime, run_operator_forever
    from core.operator.identity import OperatorMode

    runtime = get_runtime()

    # Activate if not already active
    if not runtime.is_active:
        print("\n\U0001f680 Activating Vectrax Operator...")
        try:
            result = activate_operator(mode=OperatorMode.GUIDED)
            print(f"\u2705 Operator active | mode={result['mode']} | "
                  f"layers={result['layers_active']} | "
                  f"boot={result['boot_time_ms']:.0f}ms")
        except Exception as exc:
            print(f"\u274c Activation failed: {exc}")
            sys.exit(1)
    else:
        print(f"\n\u2705 Operator already active | mode={runtime.mode.value} | "
              f"cycles={runtime.cycle_count}")

    print(f"\n\U0001f504 Starting continuous run loop (interval={interval}s)")
    print("   Press Ctrl+C to stop\n")

    def _print_cycle(cycle):
        cd = cycle.to_dict()
        ts = time.strftime("%H:%M:%S")
        print(
            f"  [{ts}] cycle={cd['cycle_id']} #{cd['cycle_number']} "
            f"| {cd['outcome']} "
            f"| signals={cd['perception']['signals']} "
            f"| high={cd['relevance']['high']} "
            f"| hyp={cd['hypotheses']['proposed']} "
            f"| proposals={cd['proposals_count']} "
            f"| {cd['elapsed_ms']:.0f}ms"
        )

    # run_operator_forever handles SIGINT/SIGTERM and ledger shutdown
    run_operator_forever(interval=interval, on_cycle=_print_cycle)

    print(f"\n\u23f9  Operator stopped after {runtime.cycle_count} cycles.")


# ===================================================================
# Mode commands
# ===================================================================

VALID_MODES = {"HOME_AUTO", "BUSINESS_GOVERNED", "MISSION_STRICT"}


def handle_mode_status():
    """Show current operational mode."""
    from core import state_manager
    base_mode = state_manager.get("operational_mode", "HOME_AUTO")

    print(f"\n🎚️  Operational Mode")
    print(f"   Base mode: {base_mode}")
    print(f"   (Effective mode is derived per-request from context + risk)")

    # Show last effective mode from recent routing decision
    try:
        from core.audit_ledger import query
        recent = query(limit=1, action_filter="model_route")
        if recent:
            import json
            meta = recent[0].get("metadata", "{}")
            meta = json.loads(meta) if isinstance(meta, str) else meta
            eff = meta.get("autonomy_mode", "unknown")
            print(f"   Last effective: {eff}")
    except Exception:
        pass
    print()


def handle_mode_set(mode_str: str):
    """Set base operational mode."""
    mode_upper = mode_str.upper()
    if mode_upper not in VALID_MODES:
        print(f"❌ Invalid mode: {mode_str}")
        print(f"   Valid modes: {', '.join(sorted(VALID_MODES))}")
        sys.exit(1)
    from core import state_manager
    state_manager.put("operational_mode", mode_upper)
    try:
        from core.audit_ledger import record
        record(
            action="mode_change",
            actor="cli",
            decision="approved",
            reason=f"Set base mode to {mode_upper}",
            metadata={"mode": mode_upper},
        )
    except Exception:
        pass
    print(f"✅ Base mode set to {mode_upper}")


# ===================================================================
# Policy commands
# ===================================================================

def handle_policy_status():
    """Show active policy version."""
    from core.policy_registry import get_policy_registry
    reg = get_policy_registry()
    active = reg.get_active()

    print("\n📜 Policy Registry")
    if active:
        print(f"   Active: v{active.version} (id={active.id})")
        print(f"   Status: {active.status}")
        print(f"   Promoted: {active.promoted_at or 'N/A'}")
        print(f"   Params:")
        for k, v in active.params.items():
            print(f"     {k}: {v}")
    else:
        print("   No active policy — using defaults")
    print()


def handle_policy_history():
    """Show policy version history."""
    from core.policy_registry import get_policy_registry
    reg = get_policy_registry()
    versions = reg.history(limit=20)

    print("\n📜 Policy History")
    if not versions:
        print("   No policy versions found")
    else:
        for pv in versions:
            status_icon = {"active": "🟢", "candidate": "🟡",
                           "retired": "⚪", "rolled_back": "🔴"}.get(pv.status, "⚪")
            print(f"  {status_icon} v{pv.version} [{pv.id}] {pv.status} — {pv.created_at}")
    print()


def handle_policy_propose():
    """Create a candidate policy."""
    from core.policy_registry import get_policy_registry
    reg = get_policy_registry()
    pv = reg.create_version()
    print(f"✅ Created candidate policy v{pv.version} (id={pv.id})")
    print(f"   Use 'vx sandbox run --policy {pv.id}' to test")
    print(f"   Use 'vx policy promote {pv.id}' to activate")


def handle_policy_promote(policy_id: str):
    """Promote a candidate policy."""
    from core.policy_registry import get_policy_registry
    reg = get_policy_registry()
    try:
        pv = reg.promote(policy_id)
        print(f"✅ Policy v{pv.version} ({pv.id}) promoted to active")
    except (ValueError, PermissionError) as e:
        print(f"❌ {e}")
        sys.exit(1)


def handle_policy_rollback(policy_id: str):
    """Rollback an active policy."""
    from core.policy_registry import get_policy_registry
    reg = get_policy_registry()
    try:
        restored = reg.rollback(policy_id)
        if restored:
            print(f"✅ Rolled back. Active is now v{restored.version} ({restored.id})")
        else:
            print("✅ Rolled back. No previous version to restore.")
    except (ValueError, PermissionError) as e:
        print(f"❌ {e}")
        sys.exit(1)


# ===================================================================
# Sandbox commands
# ===================================================================

def handle_sandbox_run(policy_id: str, num_ops: int = 10):
    """Run sandbox simulation for a candidate policy."""
    from core.policy_registry import get_policy_registry
    from core.sandbox_runner import SandboxRunner

    reg = get_policy_registry()
    pv = reg.get_version(policy_id)
    if pv is None:
        print(f"❌ Policy {policy_id} not found")
        sys.exit(1)

    runner = SandboxRunner(registry=reg)
    print(f"\n🧪 Running sandbox for policy v{pv.version} ({pv.id}) with {num_ops} ops...\n")

    # Generate ops list (use defaults, repeat/trim to num_ops)
    from core.sandbox_runner import DEFAULT_OPS
    ops = (DEFAULT_OPS * ((num_ops // len(DEFAULT_OPS)) + 1))[:num_ops]

    report = runner.run(policy_id, ops)
    print(f"   Total ops:           {report.total_ops}")
    print(f"   Escalation rate:     {report.escalation_rate:.1%}")
    print(f"   MISSION_STRICT rate: {report.mission_strict_rate:.1%}")
    print(f"   Avg severity:        {report.avg_severity:.3f}")
    print(f"   Avg latency:         {report.avg_latency_ms:.2f}ms")
    print(f"   Stability score:     {report.stability_score:.3f}")
    print(f"   Invariant violations:{report.invariant_violations}")

    can = runner.can_promote(report)
    if can:
        print(f"\n   ✅ Safe to promote")
    else:
        print(f"\n   ❌ NOT safe to promote (violations or low stability)")
    print()


# ===================================================================
# Route commands (existing)
# ===================================================================

# ===================================================================
# Router Report command — self-learning analytics
# ===================================================================

def handle_router_report():
    """
    Show router self-learning report with analytics and improvement
    proposals based on real usage data.
    """
    from core.router_learning import get_learning_engine

    engine = get_learning_engine()
    report = engine.generate_report()

    if report.get("status") == "insufficient_data":
        print(f"\n📊 Router Self-Learning Report")
        print(f"   Estado: datos insuficientes")
        print(f"   Registros: {report.get('total_records', 0)} / {report.get('min_required', 10)} mínimo")
        print(f"   {report.get('message', '')}")
        print(f"\n   El sistema registra cada decisión automáticamente.")
        print(f"   Usa el router normalmente y el reporte se poblará.")
        print()
        return

    summary = report.get("summary", {})
    print(f"\n📊 Router Self-Learning Report")
    print(f"{'═' * 64}")

    # --- Summary ---
    period = summary.get("period", {})
    print(f"\n📋 Resumen")
    print(f"   Total decisiones: {summary.get('total_decisions', 0)}")
    print(f"   Tasa de éxito:    {summary.get('success_rate', 0):.1%}")
    if period.get("start"):
        print(f"   Periodo:          {period['start']} → {period.get('end', '?')}")
        print(f"   Duración:         {period.get('duration_hours', 0):.1f}h")

    # --- Intent Distribution ---
    intents = report.get("top_intents", {})
    if intents:
        print(f"\n🎯 Intents más frecuentes")
        for intent, stats in intents.items():
            bar = "█" * min(20, int(stats['total'] / max(1, summary.get('total_decisions', 1)) * 40))
            status_icon = "✅" if stats['success_rate'] >= 0.8 else ("⚠️" if stats['success_rate'] >= 0.5 else "❌")
            print(
                f"   {status_icon} {intent:<18} "
                f"n={stats['total']:<5} "
                f"ok={stats['success_rate']:.0%} "
                f"fail={stats['failure_rate']:.0%} "
                f"fb={stats['fallback_rate']:.0%} "
                f"{bar}"
            )

    # --- Quality Scores ---
    quality = report.get("quality_scores", {})
    if quality:
        print(f"\n📈 Calidad de rutas")
        icons = {
            "correct_route": "🟢",
            "weak_route": "🟡",
            "fallback_better": "🟠",
            "failed_route": "🔴",
            "ambiguous_route": "⚪",
        }
        for score, count in quality.items():
            icon = icons.get(score, "·")
            print(f"   {icon} {score:<20} {count}")

    # --- Strategy Stats ---
    strategies = report.get("strategy_stats", {})
    if strategies:
        print(f"\n🔀 Estrategias")
        for strat, stats in strategies.items():
            print(
                f"   {strat:<24} "
                f"n={stats['total']:<5} "
                f"ok={stats['success_rate']:.0%} "
                f"lat={stats['avg_latency_ms']:.0f}ms"
            )

    # --- Fallback Analysis ---
    fb = report.get("fallback_analysis", {})
    if fb.get("total_fallbacks", 0) > 0:
        print(f"\n🔄 Fallbacks")
        print(f"   Total: {fb['total_fallbacks']} ({fb['fallback_rate']:.1%})")
        if fb.get("by_intent"):
            print(f"   Por intent: {', '.join(f'{k}({v})' for k, v in fb['by_intent'].items())}")

    # --- Ambiguity ---
    amb = report.get("ambiguity_patterns", {})
    if amb.get("total_ambiguous", 0) > 0:
        print(f"\n❓ Ambigüedad")
        print(f"   Total: {amb['total_ambiguous']} ({amb['ambiguity_rate']:.1%})")
        print(f"   Confianza promedio: {amb.get('avg_confidence', 0):.2f}")
        if amb.get("by_intent"):
            print(f"   Intents ambiguos: {', '.join(f'{k}({v})' for k, v in amb['by_intent'].items())}")
        if amb.get("by_classification_method"):
            print(f"   Por método: {', '.join(f'{k}({v})' for k, v in amb['by_classification_method'].items())}")

    # --- Confidence ---
    conf = report.get("confidence", {})
    if conf:
        print(f"\n📏 Confianza")
        print(f"   Promedio global:  {conf.get('avg_confidence', 0):.3f}")
        print(f"   Promedio éxitos:  {conf.get('avg_success_confidence', 0):.3f}")
        print(f"   Promedio fallos:  {conf.get('avg_failed_confidence', 0):.3f}")
        ranges = conf.get("confidence_ranges", {})
        if ranges:
            print(f"   Distribución:     ", end="")
            for rng, cnt in ranges.items():
                print(f"{rng}:{cnt} ", end="")
            print()

    # --- Latency ---
    lat = report.get("latency", {})
    if lat:
        print(f"\n⏱️  Latencia")
        print(f"   Routing avg:   {lat.get('avg_routing_ms', 0):.1f}ms  (p95: {lat.get('p95_routing_ms', 0):.1f}ms)")
        print(f"   Respuesta avg: {lat.get('avg_response_ms', 0):.1f}ms  (p95: {lat.get('p95_response_ms', 0):.1f}ms)")

    # --- Threshold Suggestions ---
    thresh = report.get("threshold_suggestions", {})
    if thresh.get("status") == "ready":
        print(f"\n🎚️  Sugerencia de threshold")
        print(f"   Actual:    {thresh.get('current_semantic_threshold', 0.4)}")
        print(f"   Sugerido:  {thresh.get('suggested_semantic_threshold', 0.4)}")
        if thresh.get("should_adjust"):
            print(f"   ⚡ Se recomienda ajustar (requiere proposal flow)")
        else:
            print(f"   ✅ Threshold actual es adecuado")

    # --- Improvement Proposals ---
    proposals = report.get("improvement_proposals", [])
    if proposals:
        print(f"\n💡 Propuestas de mejora ({len(proposals)})")
        for i, p in enumerate(proposals, 1):
            prio_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p.get("priority", ""), "⚪")
            print(f"\n   {i}. {prio_icon} [{p.get('type', '?')}] ({p.get('priority', '?')})")
            print(f"      {p.get('suggestion', '')}")
    else:
        print(f"\n💡 Sin propuestas de mejora pendientes")

    print(f"\n{'═' * 64}")
    print(f"   Modo: entrenamiento pasivo (observa, no modifica)")
    print(f"   Ledger: data/router_feedback.jsonl")
    print()


# ===================================================================
# Router Learn command — continuous learning cycle
# ===================================================================

def _handle_router_learn(args):
    """Handle vx router-learn subcommands."""
    from core.router_learning_cycle import get_learning_cycle

    sub = args[0] if args else "status"
    cycle = get_learning_cycle()

    if sub == "activate":
        result = cycle.activate()
        print(f"\n\U0001f9e0 Router Learning Cycle")
        print(f"   {result['previous_mode']} \u2192 {result['new_mode']}")
        print(f"   El sistema ahora observa cada decisi\u00f3n y genera propuestas.")
        print(f"   Ejecuta 'vx router-learn cycle' para un ciclo manual.")
        print()

    elif sub == "deactivate":
        result = cycle.deactivate()
        print(f"\n\U0001f9e0 Router Learning Cycle")
        print(f"   {result['previous_mode']} \u2192 {result['new_mode']}")
        print()

    elif sub == "cycle":
        if not cycle.is_active():
            print("\n\u26a0\ufe0f  Learning cycle is inactive. Activate first:")
            print("   vx router-learn activate")
            print()
            return
        result = cycle.run_cycle()
        if result.get("skipped"):
            print(f"\n\u26a0\ufe0f  Cycle skipped: {result.get('reason', '?')}")
            print()
            return

        print(f"\n\U0001f9e0 Router Learning Cycle #{result['cycle_id']}")
        print(f"   Records analyzed: {result['total_records_analyzed']}")
        print(f"   Conflicts detected: {result['conflicts_detected']} ({result['unique_conflict_patterns']} patterns)")

        wa = result.get("weight_analysis", {})
        print(f"\n   Layer weights:")
        print(f"     Semantic: {wa.get('semantic_count', 0)} ({wa.get('semantic_rate', 0):.0%})")
        print(f"     Regex:    {wa.get('regex_count', 0)} ({wa.get('regex_rate', 0):.0%})")
        print(f"     Aligned:  {wa.get('aligned_count', 0)}")

        mg = result.get("memory_gaps", {})
        if mg.get("total_local", 0) > 0:
            print(f"\n   Memory gaps:")
            print(f"     LOCAL total: {mg['total_local']}")
            print(f"     via semantic: {mg.get('semantic_local', 0)}")
            print(f"     via regex only: {mg.get('regex_local', 0)}")
            print(f"     coverage: {mg.get('semantic_coverage', 0):.0%}")

        proposals = result.get("proposals", [])
        if proposals:
            print(f"\n   Proposals generated: {len(proposals)}")
            for i, p in enumerate(proposals, 1):
                prio = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\U0001f7e2"}.get(p.get("priority"), "\u26aa")
                print(f"     {i}. {prio} [{p['proposal_type']}] {p['description'][:90]}")
                if p.get("suggested_action"):
                    print(f"        \u2192 {p['suggested_action']}")
        else:
            print(f"\n   \u2705 No proposals generated (system is healthy)")

        print(f"\n   Elapsed: {result['elapsed_ms']:.0f}ms")
        print()

    elif sub == "status":
        status = cycle.get_status()
        print(f"\n\U0001f9e0 Router Learning Cycle")
        print(f"   Mode:              {status['mode']}")
        print(f"   Activated at:      {status.get('activated_at', 'never')}")
        print(f"   Cycles completed:  {status['cycles_completed']}")
        print(f"   Feedback records:  {status['total_feedback_records']}")
        print(f"   Pending proposals: {status['pending_proposals']}")
        print(f"   Total proposals:   {status['total_proposals']}")

        pending = cycle.get_pending_proposals()
        if pending:
            print(f"\n   Pending proposals:")
            for i, p in enumerate(pending[-5:], 1):
                prio = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\U0001f7e2"}.get(p.get("priority"), "\u26aa")
                print(f"     {prio} [{p['proposal_type']}] {p['description'][:80]}")
        print()

    else:
        print(f"Unknown router-learn subcommand: {sub}")
        print("Usage: vx router-learn activate|deactivate|cycle|status")
        sys.exit(1)


def handle_route_status():
    """Show intelligence router health and model registry."""
    from core.routing.model_router import get_model_router

    router = get_model_router()
    status = router.get_status()

    print("\n🧠 Intelligence Router Status")
    print(f"   Governor mode: {status['governor_mode']}")
    print(f"   Total models:  {status['total_models']} "
          f"(local: {status['local_models']}, cloud: {status['cloud_models']})")
    if status['daily_budget_cap']:
        print(f"   Daily budget cap: {status['daily_budget_cap']} tokens")

    print("\n📦 Available Models:")
    for key, info in status['models'].items():
        loc = "🏠 local" if info['is_local'] else "☁️  cloud"
        caps = ", ".join(info['capabilities'])
        print(f"  • {key} [{loc}] priority={info['priority']}")
        print(f"    capabilities: {caps}")

    cb = status.get('circuit_breakers', {})
    if cb:
        print("\n🔌 Circuit Breakers:")
        for provider, stats in cb.items():
            print(f"  • {provider}: {stats.get('state', 'unknown')} "
                  f"(requests={stats.get('total_requests', 0)}, "
                  f"success_rate={stats.get('success_rate', 0):.1f}%)")
    else:
        print("\n🔌 Circuit Breakers: none active")

    # Show last 5 routing decisions from ledger
    try:
        from core.audit_ledger import query
        recent = query(limit=5, action_filter="model_route")
        if recent:
            print("\n📋 Recent Routing Decisions:")
            for entry in recent:
                print(f"  [{entry['timestamp']}] "
                      f"{entry.get('decision', '')} — {entry.get('reason', '')[:80]}")
    except Exception:
        pass

    print()


def handle_route_test():
    """Run routing test suite."""
    import subprocess

    test_path = str(Path(__file__).parent.parent / "tests" / "test_model_router.py")
    print(f"\n🧪 Running routing tests: {test_path}\n")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"],
        cwd=str(Path(__file__).parent.parent),
    )
    sys.exit(result.returncode)


def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2 or sys.argv[1] in ["help", "--help", "-h"]:
        print_help()
        sys.exit(0)
    
    command = sys.argv[1]
    
    # Handle commands
    if command == "mode":
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        if sub == "status":
            handle_mode_status()
        elif sub == "set":
            if len(sys.argv) < 4:
                print("Usage: vx mode set HOME_AUTO|BUSINESS_GOVERNED|MISSION_STRICT")
                sys.exit(1)
            handle_mode_set(sys.argv[3])
        else:
            print(f"Unknown mode subcommand: {sub}")
            sys.exit(1)
    elif command == "policy":
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        if sub == "status":
            handle_policy_status()
        elif sub == "history":
            handle_policy_history()
        elif sub == "propose":
            handle_policy_propose()
        elif sub == "promote":
            if len(sys.argv) < 4:
                print("Usage: vx policy promote <policy_id>")
                sys.exit(1)
            handle_policy_promote(sys.argv[3])
        elif sub == "rollback":
            if len(sys.argv) < 4:
                print("Usage: vx policy rollback <policy_id>")
                sys.exit(1)
            handle_policy_rollback(sys.argv[3])
        else:
            print(f"Unknown policy subcommand: {sub}")
            sys.exit(1)
    elif command == "sandbox":
        sub = sys.argv[2] if len(sys.argv) > 2 else "run"
        if sub == "run":
            policy_id = None
            num_ops = 10
            if "--policy" in sys.argv:
                idx = sys.argv.index("--policy")
                if idx + 1 < len(sys.argv):
                    policy_id = sys.argv[idx + 1]
            if "--ops" in sys.argv:
                idx = sys.argv.index("--ops")
                if idx + 1 < len(sys.argv):
                    num_ops = int(sys.argv[idx + 1])
            if not policy_id:
                print("Usage: vx sandbox run --policy <policy_id> [--ops <N>]")
                sys.exit(1)
            handle_sandbox_run(policy_id, num_ops)
        else:
            print(f"Unknown sandbox subcommand: {sub}")
            sys.exit(1)
    elif command == "router-report":
        handle_router_report()
    elif command == "router-learn":
        _handle_router_learn(sys.argv[2:])
    elif command == "route":
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        if sub == "status":
            handle_route_status()
        elif sub == "test":
            handle_route_test()
        else:
            print(f"Unknown route subcommand: {sub}")
            print("Usage: vx route status | vx route test")
            sys.exit(1)
    elif command == "learn":
        from cli.learn_cli import handle_learn_command
        handle_learn_command(sys.argv[2:])
    elif command == "agent":
        from agent.cli import handle_agent_command
        handle_agent_command(sys.argv[2:])
    elif command == "up":
        _handle_up()
    elif command == "down":
        _handle_down()
    elif command == "check":
        _handle_check()
    elif command == "monitor":
        _handle_monitor()
    elif command == "activate":
        handle_activate()
    elif command == "watch":
        handle_watch(sys.argv[2:])
    elif command == "run":
        interval = 5.0
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                interval = float(sys.argv[idx + 1])
        handle_run(interval=interval)
    elif command == "status":
        asyncio.run(handle_status())
        # Append learn subsystem status
        try:
            from cli.learn_cli import learn_status_summary
            summary = learn_status_summary()
            if summary:
                print("\n  Learn Subsystem:")
                print(summary)
        except Exception:
            pass
    elif command == "models":
        asyncio.run(handle_list_models())
    elif command == "start":
        asyncio.run(handle_start())
    elif command == "inject-mass":
        if len(sys.argv) < 3:
            print("Usage: vx inject-mass <node_id_or_prefix> --mass <value>")
            sys.exit(1)
        node_id = sys.argv[2]
        if "--mass" not in sys.argv:
            print("❌ --mass <value> is required")
            print("Usage: vx inject-mass <node_id_or_prefix> --mass <value>")
            sys.exit(1)
        mass_idx = sys.argv.index("--mass")
        if mass_idx + 1 >= len(sys.argv):
            print("❌ --mass requires a numeric value (e.g. --mass 0.85)")
            sys.exit(1)
        try:
            mass_val = float(sys.argv[mass_idx + 1])
        except ValueError:
            print(f"❌ Invalid mass value: {sys.argv[mass_idx + 1]}")
            sys.exit(1)
        handle_inject_mass(node_id, mass_val)
    elif command == "propose":
        # Require description for propose
        if len(sys.argv) < 3:
            print("❌ Error: 'propose' requires a description")
            print("Usage: vx propose \"description of change\"")
            sys.exit(1)
        
        # Check for --remote flag
        use_remote = "--remote" in sys.argv
        remaining = [a for a in sys.argv[2:] if a not in ("--remote",)]

        if not remaining or remaining[0].startswith("--"):
            print("❌ Error: 'propose' requires a description")
            print('Usage: vx propose "description of change"')
            sys.exit(1)

        description = remaining[0]
        model = "qwen2.5-coder:7b"  # Default model for proposals
        
        # Check for --model flag
        if "--model" in sys.argv:
            idx = sys.argv.index("--model")
            if idx + 1 < len(sys.argv):
                model = sys.argv[idx + 1]
        
        if use_remote:
            asyncio.run(handle_propose_remote(description, model=model))
        else:
            asyncio.run(handle_propose(description, model=model))
    else:
        # Treat as a prompt for generation
        prompt = command
        model = "llama3.2:3b"
        
        # Check for --model flag
        if "--model" in sys.argv:
            idx = sys.argv.index("--model")
            if idx + 1 < len(sys.argv):
                model = sys.argv[idx + 1]
        
        asyncio.run(handle_generate(prompt, model=model))


# ---------------------------------------------------------------------------
# vx up / down / check — arranque unificado via supervisor
# ---------------------------------------------------------------------------

def _handle_up():
    """Arranca todos los motores de Vectrax via launchd."""
    import subprocess

    plist_src = Path(__file__).parent.parent / "com.vectrax.supervisor.plist"
    plist_dst = Path.home() / "Library" / "LaunchAgents" / "com.vectrax.supervisor.plist"
    project = Path(__file__).parent.parent
    supervisor = project / "vectrax_supervisor.py"

    # Check if already running
    result = subprocess.run(
        [sys.executable, str(supervisor), "--check"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(result.stdout.strip())
        print("Vectrax ya está corriendo. Usa 'vx check' para ver estado.")
        return

    # Kill any orphan processes first
    subprocess.run(["pkill", "-9", "-f", "vectrax_supervisor"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "vectrax.telegram_gateway"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "vectrax_unified.py"], capture_output=True)
    time.sleep(2)

    # Clean lock files
    lock_file = Path.home() / ".vectrax" / "supervisor.lock"
    pid_file = Path.home() / ".vectrax" / "supervisor.pid"
    lock_file.unlink(missing_ok=True)
    pid_file.unlink(missing_ok=True)

    # Ensure plist is installed
    if plist_src.exists():
        import shutil
        shutil.copy2(str(plist_src), str(plist_dst))

    # Start via launchd (single source of truth — no direct Popen)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_dst)],
        capture_output=True,
    )
    time.sleep(1)
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_dst)],
        capture_output=True,
    )

    # Wait and verify
    time.sleep(5)
    check = subprocess.run(
        [sys.executable, str(supervisor), "--check"],
        capture_output=True, text=True,
    )

    if check.returncode == 0:
        print("Vectrax arrancado.")
        _handle_check()
    else:
        print("Error al arrancar. Revisa ~/.vectrax/supervisor.log")


def _handle_down():
    """Para todos los motores de Vectrax."""
    import subprocess

    plist_dst = Path.home() / "Library" / "LaunchAgents" / "com.vectrax.supervisor.plist"

    # Stop via launchd first (prevents KeepAlive from respawning)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_dst)],
        capture_output=True,
    )
    time.sleep(2)

    # Kill anything remaining
    subprocess.run(["pkill", "-9", "-f", "vectrax_supervisor"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "vectrax.telegram_gateway"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "vectrax_unified.py"], capture_output=True)
    time.sleep(1)

    # Clean lock files
    lock_file = Path.home() / ".vectrax" / "supervisor.lock"
    pid_file = Path.home() / ".vectrax" / "supervisor.pid"
    lock_file.unlink(missing_ok=True)
    pid_file.unlink(missing_ok=True)

    # Verify
    result = subprocess.run(
        ["pgrep", "-f", "vectrax_supervisor|telegram_gateway|vectrax_unified"],
        capture_output=True, text=True,
    )
    if not result.stdout.strip():
        print("Todos los motores detenidos.")
    else:
        print("Forzando limpieza...")
        subprocess.run(["pkill", "-9", "-f", "vectrax"], capture_output=True)
        time.sleep(1)
        print("Detenidos.")


def _handle_monitor():
    """Muestra métricas del sistema en tiempo real."""
    from core.operator.system_monitor import format_status
    from core.operator.load_governor import get_load_governor

    gov = get_load_governor()
    gov.evaluate()

    level_icon = {
        "green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴",
    }

    print(f"\n{'='*50}")
    print(f"  VECTRAX SYSTEM MONITOR")
    print(f"{'='*50}")
    print(format_status())
    print(f"")
    print(f"  Load Governor:")
    print(f"    Pressure:    {level_icon.get(gov.level.value, '')} {gov.level.value.upper()}")
    print(f"    Heavy jobs:  {'ACCEPT' if gov.should_accept_heavy_job() else 'REJECT'}")
    print(f"    Any jobs:    {'ACCEPT' if gov.should_accept_any_job() else 'REJECT'}")
    print(f"{'='*50}\n")


def _handle_check():
    """Verifica el estado de todos los motores."""
    import subprocess

    project = Path(__file__).parent.parent
    supervisor = project / "vectrax_supervisor.py"

    # Supervisor status
    result = subprocess.run(
        [sys.executable, str(supervisor), "--check"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  Supervisor:  OK ({result.stdout.strip()})")
    else:
        print("  Supervisor:  NO RUNNING")
        return

    # Check child processes
    procs = subprocess.run(
        ["pgrep", "-afl", "vectrax"],
        capture_output=True, text=True,
    )
    lines = [l for l in procs.stdout.strip().split("\n") if l.strip()]

    tg = any("telegram_gateway" in l for l in lines)
    meta = any("vectrax_unified" in l for l in lines)

    print(f"  Telegram:    {'OK' if tg else 'NO RUNNING'}")
    print(f"  Meta Loop:   {'OK' if meta else 'NO RUNNING'}")
    print(f"  Procesos:    {len(lines)}")


if __name__ == "__main__":
    main()
