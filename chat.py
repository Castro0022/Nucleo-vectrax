#!/usr/bin/env python3
"""Vectrax Chat - Interfaz interactiva para comunicarse con el sistema.

Permite al creador de Vectrax interactuar con el sistema de forma conversacional.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.schemas import Signal, SignalCategory, ExecutiveProposal
from pipeline.signals import SignalCollector
from main import run_pipeline
import config

from core.policy_router import (
    PolicyRouter, RoutingInput, RoutingAction,
    derive_polarity, derive_tension,
)
from core.action_sandbox import ActionSandbox, ActionPlan, SandboxValidationError
from core.action_executor import ActionExecutor, ExecutionResult
from core.causal_ledger import query_precedent, read_last as ledger_read_last


class VectraxChat:
    """Interfaz de chat interactiva con Vectrax."""

    def __init__(self) -> None:
        self.signals: list[Signal] = []
        self.running = True
        self.signal_counter = 0
        self.last_proposal: ExecutiveProposal | None = None
        self.pending_plans: dict[str, ActionPlan] = {}
        self.pending_routing: dict[str, RoutingAction] = {}
        self.router = PolicyRouter()
        self.sandbox = ActionSandbox()
        self.executor = ActionExecutor()

    def greet(self) -> None:
        """Saluda al usuario."""
        print("\n" + "=" * 60)
        print("  VECTRAX - Sistema de Análisis Inteligente")
        print("  Chat Interactivo")
        print("=" * 60)
        print("\n  Bienvenido, creador de Vectrax.")
        print("\n  Comandos disponibles:")
        print("    /signal <categoría> <mensaje> [peso]")
        print("      → Agregar una señal (categoría: econ, social, legal, tech, ambi)")
        print("    /run <título propuesta>")
        print("      → Ejecutar el pipeline con las señales actuales")
        print("    /clear")
        print("      → Limpiar todas las señales")
        print("    /signals")
        print("      → Ver señales actuales")
        print("    /config")
        print("      → Ver configuración del sistema")
        print("    /action <tipo> <descripción> [archivos...]")
        print("      → Planificar acción autónoma (tipos: create_module, add_tests, refactor_small, docs)")
        print("    /execute <plan_id>")
        print("      → Ejecutar plan aprobado")
        print("    /ledger [n]")
        print("      → Ver últimas N entradas del Causal Ledger")
        print("    /help")
        print("      → Mostrar esta ayuda")
        print("    /exit")
        print("      → Salir del chat\n")

    def category_from_string(self, cat_str: str) -> SignalCategory | None:
        """Convierte string a SignalCategory."""
        mapping = {
            "econ": SignalCategory.ECONOMICA,
            "economica": SignalCategory.ECONOMICA,
            "social": SignalCategory.SOCIAL,
            "legal": SignalCategory.LEGAL,
            "tech": SignalCategory.TECNICA,
            "tecnica": SignalCategory.TECNICA,
            "ambi": SignalCategory.AMBIENTAL,
            "ambiental": SignalCategory.AMBIENTAL,
        }
        return mapping.get(cat_str.lower())

    def add_signal(self, parts: list[str]) -> None:
        """Agrega una nueva señal."""
        if len(parts) < 3:
            print("  ✗ Formato: /signal <categoría> <mensaje> [peso]")
            return

        category_str = parts[1]
        message = " ".join(parts[2:])

        # Extraer peso si está presente (último token numérico)
        weight = 1.0
        if message:
            last_part = message.split()[-1]
            try:
                weight = float(last_part)
                message = " ".join(message.split()[:-1])
            except ValueError:
                pass

        category = self.category_from_string(category_str)
        if not category:
            print(f"  ✗ Categoría no reconocida: {category_str}")
            print("    Use: econ, social, legal, tech, ambi")
            return

        self.signal_counter += 1
        signal = Signal(
            user_id=f"creador-{self.signal_counter}",
            text=message,
            category=category,
            weight=min(max(weight, 0.1), 10.0),
        )
        self.signals.append(signal)
        print(f"  ✓ Señal agregada: [{category.value}] {message} (peso: {weight})")

    def show_signals(self) -> None:
        """Muestra las señales actuales."""
        if not self.signals:
            print("  → Sin señales aún. Usa /signal para agregar.")
            return

        print(f"\n  Señales actuales ({len(self.signals)}):")
        for i, s in enumerate(self.signals, 1):
            print(f"    {i}. [{s.category.value:10s}] {s.text} (peso: {s.weight})")

    def show_config(self) -> None:
        """Muestra la configuración actual."""
        print("\n  Configuración del Sistema:")
        print(f"    Umbral de convergencia: {config.CONVERGENCE_THRESHOLD}")
        print(f"    Min. señales convergencia: {config.MIN_SIGNALS_FOR_CONVERGENCE}")
        print(f"    Score min. estructural: {config.MIN_STRUCTURAL_SCORE}")
        print(f"    Riesgo máximo aceptable: {config.MAX_ACCEPTABLE_RISK}")
        print(f"    Score min. sostenibilidad: {config.MIN_SUSTAINABILITY_SCORE}")
        print(f"    Umbral aprobación automática: {config.AUTO_APPROVE_THRESHOLD}")
        print(f"    Umbral rechazo automático: {config.AUTO_REJECT_THRESHOLD}")

    def run_analysis(self, parts: list[str]) -> None:
        """Ejecuta el análisis del pipeline."""
        if not self.signals:
            print("  ✗ No hay señales. Agrega algunas con /signal primero.")
            return

        if len(parts) < 2:
            title = "Propuesta Interactiva"
        else:
            title = " ".join(parts[1:])

        print(f"\n  Ejecutando análisis para: {title}")
        proposal = run_pipeline(title, self.signals)
        self.last_proposal = proposal
        self._show_quick_summary(proposal)

    def _show_quick_summary(self, proposal: ExecutiveProposal) -> None:
        """Muestra un resumen rápido de la propuesta."""
        print("\n" + "─" * 60)
        print("  RESUMEN RÁPIDO")
        print("─" * 60)
        status_emoji = "✓" if proposal.status.value == "aprobada" else (
            "✗" if proposal.status.value == "rechazada" else "◐"
        )
        print(f"  {status_emoji} Estado: {proposal.status.value.upper()}")
        print(f"  ID Propuesta: {proposal.id}")

    def clear_signals(self) -> None:
        """Limpia todas las señales."""
        self.signals.clear()
        self.signal_counter = 0
        print("  ✓ Señales limpiadas.")

    def show_help(self) -> None:
        """Muestra la ayuda."""
        self.greet()

    def process_command(self, line: str) -> None:
        """Procesa un comando del usuario."""
        line = line.strip()
        if not line:
            return

        if not line.startswith("/"):
            print("  ℹ Usa / para comandos. Ejemplo: /help")
            return

        parts = line.split()
        command = parts[0].lower()

        if command == "/signal":
            self.add_signal(parts)
        elif command == "/run":
            self.run_analysis(parts)
        elif command == "/signals":
            self.show_signals()
        elif command == "/config":
            self.show_config()
        elif command == "/clear":
            self.clear_signals()
        elif command == "/action":
            self.plan_action(parts)
        elif command == "/execute":
            self.execute_plan(parts)
        elif command == "/ledger":
            self.show_ledger(parts)
        elif command == "/help":
            self.show_help()
        elif command == "/exit":
            self.running = False
            print("\n  Hasta luego, creador. Vectrax se mantiene en vigilancia.\n")
        else:
            print(f"  ✗ Comando desconocido: {command}")
            print("    Usa /help para ver disponibles.")

    def run(self) -> None:
        """Inicia el chat interactivo."""
        self.greet()

        while self.running:
            try:
                user_input = input("\n  → ")
                self.process_command(user_input)
            except KeyboardInterrupt:
                print("\n\n  Interrupción recibida. Finalizando...")
                break
            except Exception as e:
                print(f"  ✗ Error: {e}")


    # -------------------------------------------------------------------
    # Autonomous action commands
    # -------------------------------------------------------------------

    def plan_action(self, parts: list[str]) -> None:
        """Planifica una acción autónoma: /action <tipo> <descripción> [archivos...]"""
        if len(parts) < 3:
            print("  ✗ Formato: /action <tipo> <descripción> [archivos...]")
            print("    Tipos: create_module, add_tests, refactor_small, docs")
            return

        action_type = parts[1]
        rest = parts[2:]

        # Separate description from file paths (files start with path-like chars)
        description_parts = []
        file_paths = []
        for token in rest:
            if "/" in token or token.endswith(".py"):
                file_paths.append(token)
            else:
                description_parts.append(token)

        description = " ".join(description_parts) if description_parts else action_type

        # Derive routing signals from last proposal (if available)
        if self.last_proposal:
            p = self.last_proposal
            polarity = derive_polarity(
                p.convergence.converged,
                p.convergence.score,
                p.simulation.expected_impact,
            )
            dim_scores = [d.score for d in p.structural_analysis.dimensions]
            tension = derive_tension(dim_scores)
            risk_score = p.risk.risk_score
            global_score = p.structural_analysis.overall_score
        else:
            polarity = derive_polarity(False, 0.0, 0.0)
            tension = 1.0
            risk_score = 1.0
            global_score = 0.0

        precedent = query_precedent(action_type)

        # Build routing input
        ri = RoutingInput(
            global_score=global_score,
            risk_score=risk_score,
            polarity_state=polarity.value,
            tension=tension,
            precedent_confidence=precedent,
            action_type=action_type,
            affected_paths=file_paths,
        )

        # Route
        decision = self.router.evaluate(ri)
        print(f"\n  ┌─ PolicyRouter ─────────────────────────")
        print(f"  │ Decisión:    {decision.action.value}")
        print(f"  │ Razón:       {decision.reason}")
        print(f"  │ Polaridad:   {ri.polarity_state}")
        print(f"  │ Tensión:     {ri.tension:.4f}")
        print(f"  │ Riesgo:      {ri.risk_score:.4f}")
        print(f"  │ Precedente:  {ri.precedent_confidence:.4f}")
        print(f"  └──────────────────────────────────────────")

        if decision.action == RoutingAction.BLOCKED:
            print("  ✗ Acción BLOQUEADA. No se puede planificar.")
            return

        # Sandbox: create plan
        try:
            files_create = [f for f in file_paths if not self._file_exists(f)]
            files_modify = [f for f in file_paths if self._file_exists(f)]

            plan = self.sandbox.plan(
                action_type=action_type,
                description=description,
                files_to_create=files_create,
                files_to_modify=files_modify,
                commands_to_run=["python -m pytest tests/ -q"] if file_paths else [],
                routing_context=decision.to_dict(),
            )
        except SandboxValidationError as e:
            print(f"  ✗ Sandbox rechazó el plan: {e}")
            return

        # Store plan
        self.pending_plans[plan.plan_id] = plan
        self.pending_routing[plan.plan_id] = decision.action

        print(f"\n  ┌─ ActionPlan ──────────────────────────────")
        print(f"  │ Plan ID:     {plan.plan_id}")
        print(f"  │ Tipo:        {plan.action_type}")
        print(f"  │ Crear:       {plan.files_to_create or '(ninguno)'}")
        print(f"  │ Modificar:   {plan.files_to_modify or '(ninguno)'}")
        print(f"  │ Comandos:    {plan.commands_to_run or '(ninguno)'}")
        print(f"  │ Rollback:    {plan.rollback_strategy}")
        print(f"  └──────────────────────────────────────────")

        if decision.action == RoutingAction.AUTO_EXECUTE:
            print(f"  ✓ Plan aprobado para auto-ejecución. Usa /execute {plan.plan_id}")
        else:
            print(f"  ◐ Plan requiere revisión. Revisa y usa /execute {plan.plan_id} si apruebas.")

    def execute_plan(self, parts: list[str]) -> None:
        """Ejecuta un plan previamente creado: /execute <plan_id>"""
        if len(parts) < 2:
            print("  ✗ Formato: /execute <plan_id>")
            if self.pending_plans:
                print("    Planes pendientes:")
                for pid in self.pending_plans:
                    print(f"      - {pid}")
            return

        plan_id = parts[1]
        plan = self.pending_plans.get(plan_id)
        if not plan:
            print(f"  ✗ Plan no encontrado: {plan_id}")
            return

        routing_action = self.pending_routing.get(plan_id, RoutingAction.REQUIERE_REVISION)

        if routing_action != RoutingAction.AUTO_EXECUTE:
            print(f"  ⚠ Plan {plan_id} tiene routing={routing_action.value}")
            print("    Solo planes con AUTO_EXECUTE pueden ejecutarse automáticamente.")
            return

        print(f"\n  Ejecutando plan {plan_id}...")
        result = self.executor.execute(plan, routing_action)

        if result.success:
            print(f"  ✓ Plan {plan_id} ejecutado exitosamente.")
            if result.test_output:
                print(f"    Tests: {result.test_output[:200]}")
        elif result.rolled_back:
            print(f"  ✗ Plan {plan_id} falló — se ejecutó rollback.")
            if result.test_output:
                print(f"    Tests: {result.test_output[:200]}")
        else:
            print(f"  ✗ Plan {plan_id} falló: {result.error[:200]}")

        # Clean up
        self.pending_plans.pop(plan_id, None)
        self.pending_routing.pop(plan_id, None)

    def show_ledger(self, parts: list[str]) -> None:
        """Muestra las últimas entradas del Causal Ledger: /ledger [n]"""
        n = 10
        if len(parts) >= 2:
            try:
                n = int(parts[1])
            except ValueError:
                pass

        entries = ledger_read_last(n)
        if not entries:
            print("  → Causal Ledger vacío.")
            return

        print(f"\n  Causal Ledger (últimas {len(entries)} entradas):")
        print("  " + "─" * 56)
        for e in entries:
            ts = e.get("timestamp", "?")[:19]
            et = e.get("event_type", "?")
            pid = e.get("plan_id", "?")
            at = e.get("action_type", "?")
            print(f"    {ts}  {et:20s}  {pid}  [{at}]")
        print("  " + "─" * 56)

    @staticmethod
    def _file_exists(path: str) -> bool:
        """Check if a file exists (relative to project root or absolute)."""
        import os
        base = os.path.expanduser("~/Vectrax")
        abs_path = path if os.path.isabs(path) else os.path.join(base, path)
        return os.path.exists(abs_path)


if __name__ == "__main__":
    chat = VectraxChat()
    chat.run()
