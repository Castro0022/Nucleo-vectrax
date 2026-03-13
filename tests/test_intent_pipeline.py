"""
Tests para la arquitectura de ejecución por intención.

Cubre los 4 módulos:
  1. IntentEngine       — parse de intención
  2. ExecutionPlanner   — generación de plan
  3. AutoBuilder        — build dry_run / live
  4. IntentPipeline     — pipeline end-to-end
"""

import os
import tempfile
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# 1. IntentEngine
# ═══════════════════════════════════════════════════════════════════════════

from core.intent_engine import (
    IntentEngine,
    IntentPackage,
    ActionIntent,
    IntentScope,
)


class TestIntentEngine:
    """Tests para el motor de intenciones."""

    def setup_method(self):
        self.engine = IntentEngine()

    # -- Detección de acción --

    def test_create_intent_spanish(self):
        pkg = self.engine.parse("crea un módulo de autenticación con JWT")
        assert pkg.action == ActionIntent.CREATE
        assert pkg.confidence > 0

    def test_create_intent_english(self):
        pkg = self.engine.parse("create a new authentication module")
        assert pkg.action == ActionIntent.CREATE

    def test_modify_intent(self):
        pkg = self.engine.parse("modifica el router para soportar websockets")
        assert pkg.action == ActionIntent.MODIFY

    def test_connect_intent(self):
        pkg = self.engine.parse("conecta el módulo de auth con la API externa")
        assert pkg.action == ActionIntent.CONNECT

    def test_analyze_intent(self):
        pkg = self.engine.parse("analiza el rendimiento del sistema")
        assert pkg.action == ActionIntent.ANALYZE

    def test_deploy_intent(self):
        pkg = self.engine.parse("despliega el servicio en producción")
        assert pkg.action == ActionIntent.DEPLOY

    def test_configure_intent(self):
        pkg = self.engine.parse("configura los parámetros de rate limiting")
        assert pkg.action == ActionIntent.CONFIGURE

    def test_query_fallback(self):
        pkg = self.engine.parse("qué es Python?")
        assert pkg.action == ActionIntent.QUERY

    # -- IntentPackage structure --

    def test_package_has_required_fields(self):
        pkg = self.engine.parse("crea un test para el router")
        assert isinstance(pkg, IntentPackage)
        assert isinstance(pkg.action, ActionIntent)
        assert isinstance(pkg.scope, IntentScope)
        assert isinstance(pkg.confidence, float)
        assert 0 <= pkg.confidence <= 1
        assert pkg.target != ""
        assert pkg.parsed_at > 0

    def test_package_to_dict(self):
        pkg = self.engine.parse("crea un módulo nuevo")
        d = pkg.to_dict()
        assert "action" in d
        assert "target" in d
        assert "scope" in d
        assert "confidence" in d

    def test_package_summary(self):
        pkg = self.engine.parse("crea un módulo nuevo")
        s = pkg.summary()
        assert "create" in s.lower() or "CREATE" in s

    # -- Constraints detection --

    def test_constraint_no_break(self):
        pkg = self.engine.parse("modifica el router sin romper la API existente")
        assert "no_break" in pkg.constraints

    def test_constraint_reuse(self):
        pkg = self.engine.parse("crea el módulo y reutilizar componentes existentes")
        assert "reuse_existing" in pkg.constraints

    def test_constraint_validate_tests(self):
        pkg = self.engine.parse("modifica el engine y validar con tests")
        assert "validate_tests" in pkg.constraints

    # -- Scope estimation --

    def test_scope_single_file(self):
        pkg = self.engine.parse("modifica el archivo config.py")
        # single file references typically get SINGLE_FILE scope
        assert pkg.scope in (IntentScope.SINGLE_FILE, IntentScope.MODULE)

    def test_scope_system(self):
        pkg = self.engine.parse(
            "rediseña toda la arquitectura del sistema completo de módulos "
            "conectando todos los servicios"
        )
        assert pkg.scope in (IntentScope.CROSS_MODULE, IntentScope.SYSTEM)


# ═══════════════════════════════════════════════════════════════════════════
# 2. ExecutionPlanner
# ═══════════════════════════════════════════════════════════════════════════

from core.execution_planner import (
    ExecutionPlanner,
    ExecutionPlan,
    PlanStep,
    StepType,
    ScanResult,
    RiskAssessment,
)


