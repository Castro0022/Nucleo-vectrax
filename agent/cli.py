"""
Vectrax Agent — CLI Commands
===============================
`vx agent start`  — start the agent daemon
`vx agent status` — check agent status
"""

import asyncio
import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

from agent.config import load_agent_config
from agent.client import AgentClient


async def _agent_status():
    cfg = load_agent_config()
    client = AgentClient(cfg)

    print(f"🤖 Vectrax Agent")
    print(f"   ID:   {cfg.agent_id}")
    print(f"   Mode: {cfg.mode}")
    print(f"   Core: {cfg.core_url}")

    reachable = await client.health_check()
    if reachable:
        print(f"   Core status: ✅ reachable")
    else:
        print(f"   Core status: ❌ unreachable")

    await client.close()


async def _agent_start():
    from agent.daemon import run_agent

    cfg = load_agent_config()
    print(f"🤖 Starting Vectrax Agent (id={cfg.agent_id}, mode={cfg.mode})")
    print(f"   Core URL: {cfg.core_url}")
    print(f"   Press Ctrl+C to stop.\n")
    await run_agent(cfg)


def handle_agent_command(args: list):
    """Dispatch agent subcommands from vx CLI."""
    if not args or args[0] in ("help", "--help", "-h"):
        print("""
🤖 vx agent — Vectrax Local Agent

COMMANDS:
    vx agent start    Start the agent daemon
    vx agent status   Check agent status and Core connectivity
""")
        return

    subcmd = args[0]

    if subcmd == "status":
        asyncio.run(_agent_status())
    elif subcmd == "start":
        asyncio.run(_agent_start())
    else:
        print(f"❌ Unknown agent command: {subcmd}")
        print("   Use: vx agent start | vx agent status")
