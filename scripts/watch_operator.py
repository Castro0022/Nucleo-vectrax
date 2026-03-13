#!/usr/bin/env python3
"""
vx watch — Observación del Operador Universal Vectrax
========================================================
Muestra el estado completo del sistema operando con todos sus motores
activos, sin ejecutar acciones ni modificar archivos.

Modo: observación pura.

Usage:
    vx watch            Vista completa (snapshot)
    vx watch --live     Refresco continuo cada 3s
    vx watch --cycle    Ejecutar un ciclo de observación y mostrar resultado
    vx watch --json     Salida en JSON
"""

from __future__ import annotations

import json
import os
import sys
import time

# Ensure project root in path
BASE_DIR = os.path.expanduser("~/Vectrax")
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


# ---------------------------------------------------------------------------
# Data collector — delegates to sovereign status authority
# ---------------------------------------------------------------------------

def _collect_system_status() -> dict:
    """
    Collect full operator status without side effects.

    Single source of truth: core.operator.activation.get_operator_status()
    """
    from core.operator.activation import get_operator_status
    return get_operator_status()


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _render_system_status(data: dict) -> None:
    """Render SYSTEM STATUS section."""
    identity = data.get("identity", {})
    runtime = data.get("runtime", {})
    integrity = data.get("integrity", {})

    state = runtime.get("state", "unknown")
    state_icon = {
        "active": "[bold green]● ACTIVE[/bold green]",
        "paused": "[yellow]◐ PAUSED[/yellow]",
        "booting": "[cyan]⟳ BOOTING[/cyan]",
        "inactive": "[dim]○ INACTIVE[/dim]",
        "error": "[bold red]✗ ERROR[/bold red]",
    }.get(state, f"[dim]{state}[/dim]")

    mode = runtime.get("mode", "unknown")
    mode_icon = {
        "guided": "[cyan]◎ GUIDED[/cyan]",
        "supervised": "[yellow]● SUPERVISED[/yellow]",
        "autonomous": "[bold green]◉ AUTONOMOUS[/bold green]",
    }.get(mode, mode)

    healthy = integrity.get("healthy", False)
    health_str = "[bold green]HEALTHY[/bold green]" if healthy else "[bold red]DEGRADED[/bold red]"

    console.print(Panel(
        f"  Identity:    [bold]{identity.get('name', '?')}[/bold] v{identity.get('version', '?')}\n"
        f"  Creator:     {identity.get('creator', '?')}\n"
        f"  Nature:      {identity.get('nature', '?')}\n"
        f"  State:       {state_icon}\n"
        f"  Mode:        {mode_icon}\n"
        f"  Health:      {health_str}\n"
        f"  Integrity:   {'[green]✓ verified[/green]' if identity.get('integrity') else '[red]✗ failed[/red]'}\n"
        f"  Cycles:      {runtime.get('cycles_completed', 0)}\n"
        f"  Layers:      {integrity.get('active', 0)}/{integrity.get('total_layers', 0)} active\n"
        f"  Timestamp:   [dim]{data.get('timestamp', '?')}[/dim]",
        title="[bold white on blue] SYSTEM STATUS [/bold white on blue]",
        border_style="blue",
        expand=True,
    ))


def _render_active_engines(data: dict) -> None:
    """Render ACTIVE ENGINES section."""
    layers = data.get("layers", [])
    if not layers:
        console.print("[dim]No layer data available.[/dim]")
        return

    table = Table(
        title="[bold white on magenta] ACTIVE ENGINES [/bold white on magenta]",
        box=box.SIMPLE_HEAVY,
        expand=True,
    )
    table.add_column("#", width=3, justify="right")
    table.add_column("Layer", width=32)
    table.add_column("Status", width=10)
    table.add_column("Modules", width=5, justify="right")
    table.add_column("Deps", width=8)

    for layer in layers:
        status = layer.get("status", "?")
        status_str = {
            "active": "[bold green]● active[/bold green]",
            "partial": "[yellow]◐ partial[/yellow]",
            "planned": "[dim]○ planned[/dim]",
            "inactive": "[red]✗ off[/red]",
        }.get(status, status)

        deps = ", ".join(str(d) for d in layer.get("dependencies", [])) or "—"
        table.add_row(
            str(layer["id"]),
            layer["name"],
            status_str,
            str(len(layer.get("modules", []))),
            deps,
        )

    console.print(table)


