#!/usr/bin/env python3
"""
vx - Vectrax CLI Tool
Main entry point for all CLI commands
"""
import sys
import asyncio
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
    <prompt>              Generate response from AI
    propose <description> Propose system changes (shows diff, risk, waits for approval)
    propose --remote <d>  Propose via Core Central Service
    agent start           Start the local agent daemon
    agent status          Check agent status and Core connectivity
    route status          Show intelligence router health and model registry
    route test            Run routing test suite
    mode status           Show current operational mode
    mode set <MODE>       Set base mode (HOME_AUTO|BUSINESS_GOVERNED|MISSION_STRICT)
    policy status         Show active policy version
    policy history        Show policy version history
    policy propose        Create candidate policy
    policy promote <id>   Promote candidate to active
    policy rollback <id>  Rollback active policy
    sandbox run           Run policy sandbox simulation
    start                 Start Vectrax services
    status                Check system status
    models                List available models
    help                  Show this help message

EXAMPLES:
    vx "Explain quantum computing"
    vx propose "Add logging to SmartRouter class"
    vx mode status
    vx mode set BUSINESS_GOVERNED
    vx policy status
    vx sandbox run --policy abc123 --ops 20
    vx route status
    vx route test
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
    elif command == "agent":
        from agent.cli import handle_agent_command
        handle_agent_command(sys.argv[2:])
    elif command == "status":
        asyncio.run(handle_status())
    elif command == "models":
        asyncio.run(handle_list_models())
    elif command == "start":
        asyncio.run(handle_start())
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


if __name__ == "__main__":
    main()
