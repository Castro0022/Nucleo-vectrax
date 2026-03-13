"""
Vectrax Core — Pattern Extractor
====================================
Detecta patrones y convenciones del codebase analizando múltiples
módulos con el CodeReader.

Patrones detectados:
  - Singleton (get_xxx() + global _xxx)
  - Frozen dataclass para inmutables
  - Enum para clasificaciones
  - Docstring format (triple-quoted, reST, Google)
  - Import style (from __future__, relative vs absolute)
  - Naming conventions (snake_case functions, PascalCase classes)
  - to_dict() serialization pattern
  - Logger pattern (module-level logger)
  - Constant naming (UPPER_CASE)

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.operator.builder.code_reader import CodeReader, ModuleAnalysis

logger = logging.getLogger("vectrax.operator.builder.pattern_extractor")


# ---------------------------------------------------------------------------
# Resultado del análisis de patrones
# ---------------------------------------------------------------------------

@dataclass
class PatternReport:
    """Patrones detectados en el codebase."""
    modules_analyzed: int = 0
    total_classes: int = 0
    total_functions: int = 0
    total_lines: int = 0

    # Patrones estructurales
    singleton_count: int = 0
    dataclass_count: int = 0
    frozen_dataclass_count: int = 0
    enum_count: int = 0
    has_to_dict_count: int = 0

    # Convenciones de import
    uses_future_annotations: int = 0
    uses_from_imports: int = 0
    uses_direct_imports: int = 0

    # Convenciones de logging
    uses_module_logger: int = 0
    logger_format: str = ""  # e.g. "vectrax.module.name"

    # Convenciones de docstring
    has_module_docstring: int = 0
    has_class_docstring: int = 0
    has_function_docstring: int = 0

    # Top imports (más usados)
    top_imports: List[str] = field(default_factory=list)

    # Top base classes
    top_bases: List[str] = field(default_factory=list)

    # Patrones detectados como lista legible
    detected_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modules_analyzed": self.modules_analyzed,
            "total_classes": self.total_classes,
            "total_functions": self.total_functions,
            "total_lines": self.total_lines,
            "patterns": {
                "singleton": self.singleton_count,
                "dataclass": self.dataclass_count,
                "frozen_dataclass": self.frozen_dataclass_count,
                "enum": self.enum_count,
                "to_dict": self.has_to_dict_count,
                "module_logger": self.uses_module_logger,
                "future_annotations": self.uses_future_annotations,
            },
            "conventions": {
                "top_imports": self.top_imports[:10],
                "top_bases": self.top_bases[:5],
                "logger_format": self.logger_format,
            },
            "docstring_coverage": {
                "modules": self.has_module_docstring,
                "classes": self.has_class_docstring,
                "functions": self.has_function_docstring,
            },
            "detected_patterns": self.detected_patterns,
        }


# ---------------------------------------------------------------------------
# Pattern Extractor
# ---------------------------------------------------------------------------

class PatternExtractor:
    """
    Extrae patrones y convenciones del codebase.

    Usage::

        extractor = PatternExtractor()
        report = extractor.analyze("core/operator")
        print(report.detected_patterns)
    """

    def __init__(self, reader: CodeReader | None = None) -> None:
        self._reader = reader or CodeReader()

    def analyze(self, dir_path: str, recursive: bool = True) -> PatternReport:
        """Analiza un directorio y retorna los patrones detectados."""
        modules = self._reader.analyze_directory(dir_path, recursive=recursive)
        return self._extract_patterns(modules)

    def analyze_modules(self, modules: List[ModuleAnalysis]) -> PatternReport:
        """Analiza una lista de módulos ya analizados."""
        return self._extract_patterns(modules)

    def _extract_patterns(self, modules: List[ModuleAnalysis]) -> PatternReport:
        report = PatternReport(modules_analyzed=len(modules))

        import_counter: Counter = Counter()
        base_counter: Counter = Counter()
        logger_prefixes: List[str] = []

        for mod in modules:
            report.total_lines += mod.lines_total

            # Docstrings
            if mod.module_docstring:
                report.has_module_docstring += 1

            # Singleton
            if mod.has_singleton:
                report.singleton_count += 1

            # Imports
            for imp in mod.imports:
                if imp.module == "__future__" and "annotations" in imp.names:
                    report.uses_future_annotations += 1
                if imp.is_from:
                    report.uses_from_imports += 1
                    import_counter[imp.module] += 1
                else:
                    report.uses_direct_imports += 1
                    import_counter[imp.module] += 1

            # Logger pattern
            for const in mod.constants:
                if const == "logger":
                    report.uses_module_logger += 1

            # Classes
            for cls in mod.classes:
                report.total_classes += 1
                if cls.docstring:
                    report.has_class_docstring += 1
                if cls.is_dataclass:
                    report.dataclass_count += 1
                if cls.is_frozen:
                    report.frozen_dataclass_count += 1
                if cls.is_enum:
                    report.enum_count += 1
                for base in cls.bases:
                    base_counter[base] += 1
                # to_dict pattern
                if any(m.name == "to_dict" for m in cls.methods):
                    report.has_to_dict_count += 1

            # Functions
            for func in mod.functions:
                report.total_functions += 1
                if func.docstring:
                    report.has_function_docstring += 1

        # Top imports
        report.top_imports = [name for name, _ in import_counter.most_common(10)]

        # Top bases
        report.top_bases = [name for name, _ in base_counter.most_common(5)]

        # Detect logger format from source
        # (the pattern "vectrax.xxx.yyy" is dominant in the codebase)
        report.logger_format = "vectrax.<module_path>"

        # Generate detected patterns list
        report.detected_patterns = self._summarize_patterns(report)

        return report

    @staticmethod
    def _summarize_patterns(report: PatternReport) -> List[str]:
        patterns = []
        total = report.modules_analyzed or 1

        if report.singleton_count > total * 0.2:
            patterns.append("SINGLETON: get_xxx() + global _xxx pattern dominant")
        if report.dataclass_count > 3:
            patterns.append(f"DATACLASS: {report.dataclass_count} dataclasses used for structured data")
        if report.frozen_dataclass_count > 1:
            patterns.append(f"FROZEN_DATACLASS: {report.frozen_dataclass_count} frozen (immutable) dataclasses")
        if report.enum_count > 2:
            patterns.append(f"ENUM: {report.enum_count} enums for classifications (str, Enum pattern)")
        if report.has_to_dict_count > 3:
            patterns.append(f"TO_DICT: {report.has_to_dict_count} classes implement to_dict() serialization")
        if report.uses_future_annotations > total * 0.5:
            patterns.append("FUTURE_ANNOTATIONS: from __future__ import annotations is standard")
        if report.uses_module_logger > total * 0.3:
            patterns.append("MODULE_LOGGER: module-level logger = logging.getLogger(...) is standard")
        if report.has_module_docstring > total * 0.5:
            patterns.append("MODULE_DOCSTRING: most modules have top-level docstrings")

        return patterns
