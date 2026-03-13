"""
Vectrax Execution Planner — Planificador de Ejecución
========================================================
Inspecciona el repositorio, detecta módulos existentes y genera
un plan de implementación estructurado a partir de un IntentPackage.

Compone:
  - IntentEngine   → IntentPackage con acción/target/scope
  - ActionSandbox  → validación de paths y seguridad
  - ReasoningEngine (opcional) → evaluación de riesgo del plan

Pipeline:
  IntentPackage → scan_repository → detect_dependencies → generate_steps
  → risk_assessment → ExecutionPlan

Nunca ejecuta — solo planifica.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.intent_engine import ActionIntent, IntentPackage, IntentScope

logger = logging.getLogger("vectrax.execution_planner")

BASE_DIR = os.path.expanduser("~/Vectrax")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class StepType(str, Enum):
    """Tipo de paso en el plan de ejecución."""
    CREATE_FILE   = "create_file"
    MODIFY_FILE   = "modify_file"
    CREATE_DIR    = "create_dir"
    RUN_TESTS     = "run_tests"
    RUN_LINT      = "run_lint"
    REGISTER      = "register"      # registrar en __init__, CLI, etc.
    VALIDATE      = "validate"


@dataclass
class PlanStep:
    """Un paso individual en el plan de ejecución."""
    step_type: StepType
    target_path: str                    # archivo o directorio afectado
    description: str
    dependencies: List[str] = field(default_factory=list)  # paths que deben existir
    estimated_lines: int = 0
    risk_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_type": self.step_type.value,
            "target_path": self.target_path,
            "description": self.description,
            "dependencies": self.dependencies,
            "estimated_lines": self.estimated_lines,
            "risk_notes": self.risk_notes,
        }


@dataclass
class ScanResult:
    """Resultado de escanear el repositorio para un target."""
    existing_files: List[str] = field(default_factory=list)   # archivos relevantes encontrados
    existing_dirs: List[str] = field(default_factory=list)    # directorios relevantes
    imports_found: List[str] = field(default_factory=list)    # módulos que importan el target
    related_tests: List[str] = field(default_factory=list)    # tests relacionados
    total_files_scanned: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "existing_files": self.existing_files[:20],
            "existing_dirs": self.existing_dirs[:10],
            "imports_found": self.imports_found[:10],
            "related_tests": self.related_tests[:10],
            "total_files_scanned": self.total_files_scanned,
        }


@dataclass
class RiskAssessment:
    """Evaluación de riesgo del plan."""
    level: str = "LOW"          # LOW, MEDIUM, HIGH
    score: float = 0.0          # 0.0 – 1.0
    concerns: List[str] = field(default_factory=list)
    recommendation: str = "proceed"  # proceed, review, block

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "score": round(self.score, 4),
            "concerns": self.concerns,
            "recommendation": self.recommendation,
        }


@dataclass
class ExecutionPlan:
    """Plan de ejecución completo generado por el planner."""
    plan_id: str = field(default_factory=lambda: f"ep-{uuid.uuid4().hex[:8]}")
    intent_summary: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    scan_result: Optional[ScanResult] = None
    risk: RiskAssessment = field(default_factory=RiskAssessment)
    estimated_scope: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def all_affected_paths(self) -> List[str]:
        return self.files_to_create + self.files_to_modify

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "intent_summary": self.intent_summary,
            "total_steps": self.total_steps,
            "steps": [s.to_dict() for s in self.steps],
            "files_to_create": self.files_to_create,
            "files_to_modify": self.files_to_modify,
            "dependencies": self.dependencies,
            "risk": self.risk.to_dict(),
            "estimated_scope": self.estimated_scope,
        }

    def summary(self) -> str:
        return (
            f"[{self.plan_id}] {self.total_steps} steps, "
            f"create={len(self.files_to_create)}, modify={len(self.files_to_modify)}, "
            f"risk={self.risk.level}"
        )


# ---------------------------------------------------------------------------
# Execution Planner
# ---------------------------------------------------------------------------

class ExecutionPlanner:
    """
    Planificador de ejecución de Vectrax.

    Inspecciona el repositorio, detecta módulos existentes y genera
    un plan de implementación estructurado a partir de un IntentPackage.

    Usage::

        planner = ExecutionPlanner()
        plan = planner.plan(intent_package)
        print(plan.summary())
    """

    def __init__(self, base_dir: str = BASE_DIR) -> None:
        self._base_dir = base_dir

    # -- Plan principal -----------------------------------------------------

    def plan(self, intent: IntentPackage) -> ExecutionPlan:
        """
        Genera un ExecutionPlan a partir de un IntentPackage.

        Args:
            intent: IntentPackage con acción, target, scope, constraints.

        Returns:
            ExecutionPlan con steps, files, risk assessment.
        """
        t0 = time.time()

        # 1. Escanear repositorio
        scan = self._scan_repository(intent.target)

        # 2. Generar steps según la acción
        steps = self._generate_steps(intent, scan)

        # 3. Identificar archivos afectados
        files_create = [s.target_path for s in steps if s.step_type == StepType.CREATE_FILE]
        files_modify = [s.target_path for s in steps if s.step_type == StepType.MODIFY_FILE]

        # 4. Detectar dependencias
        deps = self._detect_dependencies(intent.target, scan)

        # 5. Evaluar riesgo
        risk = self._assess_risk(intent, scan, files_create, files_modify)

        # 6. Validar seguridad con ActionSandbox (si hay archivos a crear/modificar)
        self._validate_with_sandbox(files_create, files_modify)

        plan = ExecutionPlan(
            intent_summary=intent.description,
            steps=steps,
            files_to_create=files_create,
            files_to_modify=files_modify,
            dependencies=deps,
            scan_result=scan,
            risk=risk,
            estimated_scope=intent.scope.value,
        )

        elapsed = (time.time() - t0) * 1000
        logger.info("ExecutionPlanner: %s (%.1fms)", plan.summary(), elapsed)
        return plan

    # -- Escaneo del repositorio --------------------------------------------

    def _scan_repository(self, target: str) -> ScanResult:
        """Escanea el repositorio buscando módulos relevantes al target."""
        result = ScanResult()
        target_lower = target.lower()

        # Extraer keywords del target
        keywords = set(re.findall(r"\w{3,}", target_lower))
        keywords.discard("the")
        keywords.discard("una")
        keywords.discard("con")

        scan_dirs = ["core", "vectrax", "cognition", "services", "connectors", "tests"]
        count = 0

        for scan_dir in scan_dirs:
            full_dir = os.path.join(self._base_dir, scan_dir)
            if not os.path.isdir(full_dir):
                continue

            for root, dirs, files in os.walk(full_dir):
                # Skip hidden and cache dirs
                dirs[:] = [d for d in dirs if not d.startswith((".","__"))]

                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    count += 1
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, self._base_dir)

                    # Check if filename matches keywords
                    fname_lower = fname.lower().replace(".py", "").replace("_", " ")
                    if keywords & set(fname_lower.split()):
                        result.existing_files.append(rel)

                    # Check for test files
                    if fname.startswith("test_") or "_test" in fname:
                        for kw in keywords:
                            if kw in fname_lower:
                                result.related_tests.append(rel)
                                break

                # Track relevant directories
                rel_dir = os.path.relpath(root, self._base_dir)
                dir_name = os.path.basename(root).lower()
                if keywords & set(dir_name.split("_")):
                    result.existing_dirs.append(rel_dir)

        result.total_files_scanned = count

        # Search for imports (quick grep for module name in first 50 existing files)
        main_keyword = sorted(keywords, key=len, reverse=True)[0] if keywords else ""
        if main_keyword and len(main_keyword) > 3:
            result.imports_found = self._find_imports(main_keyword, limit=10)

        return result

    def _find_imports(self, keyword: str, limit: int = 10) -> List[str]:
        """Busca archivos que importen un módulo que contenga el keyword."""
        found = []
        pattern = re.compile(
            rf"\b(import|from)\b.*\b{re.escape(keyword)}\b",
            re.IGNORECASE,
        )

        for scan_dir in ["core", "vectrax", "cognition"]:
            full_dir = os.path.join(self._base_dir, scan_dir)
            if not os.path.isdir(full_dir):
                continue
            for root, dirs, files in os.walk(full_dir):
                dirs[:] = [d for d in dirs if not d.startswith((".","__"))]
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            head = f.read(2000)  # solo leer primeras líneas
                        if pattern.search(head):
                            found.append(os.path.relpath(fpath, self._base_dir))
                            if len(found) >= limit:
                                return found
                    except Exception:
                        continue
        return found

    # -- Detección de dependencias ------------------------------------------

    def _detect_dependencies(self, target: str, scan: ScanResult) -> List[str]:
        """Identifica dependencias del target basándose en el scan."""
        deps = []

        # Los archivos existentes relacionados son dependencias potenciales
        for f in scan.existing_files[:5]:
            deps.append(f)

        # Los imports encontrados indican que otros módulos dependen del target
        for f in scan.imports_found[:5]:
            if f not in deps:
                deps.append(f)

        return deps

    # -- Generación de steps ------------------------------------------------

    def _generate_steps(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Genera la lista de steps según la acción y el contexto."""
        steps: List[PlanStep] = []

        if intent.action == ActionIntent.CREATE:
            steps.extend(self._steps_for_create(intent, scan))
        elif intent.action == ActionIntent.MODIFY:
            steps.extend(self._steps_for_modify(intent, scan))
        elif intent.action == ActionIntent.CONNECT:
            steps.extend(self._steps_for_connect(intent, scan))
        elif intent.action == ActionIntent.ANALYZE:
            steps.extend(self._steps_for_analyze(intent, scan))
        elif intent.action == ActionIntent.DEPLOY:
            steps.extend(self._steps_for_deploy(intent, scan))
        elif intent.action == ActionIntent.CONFIGURE:
            steps.extend(self._steps_for_configure(intent, scan))
        else:
            # QUERY — no hay steps de ejecución
            return []

        # Siempre agregar validación al final
        if steps:
            steps.append(PlanStep(
                step_type=StepType.RUN_TESTS,
                target_path="tests/",
                description="Ejecutar test suite para validar cambios",
            ))

        return steps

    def _steps_for_create(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Steps para CREATE: nuevo módulo/archivo."""
        steps = []
        target_name = self._normalize_name(intent.target)

        # Determinar directorio basado en scope
        if intent.scope == IntentScope.SYSTEM:
            base = "core"
        elif intent.scope == IntentScope.MODULE:
            base = "core"
        else:
            base = "core"

        module_path = f"{base}/{target_name}.py"
        test_path = f"tests/test_{target_name}.py"

        # Verificar si ya existe
        if module_path in scan.existing_files:
            steps.append(PlanStep(
                step_type=StepType.MODIFY_FILE,
                target_path=module_path,
                description=f"Extender módulo existente: {module_path}",
                risk_notes="Módulo ya existe — modificar en vez de crear",
            ))
        else:
            steps.append(PlanStep(
                step_type=StepType.CREATE_FILE,
                target_path=module_path,
                description=f"Crear módulo: {module_path}",
                estimated_lines=150,
            ))

        # Test file
        if test_path not in scan.related_tests:
            steps.append(PlanStep(
                step_type=StepType.CREATE_FILE,
                target_path=test_path,
                description=f"Crear tests: {test_path}",
                estimated_lines=80,
                dependencies=[module_path],
            ))

        # Register en __init__ si es módulo
        init_path = f"{base}/__init__.py"
        if os.path.exists(os.path.join(self._base_dir, init_path)):
            steps.append(PlanStep(
                step_type=StepType.REGISTER,
                target_path=init_path,
                description=f"Registrar {target_name} en {init_path}",
                dependencies=[module_path],
            ))

        return steps

    def _steps_for_modify(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Steps para MODIFY: cambiar módulo existente."""
        steps = []

        if scan.existing_files:
            for f in scan.existing_files[:3]:
                steps.append(PlanStep(
                    step_type=StepType.MODIFY_FILE,
                    target_path=f,
                    description=f"Modificar: {f}",
                ))
        else:
            # No encontró archivos — crear nuevo como fallback
            target_name = self._normalize_name(intent.target)
            steps.append(PlanStep(
                step_type=StepType.CREATE_FILE,
                target_path=f"core/{target_name}.py",
                description=f"Crear módulo (target no encontrado): core/{target_name}.py",
                risk_notes="Target no encontrado en el repositorio",
            ))

        return steps

    def _steps_for_connect(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Steps para CONNECT: integrar módulos."""
        steps = []
        target_name = self._normalize_name(intent.target)

        # Crear bridge/adapter
        bridge_path = f"core/{target_name}_bridge.py"
        steps.append(PlanStep(
            step_type=StepType.CREATE_FILE,
            target_path=bridge_path,
            description=f"Crear bridge de integración: {bridge_path}",
            dependencies=scan.existing_files[:5],
            estimated_lines=100,
        ))

        # Modificar módulos existentes para la integración
        for f in scan.existing_files[:2]:
            steps.append(PlanStep(
                step_type=StepType.MODIFY_FILE,
                target_path=f,
                description=f"Agregar hooks de integración en: {f}",
            ))

        # Test
        steps.append(PlanStep(
            step_type=StepType.CREATE_FILE,
            target_path=f"tests/test_{target_name}_bridge.py",
            description="Crear tests de integración",
            estimated_lines=60,
            dependencies=[bridge_path],
        ))

        return steps

    def _steps_for_analyze(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Steps para ANALYZE: inspección sin cambios destructivos."""
        steps = []

        steps.append(PlanStep(
            step_type=StepType.VALIDATE,
            target_path=".",
            description=f"Analizar: {intent.target}",
        ))

        if scan.existing_files:
            steps.append(PlanStep(
                step_type=StepType.VALIDATE,
                target_path=scan.existing_files[0],
                description=f"Inspeccionar módulo principal: {scan.existing_files[0]}",
            ))

        return steps

    def _steps_for_deploy(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Steps para DEPLOY."""
        steps = []

        steps.append(PlanStep(
            step_type=StepType.RUN_TESTS,
            target_path="tests/",
            description="Ejecutar test suite completa pre-deploy",
        ))
        steps.append(PlanStep(
            step_type=StepType.RUN_LINT,
            target_path=".",
            description="Lint y type-check pre-deploy",
        ))
        steps.append(PlanStep(
            step_type=StepType.VALIDATE,
            target_path=".",
            description=f"Validar deploy: {intent.target}",
            risk_notes="Deploy requiere revisión del creator",
        ))

        return steps

    def _steps_for_configure(
        self, intent: IntentPackage, scan: ScanResult,
    ) -> List[PlanStep]:
        """Steps para CONFIGURE."""
        steps = []

        # Buscar archivos de configuración
        config_files = [f for f in scan.existing_files if "config" in f.lower()]
        if config_files:
            for cf in config_files[:2]:
                steps.append(PlanStep(
                    step_type=StepType.MODIFY_FILE,
                    target_path=cf,
                    description=f"Modificar configuración: {cf}",
                ))
        else:
            steps.append(PlanStep(
                step_type=StepType.MODIFY_FILE,
                target_path="config.py",
                description="Modificar configuración principal",
            ))

        return steps

    # -- Risk assessment ----------------------------------------------------

    def _assess_risk(
        self,
        intent: IntentPackage,
        scan: ScanResult,
        files_create: List[str],
        files_modify: List[str],
    ) -> RiskAssessment:
        """Evalúa el riesgo del plan."""
        concerns = []
        score = 0.0

        # Scope amplios son más riesgosos
        if intent.scope == IntentScope.SYSTEM:
            score += 0.3
            concerns.append("Scope de sistema — afecta múltiples componentes")
        elif intent.scope == IntentScope.CROSS_MODULE:
            score += 0.2
            concerns.append("Scope cross-module — requiere coordinación")

        # Modificar archivos existentes es más riesgoso que crear nuevos
        if files_modify:
            score += 0.15 * min(len(files_modify), 3)
            concerns.append(f"Modifica {len(files_modify)} archivo(s) existente(s)")

        # Si el target no fue encontrado, hay incertidumbre
        if not scan.existing_files and intent.action != ActionIntent.CREATE:
            score += 0.1
            concerns.append("Target no encontrado en el repositorio")

        # Si hay muchos imports, el impacto es mayor
        if len(scan.imports_found) > 3:
            score += 0.1
            concerns.append(f"{len(scan.imports_found)} módulos dependen del target")

        # Deploy siempre es alto riesgo
        if intent.action == ActionIntent.DEPLOY:
            score += 0.3
            concerns.append("Deploy — requiere validación completa")

        score = min(score, 1.0)

        if score >= 0.6:
            level = "HIGH"
            rec = "review"
        elif score >= 0.3:
            level = "MEDIUM"
            rec = "proceed"
        else:
            level = "LOW"
            rec = "proceed"

        return RiskAssessment(
            level=level,
            score=score,
            concerns=concerns,
            recommendation=rec,
        )

    # -- Validación con ActionSandbox ---------------------------------------

    def _validate_with_sandbox(
        self, files_create: List[str], files_modify: List[str],
    ) -> None:
        """Valida paths con ActionSandbox (fail-safe)."""
        try:
            from core.action_sandbox import get_sandbox
            sandbox = get_sandbox()
            all_paths = files_create + files_modify
            if all_paths:
                sandbox._validate_paths(all_paths)
        except Exception as exc:
            logger.debug("Sandbox validation skipped: %s", exc)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _normalize_name(target: str) -> str:
        """Normaliza un target a nombre de archivo Python válido."""
        # Tomar solo las palabras significativas
        words = re.findall(r"[a-záéíóúñü]+", target.lower())
        # Filtrar stopwords
        stopwords = {"un", "una", "el", "la", "los", "las", "de", "del",
                     "con", "para", "en", "que", "the", "a", "an", "of",
                     "with", "for", "in", "to"}
        words = [w for w in words if w not in stopwords and len(w) > 2]
        if not words:
            return "new_module"
        return "_".join(words[:4])

    def __repr__(self) -> str:
        return f"ExecutionPlanner(base={self._base_dir})"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_planner: Optional[ExecutionPlanner] = None


def get_execution_planner() -> ExecutionPlanner:
    """Return the global ExecutionPlanner singleton."""
    global _planner
    if _planner is None:
        _planner = ExecutionPlanner()
    return _planner
