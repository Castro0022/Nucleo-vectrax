"""
Vectrax Core — Module Generator
===================================
Genera esqueletos de módulos Python siguiendo las convenciones
detectadas en el codebase por el PatternExtractor.

IMPORTANTE: El generador produce PROPUESTAS de código. No escribe
archivos directamente al disco — retorna el contenido generado para
que sea validado en sandbox y aprobado antes de integrar.

Principio P-005: "Todo módulo nuevo debe ser coherente con el núcleo."

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vectrax.operator.builder.module_generator")


# ---------------------------------------------------------------------------
# Especificación de módulo a generar
# ---------------------------------------------------------------------------

@dataclass
class ModuleSpec:
    """Especificación de un módulo a generar."""
    name: str                           # Nombre del archivo (sin .py)
    purpose: str                        # Descripción del propósito
    layer_id: int = 1                   # Capa del operador
    module_path: str = ""               # e.g. "core.operator.xxx"
    classes: List[ClassSpec] = field(default_factory=list)
    functions: List[FunctionSpec] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    constants: List[ConstantSpec] = field(default_factory=list)
    include_singleton: bool = False
    singleton_name: str = ""            # e.g. "get_my_module"
    include_logger: bool = True
    include_future_annotations: bool = True


@dataclass
class ClassSpec:
    """Especificación de una clase a generar."""
    name: str
    bases: List[str] = field(default_factory=list)
    docstring: str = ""
    is_dataclass: bool = False
    is_frozen: bool = False
    is_enum: bool = False
    fields: List[FieldSpec] = field(default_factory=list)
    methods: List[FunctionSpec] = field(default_factory=list)
    include_to_dict: bool = True


@dataclass
class FunctionSpec:
    """Especificación de una función a generar."""
    name: str
    args: List[str] = field(default_factory=list)
    return_type: str = ""
    docstring: str = ""
    body: str = "pass"
    decorators: List[str] = field(default_factory=list)


@dataclass
class FieldSpec:
    """Especificación de un campo de dataclass."""
    name: str
    type: str = "str"
    default: str = '""'


@dataclass
class ConstantSpec:
    """Especificación de una constante."""
    name: str
    value: str
    comment: str = ""


# ---------------------------------------------------------------------------
# Resultado de generación
# ---------------------------------------------------------------------------

@dataclass
class GeneratedModule:
    """Resultado de la generación de un módulo."""
    spec: ModuleSpec
    source_code: str
    file_path: str          # Ruta propuesta relativa al proyecto
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "name": self.spec.name,
            "purpose": self.spec.purpose,
            "file_path": self.file_path,
            "lines": self.source_code.count("\n") + 1,
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# Module Generator
# ---------------------------------------------------------------------------

class ModuleGenerator:
    """
    Genera esqueletos de módulos Python coherentes con el codebase.

    Usage::

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="my_module",
            purpose="Does something useful",
            module_path="core.operator.my_module",
            include_singleton=True,
        )
        result = gen.generate(spec)
        print(result.source_code)
    """

    def generate(
        self,
        spec: ModuleSpec,
        target_dir: str = "core/operator",
    ) -> GeneratedModule:
        """
        Genera el código fuente para un módulo.

        Args:
            spec: Especificación del módulo.
            target_dir: Directorio destino (relativo al proyecto).

        Returns:
            GeneratedModule con el código fuente generado.
        """
        lines: List[str] = []

        # 1. Module docstring
        lines.append(self._gen_docstring(spec))

        # 2. Future annotations
        if spec.include_future_annotations:
            lines.append("from __future__ import annotations\n")

        # 3. Standard imports
        lines.append(self._gen_imports(spec))

        # 4. Logger
        if spec.include_logger:
            logger_name = spec.module_path or f"vectrax.{spec.name}"
            lines.append(f'logger = logging.getLogger("{logger_name}")\n')

        # 5. Constants
        if spec.constants:
            lines.append(self._gen_constants(spec.constants))

        # 6. Classes
        for cls in spec.classes:
            lines.append(self._gen_class(cls))

        # 7. Top-level functions
        for func in spec.functions:
            lines.append(self._gen_function(func))

        # 8. Singleton
        if spec.include_singleton:
            lines.append(self._gen_singleton(spec))

        source = "\n".join(lines)
        file_path = f"{target_dir}/{spec.name}.py"

        return GeneratedModule(
            spec=spec,
            source_code=source,
            file_path=file_path,
        )

    # ------------------------------------------------------------------
    # Generators
    # ------------------------------------------------------------------

    @staticmethod
    def _gen_docstring(spec: ModuleSpec) -> str:
        title = spec.name.replace("_", " ").title()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f'"""\nVectrax Core — {title}\n{"=" * (len(title) + 16)}\n{spec.purpose}\n\nCreado: {now}\nCreador: Mario Bravo Castro\n"""\n'

    @staticmethod
    def _gen_imports(spec: ModuleSpec) -> str:
        lines = []
        # Standard lib imports that are commonly needed
        std_imports = set()
        if spec.include_logger:
            std_imports.add("import logging")
        if any(c.is_dataclass for c in spec.classes):
            std_imports.add("from dataclasses import dataclass, field")
        if any(c.is_enum for c in spec.classes):
            std_imports.add("from enum import Enum")
        std_imports.add("from typing import Any, Dict, List, Optional")

        for imp in sorted(std_imports):
            lines.append(imp)

        if spec.imports:
            lines.append("")
            for imp in spec.imports:
                lines.append(imp)

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _gen_constants(constants: List[ConstantSpec]) -> str:
        lines = [
            "# ---------------------------------------------------------------------------",
            "# Constants",
            "# ---------------------------------------------------------------------------",
            "",
        ]
        for c in constants:
            if c.comment:
                lines.append(f"{c.name} = {c.value}  # {c.comment}")
            else:
                lines.append(f"{c.name} = {c.value}")
        lines.append("")
        return "\n".join(lines)

    def _gen_class(self, cls: ClassSpec) -> str:
        lines = [
            "# ---------------------------------------------------------------------------",
            f"# {cls.name}",
            "# ---------------------------------------------------------------------------",
            "",
        ]

        # Decorators
        if cls.is_dataclass:
            if cls.is_frozen:
                lines.append("@dataclass(frozen=True)")
            else:
                lines.append("@dataclass")

        # Class definition
        bases_str = ", ".join(cls.bases) if cls.bases else ""
        if cls.is_enum and not bases_str:
            bases_str = "str, Enum"
        if bases_str:
            lines.append(f"class {cls.name}({bases_str}):")
        else:
            lines.append(f"class {cls.name}:")

        # Docstring
        if cls.docstring:
            lines.append(f'    """{cls.docstring}"""')
        lines.append("")

        # Fields (for dataclass)
        if cls.is_dataclass and cls.fields:
            for f in cls.fields:
                lines.append(f"    {f.name}: {f.type} = {f.default}")
            lines.append("")

        # Methods
        if cls.methods:
            for method in cls.methods:
                method_lines = self._gen_method(method)
                lines.append(method_lines)
        elif cls.is_enum:
            lines.append("    pass")
        elif not cls.fields:
            lines.append("    pass")

        # to_dict
        if cls.include_to_dict and (cls.is_dataclass or cls.fields):
            lines.append(self._gen_to_dict(cls))

        lines.append("")
        return "\n".join(lines)

    def _gen_method(self, func: FunctionSpec) -> str:
        lines = []
        for dec in func.decorators:
            lines.append(f"    @{dec}")
        args_str = ", ".join(["self"] + func.args)
        ret = f" -> {func.return_type}" if func.return_type else ""
        lines.append(f"    def {func.name}({args_str}){ret}:")
        if func.docstring:
            lines.append(f'        """{func.docstring}"""')
        lines.append(f"        {func.body}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _gen_to_dict(cls: ClassSpec) -> str:
        lines = [
            "    def to_dict(self) -> Dict[str, Any]:",
            '        """Serialize to dict."""',
            "        return {",
        ]
        for f in cls.fields:
            lines.append(f'            "{f.name}": self.{f.name},')
        lines.append("        }")
        lines.append("")
        return "\n".join(lines)

    def _gen_function(self, func: FunctionSpec) -> str:
        lines = []
        for dec in func.decorators:
            lines.append(f"@{dec}")
        args_str = ", ".join(func.args)
        ret = f" -> {func.return_type}" if func.return_type else ""
        lines.append(f"def {func.name}({args_str}){ret}:")
        if func.docstring:
            lines.append(f'    """{func.docstring}"""')
        lines.append(f"    {func.body}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _gen_singleton(spec: ModuleSpec) -> str:
        name = spec.singleton_name or f"get_{spec.name}"
        # Infer class name from spec
        cls_name = spec.classes[0].name if spec.classes else spec.name.title().replace("_", "")
        var_name = f"_{spec.name}"

        return "\n".join([
            "# ---------------------------------------------------------------------------",
            "# Singleton",
            "# ---------------------------------------------------------------------------",
            "",
            f"_{var_name}: Optional[{cls_name}] = None",
            "",
            "",
            f"def {name}() -> {cls_name}:",
            f'    """Obtener la instancia singleton."""',
            f"    global _{var_name}",
            f"    if _{var_name} is None:",
            f"        _{var_name} = {cls_name}()",
            f"    return _{var_name}",
            "",
        ])
