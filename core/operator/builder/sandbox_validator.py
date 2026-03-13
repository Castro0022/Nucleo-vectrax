"""
Vectrax Core — Sandbox Validator
====================================
Valida módulos generados ANTES de escribirlos al disco.
Verifica: sintaxis Python, imports resolubles, coherencia con
convenciones, y ausencia de patrones peligrosos.

Pipeline de validación:
  1. Syntax check (ast.parse)
  2. Import resolution (verificar que módulos referenciados existen)
  3. Convention check (docstring, logger, naming)
  4. Safety check (no exec(), no eval(), no os.system())
  5. Coherence check (no imports circulares obvios)

Principio P-005: "Antes de integrar, validar."

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import ast
import importlib
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.operator.builder.sandbox_validator")

BASE_DIR = os.path.expanduser("~/Vectrax")

# Funciones/patrones peligrosos que no deben aparecer en módulos generados
DANGEROUS_CALLS = frozenset({
    "exec", "eval", "compile", "__import__",
    "os.system", "subprocess.call", "subprocess.run",
    "os.remove", "os.unlink", "shutil.rmtree",
})


# ---------------------------------------------------------------------------
# Resultado de validación
# ---------------------------------------------------------------------------

class ValidationLevel(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass
class ValidationIssue:
    """Un problema encontrado durante la validación."""
    level: ValidationLevel
    check: str
    message: str
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "check": self.check,
            "message": self.message,
            "line": self.line,
        }


@dataclass
class ValidationResult:
    """Resultado completo de la validación."""
    source_path: str
    passed: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)
    checks_run: int = 0

    def add_issue(self, level: ValidationLevel, check: str, message: str, line: int = 0):
        issue = ValidationIssue(level=level, check=check, message=message, line=line)
        self.issues.append(issue)
        if level == ValidationLevel.FAIL:
            self.passed = False

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.WARNING]

    @property
    def failures(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.FAIL]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": self.source_path,
            "passed": self.passed,
            "checks_run": self.checks_run,
            "issues": [i.to_dict() for i in self.issues],
            "warnings": len(self.warnings),
            "failures": len(self.failures),
        }


# ---------------------------------------------------------------------------
# Sandbox Validator
# ---------------------------------------------------------------------------

class SandboxValidator:
    """
    Valida código generado en un entorno sandbox (sin escribir a disco).

    Usage::

        validator = SandboxValidator()
        result = validator.validate(source_code, "core/operator/new_module.py")
        if result.passed:
            # Safe to write
            ...
    """

    def __init__(self, base_dir: str = BASE_DIR) -> None:
        self._base_dir = base_dir

    def validate(self, source: str, file_path: str) -> ValidationResult:
        """
        Ejecuta todas las validaciones sobre el código fuente.

        Args:
            source: Código fuente Python a validar.
            file_path: Ruta relativa propuesta.

        Returns:
            ValidationResult con passed=True si es seguro integrar.
        """
        result = ValidationResult(source_path=file_path)

        # 1. Syntax
        self._check_syntax(source, result)
        result.checks_run += 1

        # 2. Imports
        self._check_imports(source, result)
        result.checks_run += 1

        # 3. Conventions
        self._check_conventions(source, result)
        result.checks_run += 1

        # 4. Safety
        self._check_safety(source, result)
        result.checks_run += 1

        # 5. File path safety
        self._check_path_safety(file_path, result)
        result.checks_run += 1

        logger.info(
            "Validation %s: passed=%s, issues=%d",
            file_path, result.passed, len(result.issues),
        )

        return result

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_syntax(source: str, result: ValidationResult) -> None:
        """Verifica que el código es Python válido."""
        try:
            ast.parse(source)
        except SyntaxError as exc:
            result.add_issue(
                ValidationLevel.FAIL,
                "syntax",
                f"SyntaxError: {exc.msg} (line {exc.lineno})",
                line=exc.lineno or 0,
            )

    def _check_imports(self, source: str, result: ValidationResult) -> None:
        """Verifica que los imports referenciados son resolubles."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return  # Already caught by syntax check

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                # Check if module exists as a file in the project
                parts = module.split(".")
                possible_path = os.path.join(self._base_dir, *parts) + ".py"
                possible_pkg = os.path.join(self._base_dir, *parts, "__init__.py")

                if not os.path.exists(possible_path) and not os.path.exists(possible_pkg):
                    # Try as a stdlib/installed module
                    try:
                        importlib.util.find_spec(module)
                    except (ModuleNotFoundError, ValueError):
                        result.add_issue(
                            ValidationLevel.WARNING,
                            "imports",
                            f"Import '{module}' not found in project or stdlib",
                            line=node.lineno,
                        )

    @staticmethod
    def _check_conventions(source: str, result: ValidationResult) -> None:
        """Verifica convenciones del codebase Vectrax."""
        # Module docstring
        try:
            tree = ast.parse(source)
            docstring = ast.get_docstring(tree)
            if not docstring:
                result.add_issue(
                    ValidationLevel.WARNING,
                    "conventions",
                    "Missing module docstring",
                )
        except SyntaxError:
            return

        # from __future__ import annotations
        if "from __future__ import annotations" not in source:
            result.add_issue(
                ValidationLevel.WARNING,
                "conventions",
                "Missing 'from __future__ import annotations'",
            )

        # Logger
        if "logging.getLogger" not in source and "import logging" in source:
            result.add_issue(
                ValidationLevel.WARNING,
                "conventions",
                "Module imports logging but doesn't create a module-level logger",
            )

    @staticmethod
    def _check_safety(source: str, result: ValidationResult) -> None:
        """Verifica que no hay patrones peligrosos."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            # Check for dangerous function calls
            if isinstance(node, ast.Call):
                call_name = ""
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    # Build dotted name
                    parts = []
                    n = node.func
                    while isinstance(n, ast.Attribute):
                        parts.append(n.attr)
                        n = n.value
                    if isinstance(n, ast.Name):
                        parts.append(n.id)
                    call_name = ".".join(reversed(parts))

                if call_name in DANGEROUS_CALLS:
                    result.add_issue(
                        ValidationLevel.FAIL,
                        "safety",
                        f"Dangerous call detected: {call_name}()",
                        line=node.lineno,
                    )

    @staticmethod
    def _check_path_safety(file_path: str, result: ValidationResult) -> None:
        """Verifica que la ruta propuesta no toca zonas protegidas."""
        dangerous_prefixes = [
            "vault/", ".git/", ".env", "secrets/", "keys/",
        ]
        for prefix in dangerous_prefixes:
            if file_path.startswith(prefix) or f"/{prefix}" in file_path:
                result.add_issue(
                    ValidationLevel.FAIL,
                    "path_safety",
                    f"Target path is in protected zone: {prefix}",
                )