class TestExecutionPlanner:
    """Tests para el planificador de ejecución."""

    def setup_method(self):
        self.planner = ExecutionPlanner()
        self.engine = IntentEngine()

    def _make_intent(self, text: str) -> IntentPackage:
        return self.engine.parse(text)

    # -- Plan generation --

    def test_plan_create_generates_steps(self):
        intent = self._make_intent("crea un módulo de autenticación")
        plan = self.planner.plan(intent)
        assert isinstance(plan, ExecutionPlan)
        assert plan.total_steps > 0
        assert plan.plan_id.startswith("ep-")

    def test_plan_modify_generates_steps(self):
        intent = self._make_intent("modifica el smart router")
        plan = self.planner.plan(intent)
        assert plan.total_steps > 0

    def test_plan_analyze_generates_steps(self):
        intent = self._make_intent("analiza el rendimiento del sistema")
        plan = self.planner.plan(intent)
        assert plan.total_steps > 0

    # -- Plan structure --

    def test_plan_has_risk_assessment(self):
        intent = self._make_intent("crea un nuevo módulo")
        plan = self.planner.plan(intent)
        assert isinstance(plan.risk, RiskAssessment)
        assert plan.risk.level in ("LOW", "MEDIUM", "HIGH")

    def test_plan_to_dict(self):
        intent = self._make_intent("crea un módulo")
        plan = self.planner.plan(intent)
        d = plan.to_dict()
        assert "plan_id" in d
        assert "steps" in d
        assert "risk" in d

    def test_plan_summary(self):
        intent = self._make_intent("crea un módulo")
        plan = self.planner.plan(intent)
        s = plan.summary()
        assert "ep-" in s

    # -- Scan result --

    def test_scan_result_defaults(self):
        scan = ScanResult()
        assert scan.existing_files == []
        assert scan.total_files_scanned == 0

    # -- Step types --

    def test_step_types_enum(self):
        assert StepType.CREATE_FILE.value == "create_file"
        assert StepType.MODIFY_FILE.value == "modify_file"
        assert StepType.RUN_TESTS.value == "run_tests"

    def test_plan_step_to_dict(self):
        step = PlanStep(
            step_type=StepType.CREATE_FILE,
            target_path="core/new_module.py",
            description="Crear nuevo módulo",
            estimated_lines=50,
        )
        d = step.to_dict()
        assert d["step_type"] == "create_file"
        assert d["target_path"] == "core/new_module.py"


# ═══════════════════════════════════════════════════════════════════════════
# 3. AutoBuilder
# ═══════════════════════════════════════════════════════════════════════════

from core.auto_builder import (
    AutoBuilder,
    BuildResult,
    StepResult,
)