def _render_domains(data: dict) -> None:
    """Render domains section inline."""
    domains = data.get("domains", {})
    authorized = domains.get("authorized", [])
    restricted = domains.get("restricted", [])

    auth_str = "  ".join(f"[green]{d}[/green]" for d in authorized) or "[dim]none[/dim]"
    rest_str = "  ".join(f"[red]{d}[/red]" for d in restricted) or "[dim]none[/dim]"

    console.print(Panel(
        f"  Authorized:  {auth_str}\n"
        f"  Restricted:  {rest_str}",
        title="[bold white on dark_orange] DOMAINS [/bold white on dark_orange]",
        border_style="dark_orange",
    ))


def _render_last_cycle(data: dict) -> None:
    """Render LAST CYCLE section."""
    runtime = data.get("runtime", {})
    cycles = runtime.get("cycles_completed", 0)

    if cycles == 0:
        console.print(Panel(
            "  [dim]No cycles executed yet. The operator is awaiting its first cycle.[/dim]",
            title="[bold white on cyan] LAST CYCLE [/bold white on cyan]",
            border_style="cyan",
        ))
        return

    last_cycle_id = runtime.get("last_cycle_id") or "—"
    last_outcome = runtime.get("last_cycle_outcome") or "—"
    last_signals = runtime.get("last_cycle_signals", 0)
    state_src = runtime.get("state_source", "in-memory")

    outcome_color = {
        "proposed": "green",
        "observed": "cyan",
        "idle": "dim",
        "error": "red",
    }.get(last_outcome, "white")

    console.print(Panel(
        f"  Cycles completed: [bold]{cycles}[/bold]\n"
        f"  Last cycle ID:    [bold]{last_cycle_id}[/bold]\n"
        f"  Last outcome:     [{outcome_color}]{last_outcome}[/{outcome_color}]\n"
        f"  Last signals:     [bold]{last_signals}[/bold]\n"
        f"  Mode:             {runtime.get('mode', '?')}\n"
        f"  State source:     [dim]{state_src}[/dim]",
        title="[bold white on cyan] LAST CYCLE [/bold white on cyan]",
        border_style="cyan",
    ))


def _render_recent_events(data: dict) -> None:
    """Render RECENT EVENTS section."""
    ledger = data.get("ledger", {})
    recent = ledger.get("recent", [])

    table = Table(
        title="[bold white on dark_green] RECENT EVENTS [/bold white on dark_green]",
        box=box.SIMPLE_HEAVY,
        expand=True,
    )
    table.add_column("ID", width=5, justify="right", style="dim")
    table.add_column("Action", width=45)
    table.add_column("Actor", width=10)

    if not recent:
        console.print(Panel("[dim]No operator events recorded yet.[/dim]",
                           title="[bold white on dark_green] RECENT EVENTS [/bold white on dark_green]",
                           border_style="dark_green"))
        return

    for e in recent:
        action = str(e.get("action", "?"))
        # Color-code certain actions
        if "error" in action or "failed" in action:
            action = f"[red]{action}[/red]"
        elif "activated" in action or "complete" in action:
            action = f"[green]{action}[/green]"
        elif "phase" in action:
            action = f"[cyan]{action}[/cyan]"
        elif "module" in action:
            action = f"[yellow]{action}[/yellow]"

        table.add_row(
            str(e.get("id", "?")),
            action,
            e.get("actor", "?"),
        )

    console.print(table)
    console.print(
        f"  [dim]Total ledger entries: {ledger.get('total_entries', 0)} | "
        f"Operator events: {ledger.get('operator_events', 0)}[/dim]"
    )


