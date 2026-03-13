"""
Vectrax CLI — command line interface with permanent channel separation.

CREATOR channel (Mario — nucleus):
    vectrax creator ingest "<text>" [--success]
    vectrax creator observe [--pipe] [--file <path>]
    vectrax creator wake --set | --status
    vectrax creator stars [--layer core|mid|outer]
    vectrax creator constellations
    vectrax creator proposals [--all]
    vectrax creator approve <id> | reject <id>
    vectrax creator status

USER channel:
    vectrax user ingest "<text>" --name <username> [--success]
    vectrax user stars --name <username>
    vectrax user constellations --name <username>
    vectrax user status [--name <username>]

SYSTEM-WIDE (admin):
    vectrax map
    vectrax gravity
    vectrax status

DAEMON (background observer):
    vectrax daemon start [--foreground]
    vectrax daemon stop
    vectrax daemon status
    vectrax daemon restart
    vectrax daemon logs [--tail N]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from vectrax import db
from vectrax.identity import (
    CHANNEL_CREATOR,
    CHANNEL_USER,
    CREATOR_OWNER,
    ChannelViolation,
    channel_label,
)
from vectrax.models import LAYER_CORE, LAYER_MID, LAYER_OUTER

console = Console()

LAYER_COLORS = {
    LAYER_CORE: "bold yellow",
    LAYER_MID:  "cyan",
    LAYER_OUTER: "dim",
}
LAYER_ICONS = {
    LAYER_CORE: "★",
    LAYER_MID:  "☆",
    LAYER_OUTER: "·",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _render_stars_table(rows, title: str) -> None:
    if not rows:
        console.print("[dim]No stars found.[/dim]")
        return
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Layer", width=6)
    table.add_column("Gravity", justify="right", width=8)
    table.add_column("Rep", justify="right", width=5)
    table.add_column("Success%", justify="right", width=9)
    table.add_column("Content")
    for s in rows:
        color = LAYER_COLORS[s.layer]
        icon = LAYER_ICONS[s.layer]
        table.add_row(
            s.id[:8],
            f"[{color}]{icon} {s.layer}[/{color}]",
            f"{s.gravity_score:.4f}",
            str(s.repetition_count),
            f"{s.success_rate * 100:.0f}%",
            s.content[:80],
        )
    console.print(table)


def _render_constellations_table(rows, title: str) -> None:
    if not rows:
        console.print("[dim]No constellations detected yet.[/dim]")
        return
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Stars", justify="right", width=6)
    table.add_column("Gravity", justify="right", width=8)
    table.add_column("Coherence", justify="right", width=10)
    table.add_column("Success%", justify="right", width=9)
    table.add_column("Rep", justify="right", width=5)
    for c in rows:
        table.add_row(
            c.id[:8],
            str(c.member_count),
            f"{c.gravity_score:.4f}",
            f"{c.coherence_score:.4f}",
            f"{c.success_rate * 100:.0f}%",
            str(c.repetition_count),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Root CLI group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Vectrax — cognitive memory system organised as a gravitational star graph."""
    db.init_db()


# ---------------------------------------------------------------------------
# System-wide commands (admin view, no channel filter)
# ---------------------------------------------------------------------------

@cli.command(name="map")
def memory_map():
    """Show ASCII map of the full memory graph (all channels)."""
    from vectrax.graph import ascii_map, graph_summary, load_from_db
    with console.status("[dim]Loading graph…"):
        load_from_db()
    summary = graph_summary()
    console.print(Panel(
        f"Nodes: [bold]{summary['nodes']}[/bold]  "
        f"Edges: [bold]{summary['edges']}[/bold]  "
        f"Components: [bold]{summary['components']}[/bold]  "
        f"Isolated: [dim]{summary['isolated_stars']}[/dim]",
        title="[bold cyan]Memory Graph — All Channels[/bold cyan]",
        expand=False,
    ))
    console.print(ascii_map())


@cli.command()
def gravity():
    """Recompute gravity scores and reassign layers for all stars."""
    with console.status("[bold yellow]Recomputing gravity…"):
        from vectrax.engine import reorganize
        result = reorganize()
    dist = result["layer_distribution"]
    console.print(
        f"[bold green]✓[/bold green] Gravity recomputed: "
        f"{result['stars_updated']} stars, "
        f"{result['constellations_updated']} constellations updated.\n"
        f"  [yellow]★ core[/yellow]  {dist.get('core', 0)}  "
        f"  [cyan]☆ mid[/cyan]   {dist.get('mid', 0)}  "
        f"  [dim]· outer[/dim]  {dist.get('outer', 0)}"
    )


@cli.command()
def status():
    """System overview across all channels."""
    counts = db.get_counts()
    layers = counts.get("layers", {})
    console.print(Panel(
        f"[bold cyan]Stars[/bold cyan]          {counts['stars']}\n"
        f"  [yellow]★ core[/yellow]           {layers.get('core', 0)}\n"
        f"  [cyan]☆ mid[/cyan]            {layers.get('mid', 0)}\n"
        f"  [dim]· outer[/dim]          {layers.get('outer', 0)}\n\n"
        f"[bold cyan]Constellations[/bold cyan]  {counts['constellations']}\n"
        f"[bold yellow]Pending proposals[/bold yellow]  {counts['pending_proposals']}",
        title="[bold]Vectrax System Status — All Channels[/bold]",
        expand=False,
    ))


# ===========================================================================
# CREATOR CHANNEL  —  Mário / Núcleo
# ===========================================================================

@cli.group()
def creator():
    """[bold yellow]◈ Creator channel[/bold yellow] — nucleus (Mario only). \
Creation, concept, evolution, structural proposals."""


