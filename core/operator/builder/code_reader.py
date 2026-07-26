"""
Vectrax Core — Code Reader
==============================
Lector y analizador de código Python existente. Usa el módulo `ast`
de la stdlib para extraer estructura sin ejecutar código.

Extrae:
  - Clases (nombre, bases, métodos, docstring)
  - Funciones (nombre, args, docstring, decoradores)
  - Imports (módulos, from-imports)
  - Constantes de módulo (asignaciones top-level)
  - Docstring del módulo

No ejecuta código. No modifica archivos. Solo lectura y análisis.

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import ast
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.operator.builder.code_reader")

# Repo root derived from THIS file's location
# (…/core/operator/builder/code_reader.py -> 4x dirname = repo root).
# The old hardcoded "~/Vectrax" broke whenever the checkout lived elsewhere
# (CI runners, Docker /app, a different user's home).
BASE_DIR = os.environ.get(
    "VECTRAX_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)


# ---------------------------------------------------------------------------
# Estructuras de análisis
# ---------------------------------------------------------------------------

@dataclass
class FunctionInfo:
    """Información extraída de una función."""
    name: str
    args: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    docstring: str = ""
    is_method: bool = False
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "args": self.args,
            "decorators": self.decorators,
            "docstring": self.docstring[:200],
            "is_method": self.is_method,
            "line": self.line,
        }


@dataclass
class ClassInfo:
    """Información extraída de una clase."""
    name: str
    bases: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    docstring: str = ""
    methods: List[FunctionInfo] = field(default_factory=list)
    is_dataclass: bool = False
    is_frozen: bool = False
    is_enum: bool = False
    line: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "bases": self.bases,
            "decorators": self.decorators,
            "docstring": self.docstring[:200],
            "methods": [m.name for m in self.methods],
            "is_dataclass": self.is_dataclass,
            "is_frozen": self.is_frozen,
            "is_enum": self.is_enum,
            "line": self.line,
        }


@dataclass
class ImportInfo:
    """Información de un import."""
    module: str
    names: List[str] = field(default_factory=list)
    is_from: bool = False
    line: int = 0


@dataclass
class ModuleAnalysis:
    """Análisis completo de un módulo Python."""
    path: str
    module_docstring: str = ""
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    imports: List[ImportInfo] = field(default_factory=list)
    constants: List[str] = field(default_factory=list)
    lines_total: int = 0
    has_main_guard: bool = False
    has_singleton: bool = False
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "module_docstring": self.module_docstring[:200],
            "classes": [c.to_dict() for c in self.classes],
            "functions": [f.to_dict() for f in self.functions],
            "import_count": len(self.imports),
            "constant_count": len(self.constants),
            "lines_total": self.lines_total,
            "has_main_guard": self.has_main_guard,
            "has_singleton": self.has_singleton,
        }

    @property
    def summary(self) -> str:
        parts = []
        if self.classes:
            parts.append(f"{len(self.classes)} classes")
        if self.functions:
            parts.append(f"{len(self.functions)} functions")
        parts.append(f"{self.lines_total} lines")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Code Reader
# ---------------------------------------------------------------------------

class CodeReader:
    """
    Lector de código Python basado en AST.

    Usage::

        reader = CodeReader()
        analysis = reader.analyze_file("core/governor.py")
        print(analysis.classes)
        print(analysis.functions)
    """

    def __init__(self, base_dir: str = BASE_DIR) -> None:
        self._base_dir = base_dir

    def analyze_file(self, path: str) -> ModuleAnalysis:
        """
        Analiza un archivo Python y retorna su estructura.

        Args:
            path: Ruta relativa al proyecto o absoluta.

        Returns:
            ModuleAnalysis con toda la información extraída.
        """
        abs_path = self._resolve(path)
        analysis = ModuleAnalysis(path=path)

        if not os.path.isfile(abs_path):
            analysis.errors.append(f"File not found: {abs_path}")
            return analysis

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                source = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            analysis.errors.append(f"Read error: {exc}")
            return analysis

        analysis.lines_total = source.count("\n") + 1

        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            analysis.errors.append(f"Syntax error: {exc}")
            return analysis

        # Module docstring
        analysis.module_docstring = ast.get_docstring(tree) or ""

        # Walk top-level nodes
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                analysis.classes.append(self._extract_class(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                analysis.functions.append(self._extract_function(node))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    analysis.imports.append(ImportInfo(
                        module=alias.name, names=[alias.asname or alias.name],
                        is_from=False, line=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [a.name for a in node.names]
                analysis.imports.append(ImportInfo(
                    module=module, names=names, is_from=True, line=node.lineno,
                ))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        analysis.constants.append(target.id)
            elif isinstance(node, ast.If):
                # Detect if __name__ == "__main__"
                if self._is_main_guard(node):
                    analysis.has_main_guard = True

        # Detect singleton pattern
        source_lower = source.lower()
        if "_instance" in source_lower or "singleton" in source_lower:
            analysis.has_singleton = True
        # Also detect the get_xxx() pattern common in Vectrax
        for func in analysis.functions:
            if func.name.startswith("get_") and "global" in source[
                max(0, source.find(f"def {func.name}") - 50):
                source.find(f"def {func.name}") + 200
            ]:
                analysis.has_singleton = True
                break

        return analysis

    def analyze_directory(
        self,
        dir_path: str,
        recursive: bool = True,
    ) -> List[ModuleAnalysis]:
        """Analiza todos los archivos .py en un directorio."""
        abs_dir = self._resolve(dir_path)
        results: List[ModuleAnalysis] = []

        if not os.path.isdir(abs_dir):
            return results

        for root, dirs, files in os.walk(abs_dir):
            # Skip hidden dirs and __pycache__
            dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
            for fname in sorted(files):
                if fname.endswith(".py"):
                    rel = os.path.relpath(os.path.join(root, fname), self._base_dir)
                    results.append(self.analyze_file(rel))
            if not recursive:
                break

        return results

    def read_source(self, path: str) -> str:
        """Lee el código fuente de un archivo."""
        abs_path = self._resolve(path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""

    # ------------------------------------------------------------------
    # Extractores internos
    # ------------------------------------------------------------------

    def _extract_class(self, node: ast.ClassDef) -> ClassInfo:
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.dump(base))

        decorators = [self._decorator_name(d) for d in node.decorator_list]

        info = ClassInfo(
            name=node.name,
            bases=bases,
            decorators=decorators,
            docstring=ast.get_docstring(node) or "",
            line=node.lineno,
            is_dataclass="dataclass" in decorators,
            is_enum="Enum" in bases or "str, Enum" in str(bases),
        )

        # Check frozen dataclass
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and self._decorator_name(dec) == "dataclass":
                for kw in dec.keywords:
                    if kw.arg == "frozen" and isinstance(kw.value, ast.Constant):
                        info.is_frozen = kw.value.value is True

        # Extract methods
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = self._extract_function(item, is_method=True)
                info.methods.append(method)

        return info

    def _extract_function(
        self,
        node,
        is_method: bool = False,
    ) -> FunctionInfo:
        args = []
        for arg in node.args.args:
            if arg.arg != "self" and arg.arg != "cls":
                args.append(arg.arg)

        decorators = [self._decorator_name(d) for d in node.decorator_list]

        return FunctionInfo(
            name=node.name,
            args=args,
            decorators=decorators,
            docstring=ast.get_docstring(node) or "",
            is_method=is_method,
            is_static="staticmethod" in decorators,
            is_classmethod="classmethod" in decorators,
            is_property="property" in decorators,
            line=node.lineno,
        )

    @staticmethod
    def _decorator_name(node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return node.attr
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return node.func.id
            elif isinstance(node.func, ast.Attribute):
                return node.func.attr
        return "unknown"

    @staticmethod
    def _is_main_guard(node: ast.If) -> bool:
        try:
            test = node.test
            if isinstance(test, ast.Compare):
                left = test.left
                if isinstance(left, ast.Name) and left.id == "__name__":
                    return True
        except Exception:
            pass
        return False

    def _resolve(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self._base_dir, path)
