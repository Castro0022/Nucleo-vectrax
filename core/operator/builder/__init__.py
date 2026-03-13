"""
Vectrax Core — Builder (Autoconstrucción de Módulos)
======================================================
Subsistema de autoconstrucción: lee código, extrae patrones,
genera módulos, valida en sandbox, e integra controladamente.

Pipeline completo::

    from core.operator.builder import (
        CodeReader, PatternExtractor, ModuleGenerator,
        SandboxValidator, ModuleIntegrator,
    )

    # 1. Leer y analizar
    reader = CodeReader()
    analysis = reader.analyze_file("core/governor.py")

    # 2. Extraer patrones
    extractor = PatternExtractor(reader)
    patterns = extractor.analyze("core/operator")

    # 3. Generar módulo
    gen = ModuleGenerator()
    module = gen.generate(spec)

    # 4. Validar
    validator = SandboxValidator()
    validation = validator.validate(module.source_code, module.file_path)

    # 5. Integrar (requiere aprobación)
    integrator = ModuleIntegrator()
    result = integrator.integrate(module, approve=True)

Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from core.operator.builder.code_reader import (
    CodeReader,
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    ModuleAnalysis,
)
from core.operator.builder.pattern_extractor import (
    PatternExtractor,
    PatternReport,
)
from core.operator.builder.module_generator import (
    ClassSpec,
    ConstantSpec,
    FieldSpec,
    FunctionSpec,
    GeneratedModule,
    ModuleGenerator,
    ModuleSpec,
)
from core.operator.builder.sandbox_validator import (
    SandboxValidator,
    ValidationIssue,
    ValidationLevel,
    ValidationResult,
)
from core.operator.builder.integrator import (
    IntegrationResult,
    IntegrationStatus,
    ModuleIntegrator,
)
from core.operator.builder.bridge import BuilderBridge

__all__ = [
    # Code Reader
    "CodeReader",
    "ClassInfo",
    "FunctionInfo",
    "ImportInfo",
    "ModuleAnalysis",
    # Pattern Extractor
    "PatternExtractor",
    "PatternReport",
    # Module Generator
    "ClassSpec",
    "ConstantSpec",
    "FieldSpec",
    "FunctionSpec",
    "GeneratedModule",
    "ModuleGenerator",
    "ModuleSpec",
    # Sandbox Validator
    "SandboxValidator",
    "ValidationIssue",
    "ValidationLevel",
    "ValidationResult",
    # Integrator
    "IntegrationResult",
    "IntegrationStatus",
    "ModuleIntegrator",
    # Bus bridge
    "BuilderBridge",
]