@creator.command(name="ingest")
@click.argument("text")
@click.option("--success", is_flag=True, default=False)
def creator_ingest(text: str, success: bool):
    """Capture a foundational event into the nucleus."""
    from vectrax.engine import ingest as _ingest
    from vectrax import wakeword as ww
    from vectrax import config as cfg
    from datetime import datetime

    # Wake word detection — triggers observer activation if matched
    if ww.detect_in_text(text, CHANNEL_CREATOR):
        result = ww.trigger_activation(source="creator")
        ts = datetime.fromtimestamp(result["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        console.print(Panel(
            f"[bold yellow]◈ VECTRAX DESPIERTO[/bold yellow]\n"
            f"Creator: {cfg.get('creator_identity')}\n"
            f"Timestamp: {ts}\n"
            f"Observer: ACTIVE",
            title="[bold yellow]◈ Wake Word Detected[/bold yellow]",
            expand=False,
        ))
        # Ingest the activation as a special foundational star
        activation_text = f"[WAKE ACTIVATION] {ts}"
        try:
            _ingest(activation_text, success=True,
                    channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
        except Exception:
            pass
        return

    try:
        with console.status("[bold yellow]Embedding into nucleus…"):
            star = _ingest(text, success=success,
                           channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
    except ChannelViolation as e:
        console.print(f"[bold red]Channel violation:[/bold red] {e}")
        sys.exit(1)
    console.print(
        f"[bold yellow]◈ NUCLEUS ★ Star [{star.id[:8]}] foundational — "
        f"layer={star.layer} | gravity={star.gravity_score:.4f} | rep={star.repetition_count}[/bold yellow]"
    )


@creator.command(name="stars")
@click.option("--layer", type=click.Choice([LAYER_CORE, LAYER_MID, LAYER_OUTER]),
              default=None)
@click.option("--limit", default=50, show_default=True)
def creator_stars(layer, limit):
    """List nucleus stars sorted by gravity."""
    rows = db.get_all_stars(layer=layer, channel=CHANNEL_CREATOR,
                             owner=CREATOR_OWNER)[:limit]
    _render_stars_table(rows, title="[bold yellow]◈ Nucleus — Creator Stars[/bold yellow]")


@creator.command(name="constellations")
def creator_constellations():
    """List nucleus constellations."""
    rows = db.get_all_constellations(channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
    _render_constellations_table(
        rows, title="[bold yellow]◈ Nucleus — Creator Constellations[/bold yellow]"
    )


@creator.command(name="proposals")
@click.option("--all", "show_all", is_flag=True, default=False)
def creator_proposals(show_all: bool):
    """List structural proposals (creator approval required)."""
    status_filter = None if show_all else "pending"
    rows = db.get_proposals(status=status_filter)
    if not rows:
        console.print("[dim]No proposals.[/dim]")
        return
    for p in rows:
        ts = datetime.fromtimestamp(p.created_at).strftime("%Y-%m-%d %H:%M")
        sc = {"pending": "yellow", "approved": "green", "rejected": "red"}.get(p.status, "white")
        console.print(Panel(
            f"[dim]{p.description}[/dim]\n\n"
            f"Constellation: [cyan]{p.constellation_id[:8]}[/cyan]  "
            f"Status: [{sc}]{p.status}[/{sc}]  Created: {ts}",
            title=f"[bold yellow]Proposal {p.id[:8]}[/bold yellow]",
            expand=False,
        ))


def _resolve_proposal(pid: str):
    p = db.get_proposal(pid)
    if p is None:
        matches = [x for x in db.get_proposals() if x.id.startswith(pid)]
        if not matches:
            console.print(f"[red]Proposal '{pid}' not found.[/red]")
            sys.exit(1)
        p = matches[0]
    return p


@creator.command(name="approve")
@click.argument("proposal_id")
def creator_approve(proposal_id: str):
    """Approve a structural proposal."""
    p = _resolve_proposal(proposal_id)
    if p.status != "pending":
        console.print(f"[yellow]Proposal is already {p.status}.[/yellow]")
        return
    db.update_proposal_status(p.id, "approved")
    console.print(
        f"[bold green]✓[/bold green] Proposal [cyan]{p.id[:8]}[/cyan] approved — "
        "apply the structural change when ready."
    )


@creator.command(name="reject")
@click.argument("proposal_id")
def creator_reject(proposal_id: str):
    """Reject a structural proposal."""
    p = _resolve_proposal(proposal_id)
    if p.status != "pending":
        console.print(f"[yellow]Proposal is already {p.status}.[/yellow]")
        return
    db.update_proposal_status(p.id, "rejected")
    console.print(f"[bold red]✗[/bold red] Proposal [cyan]{p.id[:8]}[/cyan] rejected.")


@creator.command(name="observe")
@click.option("--pipe", "pipe_mode", is_flag=True, default=False,
              help="Pipe mode: read from stdin without prompts (e.g. tail -f log | vectrax creator observe --pipe).")
@click.option("--file", "watch_file", default=None,
              help="Tail a file and ingest each new line as a star.")
def creator_observe(pipe_mode: bool, watch_file: str):
    """Activate permanent observer mode — every event becomes a star."""
    from vectrax.observer import Observer, tail_file
    from vectrax.engine import ingest as _ingest
    from vectrax import config as cfg
    from vectrax import wakeword as ww

    if not ww.is_configured():
        console.print(
            "[yellow]Warning:[/yellow] Wake word not set. "
            "Run [bold]vectrax creator wake --set[/bold] to configure it."
        )

    if watch_file:
        # File-tail mode
        console.print(
            f"[bold yellow]◈ OBSERVER[/bold yellow] watching [dim]{watch_file}[/dim] — Ctrl-C to stop"
        )
        def _on_line(line: str):
            if not line.strip():
                return
            try:
                star = _ingest(line, success=False,
                               channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
                cfg.increment_events(1)
                ts = datetime.now().strftime("%H:%M:%S")
                console.print(
                    f"  [[dim]{ts}[/dim]] [yellow]★[/yellow] [{star.id[:8]}] {line[:60]}"
                )
            except Exception as exc:
                console.print(f"  [dim][observer error] {exc}[/dim]")
        try:
            cfg.activate_observer()
            tail_file(watch_file, _on_line)
        except (KeyboardInterrupt, FileNotFoundError) as e:
            cfg.deactivate_observer()
            if isinstance(e, FileNotFoundError):
                console.print(f"[red]File not found: {watch_file}[/red]")
        return

    obs = Observer(pipe_mode=pipe_mode)
    obs.start()


@creator.command(name="wake")
@click.option("--set", "do_set", is_flag=True, default=False,
              help="Set the secret wake word (input is hidden, never logged).")
@click.option("--status", "do_status", is_flag=True, default=False,
              help="Show wake word configuration status.")
def creator_wake(do_set: bool, do_status: bool):
    """Manage the secret wake word for system activation."""
    import getpass
    from vectrax import wakeword as ww
    from vectrax import config as cfg

    if do_set:
        console.print(
            "[bold yellow]◈ Wake Word Setup[/bold yellow]\n"
            "[dim]Input is hidden. The word will be stored as a hash only.[/dim]\n"
            "[dim]Nobody — including Vectrax — can read it back.[/dim]"
        )
        word = getpass.getpass("  Enter wake word: ")
        confirm = getpass.getpass("  Confirm wake word: ")
        if word != confirm:
            console.print("[red]Words do not match. Wake word not set.[/red]")
            sys.exit(1)
        if not word.strip():
            console.print("[red]Wake word cannot be empty.[/red]")
            sys.exit(1)
        ww.set_wake_word(word)
        console.print(
            "[bold green]✓[/bold green] Wake word configured. "
            "[dim]Hash stored. Plain word discarded.[/dim]"
        )
        return

    # Default: show status
    status = ww.activation_status()
    from datetime import datetime
    last = (
        datetime.fromtimestamp(status["last_activation"]).strftime("%Y-%m-%d %H:%M:%S")
        if status["last_activation"] else "never"
    )
    console.print(Panel(
        f"Wake word configured  {'[green]yes[/green]' if status['wake_word_configured'] else '[red]no[/red]'}\n"
        f"Observer active       {'[green]yes[/green]' if status['observer_active'] else '[dim]no[/dim]'}\n"
        f"Last activation       [dim]{last}[/dim]\n"
        f"Total events observed {status['total_events_observed']}",
        title="[bold yellow]◈ Wake Word — Status[/bold yellow]",
        expand=False,
    ))


# ---------------------------------------------------------------------------
# Intelligence Router command handlers (used inside creator chat)
# ---------------------------------------------------------------------------

def _handle_ai_command(prompt: str, session_id: str, ib, mem):
    """Handle /ai <prompt> — route to the single best model."""
    prompt = prompt.strip()
    if not prompt:
        console.print("  [dim]Uso: /ai <tu pregunta>[/dim]")
        return

    if not ib.is_ready():
        console.print("  [red]Intelligence Router no disponible. No hay modelos detectados.[/red]")
        return

    # Build memory context for the system prompt
    context = mem.recall(prompt)
    echo = context.get("echo_star")
    sys_prompt = None
    if echo:
        sys_prompt = (
            f"Context from Vectrax memory: \"{echo.content[:300]}\" "
            f"(gravity={echo.gravity_score:.3f}, layer={echo.layer})"
        )

    with console.status("[bold cyan]◈ Consultando inteligencia…[/bold cyan]"):
        result = ib.route_single(prompt, system_prompt=sys_prompt)

    db.insert_message(session_id, "mario", f"/ai {prompt}")

    if result["success"]:
        provider = result.get("provider", "?")
        model = result.get("model", "?")
        latency = result.get("latency_ms", 0)
        tokens = result.get("tokens", 0)
        task = result.get("task_type", "?")
        content = result["content"]

        console.print(Panel(
            f"{content}\n\n"
            f"[dim]{provider}:{model} | {latency:.0f}ms | {tokens} tokens | task={task}[/dim]",
            title=f"[bold cyan]◈ {provider}[/bold cyan]",
            expand=False,
        ))
        db.insert_message(session_id, "vectrax", f"[AI:{provider}] {content[:500]}")
    else:
        error = result.get("error", "Error desconocido")
        console.print(f"  [red]Error: {error}[/red]")
        db.insert_message(session_id, "vectrax", f"[AI:ERROR] {error}")


def _handle_multi_command(prompt: str, session_id: str, ib, mem):
    """Handle /multi <prompt> — parallel query + synthesis."""
    prompt = prompt.strip()
    if not prompt:
        console.print("  [dim]Uso: /multi <tu pregunta>[/dim]")
        return

    if not ib.is_ready():
        console.print("  [red]Intelligence Router no disponible.[/red]")
        return

    context = mem.recall(prompt)
    echo = context.get("echo_star")
    sys_prompt = None
    if echo:
        sys_prompt = (
            f"Context from Vectrax memory: \"{echo.content[:300]}\" "
            f"(gravity={echo.gravity_score:.3f}, layer={echo.layer})"
        )

    with console.status("[bold magenta]◈ Consultando múltiples modelos…[/bold magenta]"):
        result = ib.query_multi(prompt, system_prompt=sys_prompt)

    db.insert_message(session_id, "mario", f"/multi {prompt}")

    if result["success"]:
        content = result["content"]
        best = result.get("best_provider", "?")
        consensus = result.get("consensus_score", 0)
        compared = result.get("providers_compared", 0)
        latency = result.get("latency_ms", 0)
        task = result.get("task_type", "?")

        # Show synthesis breakdown
        synth = result.get("synthesis", {})
        scores = synth.get("provider_scores", [])
        ranking_lines = []
        for ps in scores:
            name = ps.get("provider", "?")
            rank = ps.get("rank", 0)
            total = ps.get("scores", {}).get("total", 0)
            ranking_lines.append(f"  #{rank} {name} ({total:.3f})")
        ranking_text = "\n".join(ranking_lines) if ranking_lines else ""

        console.print(Panel(
            f"{content}\n\n"
            f"[dim]Mejor: {best} | Consenso: {consensus:.2%} | "
            f"Modelos: {compared} | {latency:.0f}ms | task={task}[/dim]"
            + (f"\n[dim]{ranking_text}[/dim]" if ranking_text else ""),
            title=f"[bold magenta]◈ Multi-Modelo ({compared} proveedores)[/bold magenta]",
            expand=False,
        ))
        db.insert_message(session_id, "vectrax",
                          f"[MULTI:{best}|consensus={consensus:.2f}] {content[:500]}")
    else:
        error = result.get("error", "Error desconocido")
        console.print(f"  [red]Error: {error}[/red]")
        db.insert_message(session_id, "vectrax", f"[MULTI:ERROR] {error}")


def _handle_router_command(ib):
    """Handle /router — show Intelligence Router status."""
    status = ib.router_status()

    if "error" in status:
        console.print(f"  [red]Error: {status['error']}[/red]")
        return

    registry = status.get("connector_registry", {})
    providers = registry.get("providers", {})
    feedback = status.get("feedback_summary", {})

    lines = [
        f"[bold cyan]◈ Intelligence Router[/bold cyan]",
        f"  Inicializado: {'[green]sí[/green]' if status.get('initialized') else '[red]no[/red]'}",
        f"  Proveedores:  {registry.get('total', 0)} "
        f"(healthy: {registry.get('healthy', 0)}, local: {registry.get('local', 0)})",
    ]

    for name, info in providers.items():
        health = "[green]●[/green]" if info.get("healthy") else "[red]●[/red]"
        loc = "local" if info.get("is_local") else "cloud"
        lines.append(f"    {health} {name} [{loc}] models={info.get('models_count', 0)}")

    tracked = feedback.get("total_models_tracked", 0)
    if tracked:
        lines.append(f"  Feedback: {tracked} modelos tracked")
        for mid, minfo in feedback.get("models", {}).items():
            lines.append(
                f"    {minfo.get('label', mid)}: "
                f"queries={minfo.get('query_count', 0)} "
                f"success={minfo.get('success_rate', 0):.0%} "
                f"avg_latency={minfo.get('avg_latency_ms', 0):.0f}ms"
            )

    console.print(Panel("\n".join(lines), expand=False))


@creator.command(name="chat")
def creator_chat():
    """Loop conversacional persistente — Vectrax recuerda todo."""
    import uuid
    from vectrax.engine import ingest as _ingest
    from vectrax import wakeword as ww
    from vectrax import config as cfg
    from vectrax import memory as mem
    from vectrax import intelligence_bridge as ib

    session_id = str(uuid.uuid4())

    # ── Inicializar Intelligence Router (non-blocking) ────────────────────
    with console.status("[dim]◈ Detectando modelos de inteligencia…[/dim]"):
        ir_result = ib.initialize()
    ir_providers = ir_result.get("providers_detected", [])
    if ir_providers:
        ir_line = f"Modelos: [bold green]{', '.join(ir_providers)}[/bold green]"
    else:
        ir_line = "[dim]Sin modelos LLM detectados. /ai no disponible.[/dim]"

    # ── Bienvenida ──────────────────────────────────────────────────────────
    welcome_text = mem.build_welcome(session_id)
    console.print(Panel(
        f"[bold yellow]◈ VECTRAX — CHAT ACTIVO[/bold yellow]\n"
        f"Creator: [bold]{cfg.get('creator_identity')}[/bold]\n"
        f"Sesión:  [dim]{session_id[:8]}[/dim]\n"
        f"{ir_line}\n\n"
        f"{welcome_text}\n\n"
        f"[dim]Comandos: /ai <prompt> · /multi <prompt> · /router · Ctrl-C salir[/dim]",
        title="[bold yellow]◈ Vectrax[/bold yellow]",
        expand=False,
    ))

    db.insert_message(session_id, "vectrax", f"[SESIÓN INICIADA] {welcome_text}")

    # ── Loop principal ───────────────────────────────────────────────────────
    try:
        while True:
            try:
                text = input("\n  Mario › ")
            except EOFError:
                break

            text = text.strip()
            if not text:
                continue

            # ── Comandos de inteligencia ──────────────────────────────
            if text.startswith("/ai "):
                _handle_ai_command(text[4:], session_id, ib, mem)
                continue
            if text.startswith("/multi "):
                _handle_multi_command(text[7:], session_id, ib, mem)
                continue
            if text == "/router":
                _handle_router_command(ib)
                continue

            # Wake word
            if ww.detect_in_text(text, CHANNEL_CREATOR):
                result = ww.trigger_activation(source="creator_chat")
                ts = datetime.fromtimestamp(result["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                console.print(Panel(
                    f"[bold yellow]◈ VECTRAX DESPIERTO[/bold yellow]\n"
                    f"Timestamp: {ts}  |  Observer: ACTIVE",
                    title="[bold yellow]◈ Wake Word[/bold yellow]",
                    expand=False,
                ))
                db.insert_message(session_id, "mario", text)
                db.insert_message(session_id, "vectrax", f"[WAKE ACTIVATION] {ts}")
                continue

            # Ingestar como creator star
            try:
                with console.status("[dim]★ Grabando en el núcleo…[/dim]"):
                    star = _ingest(text, success=True,
                                   channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
            except Exception as exc:
                console.print(f"[red]Error al ingestar: {exc}[/red]")
                continue

            # Guardar mensaje de Mario en conversations
            db.insert_message(session_id, "mario", text, star_id=star.id)

            # Recall semántico
            with console.status("[dim]◈ Buscando en memoria…[/dim]"):
                context = mem.recall(text)
            response = mem.build_response(text, context, session_id)

            # Guardar respuesta de Vectrax
            db.insert_message(session_id, "vectrax", response)

            # Mostrar respuesta
            console.print(Panel(
                f"[dim]★ [{star.id[:8]}] gravity={star.gravity_score:.4f} | "
                f"layer={star.layer} | rep={star.repetition_count}[/dim]\n\n"
                f"{response}",
                title="[bold yellow]◈ Vectrax recuerda[/bold yellow]",
                expand=False,
            ))

    except KeyboardInterrupt:
        pass

    # ── Cleanup Intelligence Router ──────────────────────────────────────────
    ib.cleanup()

    # ── Resumen de cierre ────────────────────────────────────────────────────
    summary = mem.session_summary(session_id)
    if summary["mario_turns"] > 0:
        # Guardar la sesión como star fundacional
        session_text = (
            f"[SESIÓN CHAT] {summary['mario_turns']} mensajes, "
            f"{summary['stars_created']} stars creadas. "
            f"Sesión {session_id[:8]}."
        )
        try:
            _ingest(session_text, success=True,
                    channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
        except Exception:
            pass

    console.print(Panel(
        f"Mensajes:       {summary['message_count']}\n"
        f"Turnos Mario:   {summary['mario_turns']}\n"
        f"Stars creadas:  {summary['stars_created']}\n"
        f"Sesión:         [dim]{session_id[:8]}[/dim]",
        title="[bold yellow]◈ Sesión cerrada[/bold yellow]",
        expand=False,
    ))


@creator.command(name="status")
def creator_status():
    """Nucleus status: creator stars and constellations."""
    c_stars = db.get_all_stars(channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
    c_constellations = db.get_all_constellations(channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
    pending = db.get_proposals(status="pending")
    console.print(Panel(
        f"[bold yellow]◈ NUCLEUS — CREATOR CHANNEL[/bold yellow]\n"
        f"Stars (foundational)  {len(c_stars)}\n"
        f"Constellations        {len(c_constellations)}\n"
        f"Pending proposals     {len(pending)}\n\n"
        f"[dim]This channel belongs exclusively to Mario.[/dim]\n"
        f"[dim]No user content enters the nucleus.[/dim]",
        title="[bold yellow]Creator Channel — Status[/bold yellow]",
        expand=False,
    ))


# ===========================================================================
# USER CHANNEL  —  Stars and constellations
# ===========================================================================

@cli.group()
def user():
    """[cyan]◉ User channel[/cyan] — per-user stars and constellations. \
Tasks, support, local evolution."""


@user.command(name="ingest")
@click.argument("text")
@click.option("--name", required=True, help="Username (your identity in the graph).")
@click.option("--success", is_flag=True, default=False)
def user_ingest(text: str, name: str, success: bool):
    """Capture an event into a user's star."""
    from vectrax.engine import ingest as _ingest
    if name == CREATOR_OWNER:
        console.print(
            f"[red]Identity '{name}' is reserved for the creator channel.[/red]\n"
            "Use [bold]vectrax creator ingest[/bold] instead."
        )
        sys.exit(1)
    try:
        with console.status(f"[cyan]Embedding event for user '{name}'…"):
            star = _ingest(text, success=success, channel=CHANNEL_USER, owner=name)
    except ChannelViolation as e:
        console.print(f"[bold red]Channel violation:[/bold red] {e}")
        sys.exit(1)
    color = LAYER_COLORS[star.layer]
    icon = LAYER_ICONS[star.layer]
    console.print(
        f"[{color}]{icon} [{name}] Star [{star.id[:8]}] — "
        f"layer={star.layer} | gravity={star.gravity_score:.4f} | "
        f"rep={star.repetition_count}[/{color}]"
    )


@user.command(name="stars")
@click.option("--name", required=True, help="Username.")
@click.option("--layer", type=click.Choice([LAYER_CORE, LAYER_MID, LAYER_OUTER]),
              default=None)
@click.option("--limit", default=50, show_default=True)
def user_stars(name: str, layer, limit):
    """List a user's stars."""
    rows = db.get_all_stars(layer=layer, channel=CHANNEL_USER, owner=name)[:limit]
    _render_stars_table(rows, title=f"[cyan]◉ User: {name} — Stars[/cyan]")


@user.command(name="constellations")
@click.option("--name", required=True, help="Username.")
def user_constellations(name: str):
    """List a user's constellations."""
    rows = db.get_all_constellations(channel=CHANNEL_USER, owner=name)
    _render_constellations_table(rows, title=f"[cyan]◉ User: {name} — Constellations[/cyan]")


@user.command(name="status")
@click.option("--name", default=None, help="Username (omit to show all users).")
def user_status(name):
    """User channel overview."""
    if name:
        u_stars = db.get_all_stars(channel=CHANNEL_USER, owner=name)
        u_constellations = db.get_all_constellations(channel=CHANNEL_USER, owner=name)
        console.print(Panel(
            f"[cyan]◉ USER: {name}[/cyan]\n"
            f"Stars            {len(u_stars)}\n"
            f"Constellations   {len(u_constellations)}",
            title=f"[cyan]User Channel — {name}[/cyan]",
            expand=False,
        ))
    else:
        all_user_stars = db.get_all_stars(channel=CHANNEL_USER)
        owners = sorted({s.owner for s in all_user_stars if s.owner})
        lines = [f"[cyan]◉ USER CHANNEL[/cyan]  ({len(all_user_stars)} stars, {len(owners)} users)"]
        for o in owners:
            o_stars = [s for s in all_user_stars if s.owner == o]
            lines.append(f"  {o:<20} {len(o_stars)} stars")
        console.print(Panel("\n".join(lines), title="[cyan]User Channel — All Users[/cyan]",
                            expand=False))


# ===========================================================================
# DAEMON  —  Background permanent observer
# ===========================================================================

@cli.group()
def daemon():
    """[bold magenta]▣ Background daemon[/bold magenta] — permanent observer process."""


@daemon.command(name="start")
@click.option("--foreground", is_flag=True, default=False,
              help="Run in foreground instead of background (for debugging).")
def daemon_start(foreground: bool):
    """Start the background observer daemon."""
    from vectrax import daemon as d
    if d.is_running():
        console.print(
            f"[yellow]Daemon already running[/yellow] (PID {d.get_pid()}). "
            "Use [bold]vectrax daemon status[/bold] for details."
        )
        return
    console.print("[bold magenta]▣[/bold magenta] Starting Vectrax daemon…")
    ok = d.start(foreground=foreground)
    if ok:
        st = d.status()
        console.print(Panel(
            f"[bold green]✓ Daemon started[/bold green]\n"
            f"PID            {st['pid']}\n"
            f"Socket         [dim]∼/.vectrax/events.sock[/dim]\n"
            f"Log            [dim]{st['log_path']}[/dim]\n"
            f"Creator        {st['creator']}",
            title="[bold magenta]▣ Vectrax Daemon[/bold magenta]",
            expand=False,
        ))
    else:
        console.print("[red]Failed to start daemon. Check log:[/red] "
                      f"[dim]{d.LOG_FILE}[/dim]")


@daemon.command(name="stop")
def daemon_stop():
    """Stop the background observer daemon."""
    from vectrax import daemon as d
    if not d.is_running():
        console.print("[dim]Daemon is not running.[/dim]")
        return
    pid = d.get_pid()
    ok = d.stop()
    if ok:
        console.print(f"[bold green]✓[/bold green] Daemon stopped (was PID {pid}).")
    else:
        console.print("[red]Could not stop daemon.[/red]")


@daemon.command(name="status")
def daemon_status():
    """Show daemon status."""
    from vectrax import daemon as d
    st = d.status()
    running_str = "[bold green]● RUNNING[/bold green]" if st["running"] else "[red]● STOPPED[/red]"
    socket_str  = "[green]alive[/green]" if st["socket_alive"] else "[dim]n/a[/dim]"
    started = (
        datetime.fromtimestamp(st["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
        if st["started_at"] else "never"
    )
    last_wake = (
        datetime.fromtimestamp(st["last_activation"]).strftime("%Y-%m-%d %H:%M:%S")
        if st["last_activation"] else "never"
    )
    console.print(Panel(
        f"Status          {running_str}\n"
        f"PID             {st['pid'] or '—'}\n"
        f"IPC socket      {socket_str}\n"
        f"Creator         {st['creator']}\n"
        f"Observer active {'[green]yes[/green]' if st['observer_active'] else '[dim]no[/dim]'}\n"
        f"Wake word       {'[green]configured[/green]' if st['wake_word_configured'] else '[red]not set[/red]'}\n"
        f"Total events    {st['total_events']}\n"
        f"Started at      [dim]{started}[/dim]\n"
        f"Last wake       [dim]{last_wake}[/dim]\n"
        f"Log             [dim]{st['log_path']}[/dim]",
        title="[bold magenta]▣ Vectrax Daemon — Status[/bold magenta]",
        expand=False,
    ))


@daemon.command(name="restart")
def daemon_restart():
    """Restart the background observer daemon."""
    from vectrax import daemon as d
    console.print("[bold magenta]▣[/bold magenta] Restarting daemon…")
    ok = d.restart()
    if ok:
        console.print(f"[bold green]✓[/bold green] Daemon restarted (PID {d.get_pid()}).")
    else:
        console.print("[red]Failed to restart daemon.[/red]")


@daemon.command(name="logs")
@click.option("--tail", default=50, show_default=True, help="Number of lines to show.")
def daemon_logs(tail: int):
    """Show the daemon log."""
    from vectrax.daemon import LOG_FILE
    from pathlib import Path
    log = Path(LOG_FILE)
    if not log.exists():
        console.print("[dim]No log file yet. Start the daemon first.[/dim]")
        return
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    shown = lines[-tail:] if len(lines) > tail else lines
    for line in shown:
        if "WAKE" in line or "DESPIERTO" in line:
            console.print(f"[bold yellow]{line}[/bold yellow]")
        elif "error" in line.lower() or "FATAL" in line:
            console.print(f"[red]{line}[/red]")
        elif "★" in line:
            console.print(f"[dim]{line}[/dim]")
        else:
            console.print(line)


# ---------------------------------------------------------------------------
# creator inject  —  send event to running daemon
# ---------------------------------------------------------------------------

@creator.command(name="inject")
@click.argument("text")
def creator_inject(text: str):
    """Inject an event directly into the running daemon."""
    from vectrax.collectors.socket_srv import send_event, ping_daemon
    from vectrax import daemon as d

    if not d.is_running():
        console.print(
            "[yellow]Daemon not running.[/yellow] "
            "Ingesting directly into nucleus instead."
        )
        from vectrax.engine import ingest as _ingest
        try:
            star = _ingest(text, channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
            console.print(
                f"[bold yellow]◈ NUCLEUS ★ Star [{star.id[:8]}] — "
                f"gravity={star.gravity_score:.4f}[/bold yellow]"
            )
        except ChannelViolation as e:
            console.print(f"[red]{e}[/red]")
        return

    ok = send_event(text)
    if ok:
        console.print(
            f"[bold green]✓[/bold green] Event injected into daemon — "
            f"[dim]{text[:60]}[/dim]"
        )
    else:
        console.print("[red]Daemon did not respond. Is it running?[/red]")


# ===========================================================================
# COGNITION — Cognitive Layer management (4 motors)
# ===========================================================================

@cli.group()
def cognition():
    """[bold magenta]◆ Cognition[/bold magenta] — cognitive layer (4 motors: perception, reasoning, memory, orchestrator)."""


@cognition.command(name="status")
def cognition_status():
    """Show status of all four cognitive motors."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    st = orch.status()

    mode_color = "green" if "supervised" in st["mode"] else "yellow"
    perception = st.get("perception", {})
    memory = st.get("memory", {})
    reasoning = st.get("reasoning", {})
    graph = memory.get("graph", {})

    console.print(Panel(
        f"[bold]Mode:[/bold]          [{mode_color}]{st['mode']}[/{mode_color}]\n"
        f"[bold]Pending proposals:[/bold] {st.get('proposals_pending', 0)}\n\n"
        f"[bold magenta]Motor 1 — Perception[/bold magenta]\n"
        f"  Buffer: {perception.get('buffer_size', 0)}/{perception.get('buffer_max', 0)}\n\n"
        f"[bold magenta]Motor 2 — Reasoning[/bold magenta]\n"
        f"  Components: {len(reasoning.get('components', []))}\n"
        f"  Ready: {'[green]✓[/green]' if reasoning.get('ready') else '[red]✗[/red]'}\n\n"
        f"[bold magenta]Motor 3 — Memory[/bold magenta]\n"
        f"  Decisions: {memory.get('decisions', 0)}\n"
        f"  Patterns:  {memory.get('patterns', 0)}\n"
        f"  Signals:   {memory.get('signals', 0)}\n"
        f"  Errors:    {memory.get('errors', 0)}\n"
        f"  Principles: {memory.get('principles', 0)}\n"
        f"  Graph:     {graph.get('nodes', 0)} nodes, {graph.get('edges', 0)} edges\n\n"
        f"[bold magenta]Motor 4 — Orchestrator[/bold magenta]\n"
        f"  Loop: ready",
        title="[bold magenta]◆ Vectrax Cognitive Layer[/bold magenta]",
        expand=False,
    ))


@cognition.command(name="perceive")
def cognition_perceive():
    """Execute one perception cycle and show results."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    with console.status("[dim]Perceiving…"):
        ctx = orch.perception.perceive()

    console.print(Panel(
        f"Signals:    [bold]{ctx.signal_count}[/bold]\n"
        f"Dominant:   {ctx.dominant_category.value if ctx.dominant_category else 'none'}\n"
        f"Avg priority: {ctx.avg_priority:.4f}\n"
        f"Governor:   {ctx.governor_mode}\n"
        f"Health:     {ctx.system_health}\n"
        f"Changes:    {ctx.changes_detected}",
        title="[bold magenta]◆ Perception Cycle[/bold magenta]",
        expand=False,
    ))

    if ctx.signals:
        table = Table(box=box.SIMPLE_HEAVY)
        table.add_column("Source", width=18)
        table.add_column("Category", width=10)
        table.add_column("Priority", width=10, justify="right")
        table.add_column("Intent", width=12)
        table.add_column("Summary", width=40)
        for s in ctx.signals[:20]:
            cat_color = {"CRITICAL": "red", "ANOMALY": "yellow", "CHANGE": "cyan", "PATTERN": "green"}.get(s.category.value, "dim")
            table.add_row(
                s.source[:18],
                f"[{cat_color}]{s.category.value}[/{cat_color}]",
                f"{s.priority:.4f}",
                s.intent[:12],
                s.summary[:40],
            )
        console.print(table)


@cognition.command(name="events")
@click.option("--tail", default=20, show_default=True)
def cognition_events(tail: int):
    """Show recent events from the observation buffer."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    events = orch.perception.recent_events(tail)
    if not events:
        console.print("[dim]No events in buffer yet.[/dim]")
        return

    table = Table(title="[bold magenta]◆ Recent Events[/bold magenta]", box=box.SIMPLE_HEAVY)
    table.add_column("Source", width=18)
    table.add_column("Cat", width=10)
    table.add_column("Prio", width=8, justify="right")
    table.add_column("Intent", width=12)
    table.add_column("Summary")
    for s in events:
        table.add_row(s.source[:18], s.category.value, f"{s.priority:.3f}", s.intent[:12], s.summary[:50])
    console.print(table)


@cognition.group(name="memory")
def cognition_memory():
    """Query structural memory."""


@cognition_memory.command(name="query")
@click.argument("term")
def cognition_memory_query(term: str):
    """Search across all memory stores for <term>."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    mem = orch.memory.query(intent=term)

    console.print(f"[bold magenta]◆ Memory Query:[/bold magenta] '{term}' (tier: {mem.tier.value})")
    if mem.related_decisions:
        console.print(f"\n[bold]Decisions ({len(mem.related_decisions)}):[/bold]")
        for d in mem.related_decisions[:5]:
            console.print(f"  • [{d.get('status', '?')}] {d.get('description', '')[:60]}")
    if mem.related_patterns:
        console.print(f"\n[bold]Patterns ({len(mem.related_patterns)}):[/bold]")
        for p in mem.related_patterns[:5]:
            console.print(f"  • [{p.get('tier', '?')}] {p.get('summary', p.get('intent', ''))[:60]}")
    if mem.related_errors:
        console.print(f"\n[bold]Errors ({len(mem.related_errors)}):[/bold]")
        for e in mem.related_errors[:5]:
            console.print(f"  • [{e.get('error_type', '?')}] {e.get('description', '')[:60]}")
    if mem.active_principles:
        console.print(f"\n[bold]Principles ({len(mem.active_principles)}):[/bold]")
        for p in mem.active_principles[:5]:
            console.print(f"  • [{p.get('type', '?')}] {p.get('name', '')}")
    if not any([mem.related_decisions, mem.related_patterns, mem.related_errors]):
        console.print("[dim]No matching memories found.[/dim]")


@cognition.command(name="reason")
def cognition_reason():
    """Launch a strategic evaluation based on current context."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    with console.status("[dim]Perceiving + reasoning…"):
        ctx = orch.perception.perceive()
        mem = orch.memory.query(context=ctx)
        result = orch.reasoning.reason(ctx, mem)

    rec_color = {"proceed": "green", "review": "yellow", "block": "red"}.get(result.recommendation, "white")
    console.print(Panel(
        f"Risk:           [{rec_color}]{result.risk_level}[/{rec_color}] ({result.risk_score:.4f})\n"
        f"Impact:         {result.impact.overall:.4f}" + (f" (S={result.impact.short_term:.2f} M={result.impact.medium_term:.2f} L={result.impact.long_term:.2f})" if result.impact else "") + "\n"
        f"Scenarios:      {len(result.scenarios)}\n"
        f"Contradictions: {len(result.contradictions)}\n"
        f"Constitution:   {'[green]PASS[/green]' if result.constitutional_pass else '[red]FAIL[/red]'}\n"
        f"Recommendation: [{rec_color}]{result.recommendation.upper()}[/{rec_color}]",
        title="[bold magenta]◆ Strategic Reasoning[/bold magenta]",
        expand=False,
    ))

    if result.scenarios:
        for i, s in enumerate(result.scenarios, 1):
            console.print(f"  {i}. {s.description} (p={s.probability:.2f}, risk={s.risk:.2f})")


@cognition.command(name="proposals")
@click.option("--all", "show_all", is_flag=True, help="Show all statuses.")
def cognition_proposals(show_all: bool):
    """View cognitive proposals."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    status_filter = None if show_all else "pending"
    proposals = orch.list_proposals(status=status_filter)

    if not proposals:
        console.print("[dim]No cognitive proposals found.[/dim]")
        return

    table = Table(title="[bold magenta]◆ Cognitive Proposals[/bold magenta]", box=box.SIMPLE_HEAVY)
    table.add_column("ID", width=16)
    table.add_column("Status", width=12)
    table.add_column("Risk", width=10)
    table.add_column("Rec", width=10)
    table.add_column("Description", width=40)
    for p in proposals[:20]:
        sc = {"pending": "yellow", "approved": "green", "rejected": "red", "executed": "cyan"}.get(p.status.value, "dim")
        risk = f"{p.reasoning.risk_score:.3f}" if p.reasoning else "—"
        rec = p.reasoning.recommendation if p.reasoning else "—"
        table.add_row(p.id, f"[{sc}]{p.status.value}[/{sc}]", risk, rec, p.description[:40])
    console.print(table)


@cognition.command(name="approve")
@click.argument("proposal_id")
def cognition_approve(proposal_id: str):
    """Approve a cognitive proposal (creator only)."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    result = orch.approve(proposal_id)
    if result:
        console.print(f"[bold green]✓[/bold green] Cognitive proposal [cyan]{proposal_id}[/cyan] approved.")
    else:
        console.print(f"[red]Proposal '{proposal_id}' not found.[/red]")


@cognition.command(name="reject")
@click.argument("proposal_id")
def cognition_reject(proposal_id: str):
    """Reject a cognitive proposal."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    result = orch.reject(proposal_id)
    if result:
        console.print(f"[bold red]✗[/bold red] Cognitive proposal [cyan]{proposal_id}[/cyan] rejected.")
    else:
        console.print(f"[red]Proposal '{proposal_id}' not found.[/red]")


@cognition.command(name="mode")
@click.argument("new_mode", required=False)
def cognition_mode(new_mode: str):
    """View or change cognitive mode (silent / supervised)."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    if new_mode:
        try:
            result = orch.set_mode(new_mode)
            console.print(f"[bold green]✓[/bold green] Cognitive mode set to: [bold]{result}[/bold]")
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
    else:
        console.print(f"Current cognitive mode: [bold]{orch.get_mode()}[/bold]")


@cognition.command(name="dry-run")
def cognition_dry_run():
    """Execute a complete cognitive cycle in dry-run mode (no side effects)."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    with console.status("[dim]Running cognitive cycle (dry-run)…"):
        result = orch.run_cycle(dry_run=True)

    console.print(Panel(
        f"Cycle ID:   {result.get('cycle_id', '?')}\n"
        f"Mode:       {result.get('mode', '?')}\n"
        f"Outcome:    {result.get('outcome', '?')}\n"
        f"Duration:   {result.get('duration_ms', 0):.1f}ms\n"
        f"Dry-run:    [green]yes[/green]",
        title="[bold magenta]◆ Cognitive Dry-Run[/bold magenta]",
        expand=False,
    ))

    if result.get("proposal"):
        p = result["proposal"]
        console.print(f"\n[bold]Generated proposal:[/bold] {p.get('id', '?')}")
        console.print(f"  Description: {p.get('description', '')[:60]}")
        r = p.get("reasoning", {})
        if r:
            console.print(f"  Risk: {r.get('risk_level', '?')} ({r.get('risk_score', 0):.3f})")
            console.print(f"  Recommendation: {r.get('recommendation', '?')}")


@cognition.command(name="trace")
@click.option("--tail", default=20, show_default=True)
def cognition_trace(tail: int):
    """View recent cognitive loop traces."""
    from cognition.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    traces = orch.tracer.recent(tail)
    if not traces:
        console.print("[dim]No traces yet.[/dim]")
        return

    table = Table(title="[bold magenta]◆ Cognitive Traces[/bold magenta]", box=box.SIMPLE_HEAVY)
    table.add_column("Cycle", width=14)
    table.add_column("Step", width=20)
    table.add_column("Duration", width=10, justify="right")
    table.add_column("OK", width=4)
    table.add_column("Output")
    for t in traces:
        ok = "[green]✓[/green]" if t.get("success", True) else "[red]✗[/red]"
        table.add_row(
            t.get("cycle_id", "")[:14],
            t.get("step", ""),
            f"{t.get('duration_ms', 0):.1f}ms",
            ok,
            t.get("output_summary", "")[:40],
        )
    console.print(table)


# ===========================================================================
# CONNECT — Universal Connection Engine management
# ===========================================================================

@cli.group()
def connect():
    """[bold cyan]◎ UCE[/bold cyan] — Universal Connection Engine management."""


@connect.command(name="list")
def connect_list():
    """List all registered connectors with status."""
    from connectors.engine import get_connection_engine
    from connectors.adapters import rest_api, local_filesystem, shell, sqlite_db
    from connectors.adapters import webhook, google_workspace, icloud

    engine = get_connection_engine()
    # Auto-register adapters if empty
    if not engine.list_connectors():
        _auto_register_adapters(engine)

    table = Table(title="[bold cyan]◎ UCE — Registered Connectors[/bold cyan]", box=box.SIMPLE_HEAVY)
    table.add_column("Name", width=20)
    table.add_column("Family", width=14)
    table.add_column("Version", width=8)
    table.add_column("Status", width=14)
    table.add_column("Healthy", width=8)
    table.add_column("Approved", width=9)

    status_data = engine.status()
    for name, info in status_data["connectors"].items():
        healthy_str = "[green]✓[/green]" if info["healthy"] else "[red]✗[/red]"
        approved_str = "[green]✓[/green]" if info["approved"] else "[yellow]pending[/yellow]"
        status_color = {"idle": "dim", "connected": "green", "error": "red", "degraded": "yellow"}.get(info["status"], "white")
        table.add_row(
            name,
            info["family"],
            info["version"],
            f"[{status_color}]{info['status']}[/{status_color}]",
            healthy_str,
            approved_str,
        )
    console.print(table)
    console.print(f"\n  Total: [bold]{status_data['total_connectors']}[/bold] connectors")


@connect.command(name="status")
@click.argument("name")
def connect_status(name: str):
    """Show detailed status of a connector."""
    from connectors.engine import get_connection_engine

    engine = get_connection_engine()
    if not engine.list_connectors():
        _auto_register_adapters(engine)

    connector = engine.get_connector(name)
    contract = engine.get_contract(name)
    if not connector or not contract:
        console.print(f"[red]Connector '{name}' not found.[/red]")
        return

    info = connector.info()
    stats = engine._tracker.get_stats(name)
    console.print(Panel(
        f"Name:         [bold]{info['name']}[/bold]\n"
        f"Version:      {info['version']}\n"
        f"Family:       {contract.family.value}\n"
        f"Status:       {info['status']}\n"
        f"Healthy:      {'[green]✓[/green]' if info['healthy'] else '[red]✗[/red]'}\n"
        f"Capabilities: {', '.join(info['capabilities'])}\n"
        f"Permissions:  {', '.join(info['permissions'])}\n"
        f"Auth:         {contract.auth_method.value}\n"
        f"Approved:     {engine._gate.is_approved(name)}\n"
        f"Risks:        {', '.join(contract.risks) or 'none'}\n\n"
        f"[bold]Stats:[/bold]\n"
        f"  Total ops:    {stats.get('total', 0)}\n"
        f"  Success rate: {stats.get('success_rate', 0):.1%}\n"
        f"  Avg latency:  {stats.get('avg_latency_ms', 0):.1f}ms",
        title=f"[bold cyan]◎ Connector: {name}[/bold cyan]",
        expand=False,
    ))


@connect.command(name="test")
@click.argument("name")
def connect_test(name: str):
    """Run self-test on a connector."""
    from connectors.engine import get_connection_engine
    from connectors.core.errors import UCEError

    engine = get_connection_engine()
    if not engine.list_connectors():
        _auto_register_adapters(engine)

    connector = engine.get_connector(name)
    if not connector:
        console.print(f"[red]Connector '{name}' not found.[/red]")
        return

    # Load credentials from vault and authenticate before testing
    try:
        engine.connect(name)
    except UCEError as exc:
        console.print(f"[yellow]⚠ Auth skipped:[/yellow] {exc}")
    except Exception as exc:
        console.print(f"[yellow]⚠ Auth skipped:[/yellow] {exc}")

    with console.status(f"Testing '{name}'…"):
        result = connector.test()
    if result["passed"]:
        console.print(f"[bold green]✓[/bold green] {result['details']}")
    else:
        console.print(f"[bold red]✗[/bold red] {result['details']}")


@connect.command(name="propose")
@click.argument("name")
def connect_propose(name: str):
    """Generate a connection proposal for creator approval."""
    from connectors.engine import get_connection_engine

    engine = get_connection_engine()
    if not engine.list_connectors():
        _auto_register_adapters(engine)

    try:
        proposal = engine.propose_connection(name)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        return

    console.print(Panel(
        f"Connector:    [bold]{proposal['connector_name']}[/bold]\n"
        f"Version:      {proposal['connector_version']}\n"
        f"Family:       {proposal['family']}\n"
        f"Capabilities: {', '.join(proposal['capabilities'])}\n"
        f"Permissions:  {', '.join(proposal['permissions_required'])}\n"
        f"Auth:         {proposal['auth_method']}\n"
        f"Risk Level:   {proposal['risk_level']}\n"
        f"Risks:        {chr(10).join(proposal['risks']) if proposal['risks'] else 'none'}",
        title=f"[bold yellow]◈ Connection Proposal: {proposal['id']}[/bold yellow]",
        expand=False,
    ))
    console.print("[dim]Use 'vectrax connect approve <id>' to approve.[/dim]")


@connect.command(name="approve")
@click.argument("proposal_id")
def connect_approve(proposal_id: str):
    """Approve a connection proposal (creator only)."""
    from connectors.engine import get_connection_engine

    engine = get_connection_engine()
    result = engine.approve(proposal_id)
    if result:
        console.print(
            f"[bold green]✓[/bold green] Proposal [cyan]{proposal_id}[/cyan] approved — "
            f"connector '{result['connector_name']}' is now active."
        )
    else:
        console.print(f"[red]Proposal '{proposal_id}' not found.[/red]")


@connect.command(name="reject")
@click.argument("proposal_id")
def connect_reject(proposal_id: str):
    """Reject a connection proposal."""
    from connectors.engine import get_connection_engine

    engine = get_connection_engine()
    result = engine.reject(proposal_id)
    if result:
        console.print(f"[bold red]✗[/bold red] Proposal [cyan]{proposal_id}[/cyan] rejected.")
    else:
        console.print(f"[red]Proposal '{proposal_id}' not found.[/red]")


@connect.command(name="logs")
@click.option("--name", default=None, help="Filter by connector name.")
@click.option("--tail", default=20, show_default=True)
def connect_logs(name: str, tail: int):
    """Show connection operation logs."""
    from connectors.engine import get_connection_engine

    engine = get_connection_engine()
    entries = engine.logs(name=name, limit=tail)
    if not entries:
        console.print("[dim]No connection logs yet.[/dim]")
        return

    table = Table(title="[bold cyan]◎ Connection Logs[/bold cyan]", box=box.SIMPLE_HEAVY)
    table.add_column("Time", width=19)
    table.add_column("Connector", width=18)
    table.add_column("Op", width=10)
    table.add_column("Status", width=10)
    table.add_column("Latency", width=10, justify="right")
    table.add_column("Error", width=30)

    for e in entries:
        ts = datetime.fromtimestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        sc = "green" if e["status"] == "success" else "red"
        table.add_row(
            ts, e["connector_name"], e["operation"],
            f"[{sc}]{e['status']}[/{sc}]",
            f"{e['latency_ms']:.0f}ms",
            (e.get("error_detail") or "")[:30],
        )
    console.print(table)


@connect.command(name="health")
def connect_health():
    """Healthcheck all registered connectors."""
    from connectors.engine import get_connection_engine

    engine = get_connection_engine()
    if not engine.list_connectors():
        _auto_register_adapters(engine)

    all_ok = True
    for name in engine.list_connectors():
        connector = engine.get_connector(name)
        ok = connector.healthcheck() if connector else False
        if not ok:
            all_ok = False
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {icon} {name}")

    if all_ok:
        console.print("\n[bold green]All connectors healthy.[/bold green]")
    else:
        console.print("\n[yellow]Some connectors are unhealthy.[/yellow]")


# ---------------------------------------------------------------------------
# UCE helper: auto-register adapters
# ---------------------------------------------------------------------------

def _auto_register_adapters(engine):
    """Register all built-in UCE adapters with the ConnectionEngine."""
    from connectors.adapters.rest_api import RestApiConnector, REST_API_CONTRACT
    from connectors.adapters.local_filesystem import LocalFilesystemConnector, FILESYSTEM_CONTRACT
    from connectors.adapters.shell import ShellConnector, SHELL_CONTRACT
    from connectors.adapters.sqlite_db import SqliteConnector, SQLITE_CONTRACT
    from connectors.adapters.webhook import WebhookConnector, WEBHOOK_CONTRACT
    from connectors.adapters.google_workspace import GoogleWorkspaceConnector, GOOGLE_CONTRACT
    from connectors.adapters.icloud import ICloudConnector, ICLOUD_CONTRACT

    adapters = [
        (RestApiConnector(), REST_API_CONTRACT),
        (LocalFilesystemConnector(), FILESYSTEM_CONTRACT),
        (ShellConnector(), SHELL_CONTRACT),
        (SqliteConnector(), SQLITE_CONTRACT),
        (WebhookConnector(), WEBHOOK_CONTRACT),
        (GoogleWorkspaceConnector(), GOOGLE_CONTRACT),
        (ICloudConnector(), ICLOUD_CONTRACT),
    ]
    for connector, contract in adapters:
        try:
            engine.register(connector, contract)
        except Exception:
            pass


# ===========================================================================
# AUTH — Multi-user authentication
# ===========================================================================

_SESSION_PATH = os.path.join(os.path.expanduser("~/.vectrax"), "session.json")


def _load_session() -> dict | None:
    """Load the saved session token from disk."""
    import json as _json
    if os.path.isfile(_SESSION_PATH):
        try:
            with open(_SESSION_PATH, "r") as f:
                return _json.load(f)
        except Exception:
            pass
    return None


def _save_session(data: dict) -> None:
    import json as _json
    os.makedirs(os.path.dirname(_SESSION_PATH), exist_ok=True)
    with open(_SESSION_PATH, "w") as f:
        _json.dump(data, f, indent=2)
    os.chmod(_SESSION_PATH, 0o600)


def _clear_session() -> None:
    if os.path.isfile(_SESSION_PATH):
        os.remove(_SESSION_PATH)


@cli.group()
def auth():
    """[bold green]🔐 Auth[/bold green] — multi-user login/logout."""


@auth.command(name="login")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True)
def auth_login(username: str, password: str):
    """Authenticate and save session locally."""
    from services.core.users import UserStore
    from services.core.tokens import TokenManager

    store = UserStore()
    user = store.authenticate(username, password)
    if not user:
        console.print("[bold red]✗[/bold red] Invalid credentials.")
        sys.exit(1)

    tm = TokenManager()
    token = tm.issue_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        channel=user.channel,
    )
    _save_session({
        "token": token,
        "username": user.username,
        "role": user.role,
        "channel": user.channel,
    })
    console.print(
        f"[bold green]✓[/bold green] Logged in as [bold]{user.username}[/bold] "
        f"(role={user.role}, channel={user.channel})"
    )


@auth.command(name="logout")
def auth_logout():
    """Clear local session."""
    session = _load_session()
    if session:
        # Revoke server-side tokens too
        try:
            from services.core.tokens import TokenManager
            tm = TokenManager()
            tm.revoke_token(session["token"])
        except Exception:
            pass
    _clear_session()
    console.print("[bold green]✓[/bold green] Logged out.")


@auth.command(name="whoami")
def auth_whoami():
    """Show the currently logged-in user."""
    session = _load_session()
    if not session:
        console.print("[dim]Not logged in. Use 'vectrax auth login'.[/dim]")
        return
    console.print(Panel(
        f"Username:  [bold]{session.get('username', '?')}[/bold]\n"
        f"Role:      {session.get('role', '?')}\n"
        f"Channel:   {session.get('channel', '?')}",
        title="[bold green]🔐 Current Session[/bold green]",
        expand=False,
    ))


# ===========================================================================
# ADMIN — User management (owner-only)
# ===========================================================================

@cli.group()
def admin():
    """[bold red]⚙ Admin[/bold red] — user management (creator only)."""


@admin.command(name="users")
def admin_users():
    """List all registered users."""
    from services.core.users import UserStore

    store = UserStore()
    users = store.list_users(active_only=False)
    if not users:
        console.print("[dim]No users registered.[/dim]")
        return

    table = Table(title="[bold]Registered Users[/bold]", box=box.SIMPLE_HEAVY)
    table.add_column("ID", width=14, style="dim")
    table.add_column("Username", width=18)
    table.add_column("Role", width=10)
    table.add_column("Channel", width=10)
    table.add_column("Active", width=7)
    for u in users:
        active = "[green]✓[/green]" if u.is_active else "[red]✗[/red]"
        role_color = {"owner": "yellow", "operator": "cyan", "viewer": "dim"}.get(u.role, "white")
        table.add_row(
            u.id, u.username, f"[{role_color}]{u.role}[/{role_color}]",
            u.channel, active,
        )
    console.print(table)


@admin.command(name="create-user")
@click.option("--username", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--role", type=click.Choice(["operator", "viewer"]), default="viewer")
def admin_create_user(username: str, password: str, role: str):
    """Create a new user account."""
    from services.core.users import UserStore

    store = UserStore()
    try:
        user = store.create_user(username=username, password=password, role=role)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    console.print(
        f"[bold green]✓[/bold green] User [bold]{user.username}[/bold] created "
        f"(role={user.role}, channel={user.channel})"
    )


@admin.command(name="deactivate")
@click.argument("username")
def admin_deactivate(username: str):
    """Deactivate a user account (soft delete)."""
    from services.core.users import UserStore
    from services.core.tokens import TokenManager

    store = UserStore()
    user = store.get_by_username(username)
    if not user:
        console.print(f"[red]User '{username}' not found.[/red]")
        sys.exit(1)
    try:
        store.deactivate(user.id)
        # Also revoke all their tokens
        TokenManager().revoke_all_for_user(user.id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    console.print(f"[bold green]✓[/bold green] User [bold]{username}[/bold] deactivated.")


@admin.command(name="set-role")
@click.argument("username")
@click.argument("role", type=click.Choice(["operator", "viewer"]))
def admin_set_role(username: str, role: str):
    """Change a user's role."""
    from services.core.users import UserStore

    store = UserStore()
    user = store.get_by_username(username)
    if not user:
        console.print(f"[red]User '{username}' not found.[/red]")
        sys.exit(1)
    try:
        store.update_role(user.id, role)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    console.print(f"[bold green]✓[/bold green] {username} is now [bold]{role}[/bold].")


if __name__ == "__main__":
    cli()