def _render_pending_hypotheses(data: dict) -> None:
    """Render PENDING HYPOTHESES section."""
    hyp = data.get("hypotheses", {})

    total = hyp.get("total", 0)
    active = hyp.get("active", 0)
    resolved = hyp.get("resolved", 0)
    avg_conf = hyp.get("avg_confidence", 0)
    by_status = hyp.get("by_status", {})
    active_list = hyp.get("active_list", [])

    status_line = "  ".join(
        f"{k}=[bold]{v}[/bold]" for k, v in by_status.items()
    ) if by_status else "[dim]no hypotheses[/dim]"

    lines = [
        f"  Total: [bold]{total}[/bold]  |  Active: [bold]{active}[/bold]  |  "
        f"Resolved: [bold]{resolved}[/bold]  |  Avg confidence: [bold]{avg_conf:.2f}[/bold]",
        f"  Status breakdown: {status_line}",
    ]

    if active_list:
        lines.append("")
        for h in active_list:
            conf = h.get("confidence", 0)
            conf_color = "green" if conf >= 0.7 else "yellow" if conf >= 0.4 else "dim"
            lines.append(
                f"  [{conf_color}]▸[/{conf_color}] {h['id']}  "
                f"[{conf_color}]{conf:.0%}[/{conf_color}]  "
                f"{h.get('evidence', 0)} evidence  "
                f"[dim]{h.get('description', '')[:50]}[/dim]"
            )
    elif active == 0:
        lines.append("  [dim]No active hypotheses. The system is in equilibrium.[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title="[bold white on purple] PENDING HYPOTHESES [/bold white on purple]",
        border_style="purple",
    ))


def _render_risk_status(data: dict) -> None:
    """Render RISK STATUS section."""
    risk = data.get("risk", {})
    gov_mode = risk.get("governor_mode", "unknown")

    mode_display = {
        "observe": "[green]◎ OBSERVE[/green] — monitoring only",
        "act": "[bold cyan]● ACT[/bold cyan] — executing allowed",
        "cautious": "[yellow]◐ CAUTIOUS[/yellow] — reduced autonomy",
        "recover": "[bold red]○ RECOVER[/bold red] — recovery mode, actions blocked",
    }.get(gov_mode, f"[dim]{gov_mode}[/dim]")

    autopatch = risk.get("autopatch_allowed", False)
    ap_str = "[green]allowed[/green]" if autopatch else "[yellow]blocked[/yellow]"

    console.print(Panel(
        f"  Governor mode:     {mode_display}\n"
        f"  Autopatch:         {ap_str}",
        title="[bold white on red] RISK STATUS [/bold white on red]",
        border_style="red",
    ))


def _render_approval_status(data: dict) -> None:
    """Render APPROVAL STATUS section."""
    approvals = data.get("approvals", {})
    pending_count = approvals.get("pending_count", 0)
    total = approvals.get("total_history", 0)
    pending_list = approvals.get("pending", [])

    pending_str = (
        f"[bold yellow]{pending_count}[/bold yellow]"
        if pending_count > 0
        else "[green]0[/green]"
    )

    lines = [
        f"  Pending approvals: {pending_str}  |  Total history: {total}",
    ]

    if pending_list:
        lines.append("")
        for r in pending_list:
            lines.append(
                f"  [yellow]▸[/yellow] {r['id']}  "
                f"risk=[bold]{r.get('risk', 0):.2f}[/bold]  "
                f"[dim]{r.get('action', '')[:40]}[/dim]"
            )
    elif pending_count == 0:
        lines.append("  [dim]No actions awaiting creator approval.[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title="[bold white on yellow] APPROVAL STATUS [/bold white on yellow]",
        border_style="yellow",
    ))


def _render_next_action(data: dict) -> None:
    """Render NEXT OBSERVABLE ACTION section."""
    runtime = data.get("runtime", {})
    state = runtime.get("state", "unknown")
    mode = runtime.get("mode", "unknown")
    approvals = data.get("approvals", {})
    hyp = data.get("hypotheses", {})
    bus = data.get("bus", {})

    lines = []

    if state == "not_initialized" or state == "inactive":
        lines.append(
            "  [cyan]→[/cyan] Operator not yet activated. Run:"
        )
        lines.append(
            "    [bold]vx activate[/bold]  — activate the operator"
        )
        lines.append(
            "    [bold]vx run[/bold]       — activate and start continuous cycles"
        )
    elif state == "active":
        lines.append(f"  [green]●[/green] Operator is [bold]ACTIVE[/bold] in [cyan]{mode}[/cyan] mode")

        pending = approvals.get("pending_count", 0)
        if pending > 0:
            lines.append(f"  [yellow]→[/yellow] {pending} action(s) awaiting creator approval")

        active_hyp = hyp.get("active", 0)
        if active_hyp > 0:
            lines.append(f"  [purple]→[/purple] {active_hyp} hypothesis(es) under evaluation")

        if mode == "guided":
            lines.append(
                "  [dim]→ GUIDED mode: observing and proposing. No autonomous execution.[/dim]"
            )

        lines.append(
            f"  [dim]  Bus: {bus.get('total_published', 0)} events published, "
            f"{bus.get('subscriptions', 0)} subscriptions[/dim]"
        )
    elif state == "paused":
        lines.append("  [yellow]◐[/yellow] Operator is PAUSED. Use runtime.resume() to continue.")
    elif state == "error":
        lines.append("  [red]✗[/red] Operator encountered an error. Check logs.")

    console.print(Panel(
        "\n".join(lines),
        title="[bold white on dark_cyan] NEXT OBSERVABLE ACTION [/bold white on dark_cyan]",
        border_style="dark_cyan",
    ))


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_watch(data: dict) -> None:
    """Render full watch output."""
    console.print()
    console.rule("[bold]vx watch — Vectrax Operator Observatory[/bold]", style="blue")
    console.print()

    _render_system_status(data)
    console.print()
    _render_active_engines(data)
    console.print()
    _render_domains(data)
    console.print()
    _render_last_cycle(data)
    console.print()
    _render_recent_events(data)
    console.print()
    _render_pending_hypotheses(data)
    console.print()
    _render_risk_status(data)
    console.print()
    _render_approval_status(data)
    console.print()
    _render_next_action(data)
    console.print()
    console.rule("[dim]Observation mode — no actions executed[/dim]", style="dim")
    console.print()