class TestAutoBuilder:
    """Tests para el constructor automático."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.builder = AutoBuilder(base_dir=self.tmpdir)
        self.engine = IntentEngine()
        self.planner = ExecutionPlanner(base_dir=self.tmpdir)

    def _make_plan(self, text: str) -> ExecutionPlan:
        intent = self.engine.parse(text)
        return self.planner.plan(intent)

    # -- Dry run --

    def test_dry_run_succeeds(self):
        plan = self._make_plan("crea un módulo de prueba")
        result = self.builder.build(plan, dry_run=True)
        assert isinstance(result, BuildResult)
        assert result.dry_run is True
        assert result.success is True
        assert result.test_passed is True  # dry_run skips tests
        assert result.total_steps > 0

    def test_dry_run_no_files_created(self):
        plan = self._make_plan("crea un módulo nuevo")
        result = self.builder.build(plan, dry_run=True)
        # In dry_run, files should not be physically created
        assert len(result.files_created) == 0

    def test_dry_run_steps_validated(self):
        plan = self._make_plan("crea un módulo de auth")
        result = self.builder.build(plan, dry_run=True)
        for sr in result.step_results:
            assert sr.success is True
            assert sr.action_taken in ("validated", "skipped")

    # -- Empty plan --

    def test_empty_plan_fails(self):
        plan = ExecutionPlan(steps=[])
        result = self.builder.build(plan, dry_run=True)
        assert result.success is False
        assert "vacío" in result.error.lower() or "empty" in result.error.lower()

    # -- BuildResult --

    def test_build_result_to_dict(self):
        plan = self._make_plan("crea un módulo")
        result = self.builder.build(plan, dry_run=True)
        d = result.to_dict()
        assert "plan_id" in d
        assert "dry_run" in d
        assert "success" in d
        assert "total_steps" in d

    def test_build_result_summary(self):
        plan = self._make_plan("crea un módulo")
        result = self.builder.build(plan, dry_run=True)
        s = result.summary()
        assert "DRY-RUN" in s
        assert "OK" in s

    # -- StepResult --

    def test_step_result_to_dict(self):
        sr = StepResult(
            step_type="create_file",
            target_path="core/test.py",
            success=True,
            action_taken="created",
        )
        d = sr.to_dict()
        assert d["step_type"] == "create_file"
        assert d["success"] is True

    # -- Build time --

    def test_build_time_recorded(self):
        plan = self._make_plan("crea un módulo")
        result = self.builder.build(plan, dry_run=True)
        assert result.build_time_ms > 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. IntentPipeline
# ═══════════════════════════════════════════════════════════════════════════

from core.intent_pipeline import (
    IntentPipeline,
    IntentResult,
    PipelineStatus,
)


class TestIntentPipeline:
    """Tests para el pipeline unificado de ejecución por intención."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pipeline = IntentPipeline(
            builder=AutoBuilder(base_dir=self.tmpdir),
            planner=ExecutionPlanner(base_dir=self.tmpdir),
        )

    # -- End-to-end dry_run --

    def test_pipeline_dry_run_create(self):
        result = self.pipeline.execute("crea un módulo de autenticación")
        assert isinstance(result, IntentResult)
        assert result.status == PipelineStatus.DRY_RUN_OK
        assert result.dry_run is True
        assert result.intent_package is not None
        assert result.smart_route is not None
        assert result.execution_plan is not None
        assert result.build_result is not None

    def test_pipeline_dry_run_modify(self):
        result = self.pipeline.execute("modifica el router principal")
        assert result.status == PipelineStatus.DRY_RUN_OK

    def test_pipeline_dry_run_analyze(self):
        result = self.pipeline.execute("analiza el rendimiento del sistema")
        assert result.status == PipelineStatus.DRY_RUN_OK

    # -- Stages tracking --

    def test_all_stages_completed(self):
        result = self.pipeline.execute("crea un módulo nuevo")
        assert "intent_parse" in result.stages_completed
        assert "smart_route" in result.stages_completed
        assert "policy_ok" in result.stages_completed
        assert "execution_plan" in result.stages_completed
        assert "build" in result.stages_completed
        assert "memory" in result.stages_completed
        assert len(result.stages_completed) == 6

    # -- IntentResult --

    def test_result_to_dict(self):
        result = self.pipeline.execute("crea un módulo")
        d = result.to_dict()
        assert "pipeline_id" in d
        assert "status" in d
        assert "intent" in d
        assert "stages_completed" in d

    def test_result_summary(self):
        result = self.pipeline.execute("crea un módulo")
        s = result.summary()
        assert "DRY-RUN" in s
        assert "dry_run_ok" in s

    def test_result_pipeline_id(self):
        result = self.pipeline.execute("crea algo")
        assert result.pipeline_id.startswith("pip-")

    def test_result_pipeline_time(self):
        result = self.pipeline.execute("crea un módulo")
        assert result.pipeline_time_ms > 0

    # -- Different intents --

    def test_pipeline_configure_intent(self):
        result = self.pipeline.execute("configura el rate limiter del API")
        assert result.status == PipelineStatus.DRY_RUN_OK
        assert result.intent_package.action == ActionIntent.CONFIGURE

    def test_pipeline_connect_intent(self):
        result = self.pipeline.execute("conecta el auth con la base de datos")
        assert result.status == PipelineStatus.DRY_RUN_OK
        assert result.intent_package.action == ActionIntent.CONNECT

    # -- Pipeline status enum --

    def test_pipeline_status_values(self):
        assert PipelineStatus.SUCCESS.value == "success"
        assert PipelineStatus.DRY_RUN_OK.value == "dry_run_ok"
        assert PipelineStatus.BLOCKED.value == "blocked"
        assert PipelineStatus.PROPOSAL_CREATED.value == "proposal_created"

    # -- Channel & owner propagation --

    def test_channel_and_owner(self):
        result = self.pipeline.execute(
            "crea un módulo", channel="admin", owner="test_user"
        )
        assert result.intent_package.channel == "admin"
        assert result.intent_package.owner == "test_user"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Integration — Import validation
# ═══════════════════════════════════════════════════════════════════════════


class TestImports:
    """Validate all modules can be imported."""

    def test_import_intent_engine(self):
        from core.intent_engine import IntentEngine, IntentPackage, ActionIntent
        assert IntentEngine is not None

    def test_import_execution_planner(self):
        from core.execution_planner import ExecutionPlanner, ExecutionPlan, StepType
        assert ExecutionPlanner is not None

    def test_import_auto_builder(self):
        from core.auto_builder import AutoBuilder, BuildResult, StepResult
        assert AutoBuilder is not None

    def test_import_intent_pipeline(self):
        from core.intent_pipeline import IntentPipeline, IntentResult, PipelineStatus
        assert IntentPipeline is not None

    def test_import_singletons(self):
        from core.auto_builder import get_auto_builder
        from core.intent_pipeline import get_intent_pipeline
        assert get_auto_builder() is not None
        assert get_intent_pipeline() is not None