def run_cycle_and_render() -> None:
    """Run one observation cycle and render results."""
    try:
        from core.operator.activation import get_runtime

        runtime = get_runtime()
        if not runtime.is_active:
            console.print("[yellow]Activating operator for observation...[/yellow]")
            from core.operator.activation import activate_operator
            activate_operator()

        console.print("[dim]Running observation cycle...[/dim]")
        result = runtime.run_cycle()
        cd = result.to_dict()

        console.print(Panel(
            f"  Cycle:       [bold]{cd['cycle_id']}[/bold] (#{cd['cycle_number']})\n"
            f"  Outcome:     [bold]{cd['outcome']}[/bold]\n"
            f"  Mode:        {cd['mode']}\n"
            f"  Elapsed:     {cd['elapsed_ms']}ms\n\n"
            f"  Perception:  {cd['perception']['signals']} signals "
            f"({cd['perception']['restricted']} restricted)\n"
            f"  Relevance:   H={cd['relevance']['high']} "
            f"M={cd['relevance']['medium']} L={cd['relevance']['low']}\n"
            f"  Hypotheses:  {cd['hypotheses']['proposed']} proposed, "
            f"{cd['hypotheses']['active']} active\n"
            f"  Governance:  {cd['governance']['evaluations']} evals, "
            f"{cd['governance']['pending']} pending\n"
            f"  Proposals:   {cd['proposals_count']}\n"
            f"  Bus events:  {cd['bus_events']}\n\n"
            f"  Step timings:\n" + "\n".join(
                f"    {step:15s} {ms:.2f}ms"
                for step, ms in cd.get("step_timings", {}).items()
            ),
            title="[bold white on cyan] OBSERVATION CYCLE RESULT [/bold white on cyan]",
            border_style="cyan",
        ))

    except Exception as exc:
        console.print(f"[red]Cycle error: {exc}[/red]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    """Entry point — callable in-process or as __main__."""
    if args is None:
        args = sys.argv[1:] if len(sys.argv) > 1 else []

    # Parse flags
    json_mode = "--json" in args
    live_mode = "--live" in args
    cycle_mode = "--cycle" in args

    if "--help" in args or "-h" in args:
        console.print(__doc__)
        sys.exit(0)

    if cycle_mode:
        run_cycle_and_render()
        data = _collect_system_status()
        if json_mode:
            print(json.dumps(data, indent=2, default=str))
        else:
            render_watch(data)
        return

    if json_mode and not live_mode:
        data = _collect_system_status()
        print(json.dumps(data, indent=2, default=str))
        return

    if live_mode:
        try:
            while True:
                os.system("clear" if os.name != "nt" else "cls")
                data = _collect_system_status()
                if json_mode:
                    print(json.dumps(data, indent=2, default=str))
                else:
                    render_watch(data)
                    console.print("[dim]Refreshing in 3s... (Ctrl+C to stop)[/dim]")
                time.sleep(3)
        except KeyboardInterrupt:
            console.print("\n[dim]Watch stopped.[/dim]")
        return

    # Default: single snapshot
    data = _collect_system_status()
    render_watch(data)


if __name__ == "__main__":
    main()
