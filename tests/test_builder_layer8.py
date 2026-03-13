"""
Tests — Capa 8: Autoconstrucción de Módulos
=============================================
Valida el pipeline completo: CodeReader → PatternExtractor →
ModuleGenerator → SandboxValidator → ModuleIntegrator → BuilderBridge.

Ningún test escribe archivos reales al disco (mode GUIDED).
"""

from __future__ import annotations

import os
import sys
import textwrap

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.expanduser("~/Vectrax"))


# ---------------------------------------------------------------------------
# 1. CodeReader
# ---------------------------------------------------------------------------

class TestCodeReader:
    """CodeReader debe leer y analizar archivos Python via AST."""

    def test_analyze_file_returns_module_analysis(self):
        from core.operator.builder.code_reader import CodeReader, ModuleAnalysis

        reader = CodeReader()
        result = reader.analyze_file("core/operator/builder/code_reader.py")
        assert isinstance(result, ModuleAnalysis)
        assert result.lines_total > 0
        assert len(result.classes) > 0, "code_reader.py contains classes"
        assert not result.errors

    def test_analyze_file_nonexistent(self):
        from core.operator.builder.code_reader import CodeReader

        reader = CodeReader()
        result = reader.analyze_file("nonexistent_file_12345.py")
        assert len(result.errors) > 0

    def test_analyze_directory(self):
        from core.operator.builder.code_reader import CodeReader

        reader = CodeReader()
        results = reader.analyze_directory("core/operator/builder")
        assert len(results) >= 6, f"Expected >=6 builder modules, got {len(results)}"
        for r in results:
            assert r.path.endswith(".py")

    def test_extracts_classes_and_functions(self):
        from core.operator.builder.code_reader import CodeReader

        reader = CodeReader()
        result = reader.analyze_file("core/operator/builder/sandbox_validator.py")
        class_names = [c.name for c in result.classes]
        assert "SandboxValidator" in class_names
        assert "ValidationResult" in class_names

    def test_detects_singleton_pattern(self):
        from core.operator.builder.code_reader import CodeReader

        reader = CodeReader()
        # universal_bus.py has a get_universal_bus() singleton
        result = reader.analyze_file("core/operator/universal_bus.py")
        assert result.has_singleton, "universal_bus.py should have singleton pattern"

    def test_detects_imports(self):
        from core.operator.builder.code_reader import CodeReader

        reader = CodeReader()
        result = reader.analyze_file("core/operator/builder/bridge.py")
        modules = [imp.module for imp in result.imports]
        assert "logging" in modules or "__future__" in modules


# ---------------------------------------------------------------------------
# 2. PatternExtractor
# ---------------------------------------------------------------------------

class TestPatternExtractor:
    """PatternExtractor debe detectar patrones del codebase."""

    def test_analyze_produces_report(self):
        from core.operator.builder.code_reader import CodeReader
        from core.operator.builder.pattern_extractor import PatternExtractor, PatternReport

        reader = CodeReader()
        ext = PatternExtractor(reader)
        report = ext.analyze("core/operator/builder")
        assert isinstance(report, PatternReport)
        assert report.modules_analyzed >= 6
        assert report.total_classes > 0

    def test_detects_dataclass_pattern(self):
        from core.operator.builder.code_reader import CodeReader
        from core.operator.builder.pattern_extractor import PatternExtractor

        reader = CodeReader()
        ext = PatternExtractor(reader)
        report = ext.analyze("core/operator/builder")
        assert report.dataclass_count > 5, "builder modules use many dataclasses"

    def test_detects_to_dict_pattern(self):
        from core.operator.builder.code_reader import CodeReader
        from core.operator.builder.pattern_extractor import PatternExtractor

        reader = CodeReader()
        ext = PatternExtractor(reader)
        report = ext.analyze("core/operator/builder")
        assert report.has_to_dict_count > 3

    def test_detected_patterns_is_list_of_strings(self):
        from core.operator.builder.code_reader import CodeReader
        from core.operator.builder.pattern_extractor import PatternExtractor

        reader = CodeReader()
        ext = PatternExtractor(reader)
        report = ext.analyze("core/operator/builder")
        assert isinstance(report.detected_patterns, list)
        for p in report.detected_patterns:
            assert isinstance(p, str)

    def test_to_dict_serializable(self):
        import json
        from core.operator.builder.code_reader import CodeReader
        from core.operator.builder.pattern_extractor import PatternExtractor

        reader = CodeReader()
        ext = PatternExtractor(reader)
        report = ext.analyze("core/operator/builder")
        d = report.to_dict()
        # Must be JSON-serializable
        json.dumps(d)


# ---------------------------------------------------------------------------
# 3. ModuleGenerator
# ---------------------------------------------------------------------------

class TestModuleGenerator:
    """ModuleGenerator debe generar esqueletos coherentes."""

    def test_generate_basic_module(self):
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec, GeneratedModule,
        )

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="test_new_module",
            purpose="Test module for validation",
            module_path="core.operator.test_new_module",
        )
        result = gen.generate(spec)
        assert isinstance(result, GeneratedModule)
        assert "test_new_module" in result.file_path
        assert "from __future__ import annotations" in result.source_code
        assert "logging" in result.source_code

    def test_generate_with_class(self):
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec, ClassSpec, FieldSpec,
        )

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="with_class",
            purpose="Module with a class",
            classes=[
                ClassSpec(
                    name="MyData",
                    is_dataclass=True,
                    docstring="A test data class",
                    fields=[
                        FieldSpec(name="value", type="str", default='""'),
                        FieldSpec(name="count", type="int", default="0"),
                    ],
                ),
            ],
        )
        result = gen.generate(spec)
        assert "class MyData" in result.source_code
        assert "@dataclass" in result.source_code
        assert "value: str" in result.source_code

    def test_generate_with_singleton(self):
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec, ClassSpec,
        )

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="my_service",
            purpose="Service with singleton",
            classes=[ClassSpec(name="MyService", docstring="A service")],
            include_singleton=True,
            singleton_name="get_my_service",
        )
        result = gen.generate(spec)
        assert "def get_my_service" in result.source_code
        assert "Optional[MyService]" in result.source_code

    def test_generated_code_is_valid_python(self):
        """Generated code must pass compile()."""
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec, ClassSpec, FieldSpec,
        )

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="compile_test",
            purpose="Test that generated code compiles",
            classes=[
                ClassSpec(
                    name="CompileTest",
                    is_dataclass=True,
                    fields=[FieldSpec(name="x", type="int", default="0")],
                ),
            ],
            include_singleton=True,
        )
        result = gen.generate(spec)
        # Must not raise SyntaxError
        compile(result.source_code, "<generated>", "exec")


# ---------------------------------------------------------------------------
# 4. SandboxValidator
# ---------------------------------------------------------------------------

class TestSandboxValidator:
    """SandboxValidator debe validar código sin ejecutarlo."""

    def test_valid_code_passes(self):
        from core.operator.builder.sandbox_validator import (
            SandboxValidator, ValidationResult,
        )

        validator = SandboxValidator()
        source = textwrap.dedent('''\
            """Test module."""
            from __future__ import annotations
            import logging

            logger = logging.getLogger("test")

            class Foo:
                """A foo."""
                pass
        ''')
        result = validator.validate(source, "core/operator/test_foo.py")
        assert isinstance(result, ValidationResult)
        assert result.passed
        assert result.checks_run >= 4

    def test_syntax_error_fails(self):
        from core.operator.builder.sandbox_validator import SandboxValidator

        validator = SandboxValidator()
        result = validator.validate("def broken(:", "core/test.py")
        assert not result.passed
        assert any(i.check == "syntax" for i in result.failures)

    def test_dangerous_call_fails(self):
        from core.operator.builder.sandbox_validator import SandboxValidator

        validator = SandboxValidator()
        source = textwrap.dedent('''\
            """Dangerous module."""
            from __future__ import annotations
            import os
            import logging

            logger = logging.getLogger("danger")

            os.system("rm -rf /")
        ''')
        result = validator.validate(source, "core/test_danger.py")
        assert not result.passed
        assert any(i.check == "safety" for i in result.failures)

    def test_protected_path_fails(self):
        from core.operator.builder.sandbox_validator import SandboxValidator

        validator = SandboxValidator()
        source = '"""ok"""\nfrom __future__ import annotations\n'
        result = validator.validate(source, "vault/steal_secrets.py")
        assert not result.passed
        assert any(i.check == "path_safety" for i in result.failures)

    def test_missing_docstring_warns(self):
        from core.operator.builder.sandbox_validator import SandboxValidator

        validator = SandboxValidator()
        source = "from __future__ import annotations\nx = 1\n"
        result = validator.validate(source, "core/test_no_doc.py")
        # Should pass but with a warning
        assert result.passed
        assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# 5. ModuleIntegrator (dry-run only)
# ---------------------------------------------------------------------------

class TestModuleIntegrator:
    """ModuleIntegrator debe planificar integraciones sin ejecutar."""

    def test_validate_only(self):
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec,
        )
        from core.operator.builder.integrator import ModuleIntegrator

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="integration_test",
            purpose="Test integration pipeline",
            module_path="core.operator.integration_test",
        )
        module = gen.generate(spec)

        integrator = ModuleIntegrator()
        result = integrator.validate_only(module)
        assert result.passed

    def test_integrate_without_approval_returns_approval_required(self):
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec,
        )
        from core.operator.builder.integrator import (
            ModuleIntegrator, IntegrationStatus,
        )

        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="no_approval_test",
            purpose="Should require approval",
            module_path="core.operator.no_approval_test",
        )
        module = gen.generate(spec)

        integrator = ModuleIntegrator()
        result = integrator.integrate(module, approve=False)
        assert result.status == IntegrationStatus.APPROVAL_REQUIRED

    def test_integrate_existing_file_returns_conflict(self):
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec, GeneratedModule,
        )
        from core.operator.builder.integrator import (
            ModuleIntegrator, IntegrationStatus,
        )

        # This file already exists
        module = GeneratedModule(
            spec=ModuleSpec(name="__init__", purpose="conflict test"),
            source_code='"""exists"""\nfrom __future__ import annotations\n',
            file_path="core/operator/builder/__init__.py",
        )
        integrator = ModuleIntegrator()
        result = integrator.integrate(module, approve=True, overwrite=False)
        assert result.status == IntegrationStatus.CONFLICT


# ---------------------------------------------------------------------------
# 6. BuilderBridge
# ---------------------------------------------------------------------------

class TestBuilderBridge:
    """BuilderBridge debe conectarse al bus sin errores."""

    def test_instantiation(self):
        from core.operator.builder.bridge import BuilderBridge

        bridge = BuilderBridge()
        assert bridge._analysis_count == 0

    def test_subscribe_to_bus(self):
        from core.operator.builder.bridge import BuilderBridge
        from core.operator.universal_bus import UniversalBus

        bus = UniversalBus(ledger_enabled=False)
        bridge = BuilderBridge()
        bridge.subscribe(bus)
        assert len(bridge._subscription_ids) == 2

    def test_status(self):
        from core.operator.builder.bridge import BuilderBridge

        bridge = BuilderBridge()
        status = bridge.status()
        assert "analysis_count" in status
        assert status["analysis_count"] == 0

    def test_bus_event_triggers_analysis(self):
        """Verify that a cycle_complete event triggers the builder analysis."""
        from core.operator.builder.bridge import BuilderBridge
        from core.operator.universal_bus import (
            UniversalBus, Channels, EventPriority,
        )

        bus = UniversalBus(ledger_enabled=False)
        bridge = BuilderBridge()
        bridge._min_interval = 0  # disable rate limit for test
        bridge.subscribe(bus)

        # Emit a cycle_complete event that should trigger analysis
        bus.emit(
            channel=Channels.NUCLEUS,
            event_type="cycle_complete",
            source_layer=1,
            priority=EventPriority.NORMAL,
            payload={"cycle_id": "TEST-001", "outcome": "proposed"},
        )

        # The bridge should have attempted at least one analysis
        assert bridge._analysis_count >= 1


# ---------------------------------------------------------------------------
# 7. End-to-End Pipeline
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    """Pipeline completo: read → extract → generate → validate → plan."""

    def test_full_pipeline_without_writing(self):
        """
        Execute the full Layer 8 pipeline:
        1. Read existing code
        2. Extract patterns
        3. Generate a new module based on patterns
        4. Validate the generated module
        5. Check that integration would require approval (GUIDED mode)
        """
        from core.operator.builder.code_reader import CodeReader
        from core.operator.builder.pattern_extractor import PatternExtractor
        from core.operator.builder.module_generator import (
            ModuleGenerator, ModuleSpec, ClassSpec, FieldSpec,
        )
        from core.operator.builder.sandbox_validator import SandboxValidator
        from core.operator.builder.integrator import (
            ModuleIntegrator, IntegrationStatus,
        )

        # 1. Read
        reader = CodeReader()
        modules = reader.analyze_directory("core/operator/builder")
        assert len(modules) >= 6

        # 2. Extract patterns
        extractor = PatternExtractor(reader)
        report = extractor.analyze_modules(modules)
        assert report.total_classes > 10
        assert len(report.detected_patterns) > 0

        # 3. Generate module following detected patterns
        gen = ModuleGenerator()
        spec = ModuleSpec(
            name="auto_constructed_example",
            purpose="Module generated by Layer 8 end-to-end test",
            module_path="core.operator.auto_constructed_example",
            layer_id=8,
            classes=[
                ClassSpec(
                    name="AutoConstructedResult",
                    is_dataclass=True,
                    docstring="Result of an auto-construction operation.",
                    fields=[
                        FieldSpec(name="name", type="str", default='""'),
                        FieldSpec(name="success", type="bool", default="False"),
                        FieldSpec(name="score", type="float", default="0.0"),
                    ],
                    include_to_dict=True,
                ),
            ],
            include_singleton=False,
            include_logger=True,
            include_future_annotations=True,
        )
        generated = gen.generate(spec)
        assert "class AutoConstructedResult" in generated.source_code
        assert "from __future__ import annotations" in generated.source_code

        # 4. Validate
        validator = SandboxValidator()
        validation = validator.validate(generated.source_code, generated.file_path)
        assert validation.passed, f"Validation failed: {[i.message for i in validation.failures]}"

        # 5. Plan integration (without executing)
        integrator = ModuleIntegrator()
        result = integrator.integrate(generated, approve=False)
        assert result.status == IntegrationStatus.APPROVAL_REQUIRED
        assert "approval" in result.message.lower()

    def test_nucleus_layer8_exists(self):
        """Verify that nucleus correctly declares Layer 8 as ACTIVE."""
        from core.operator.nucleus import LAYERS, LayerStatus

        layer8 = next((l for l in LAYERS if l.id == 8), None)
        assert layer8 is not None, "Layer 8 not found in nucleus LAYERS"
        assert layer8.name == "Autoconstrucción de Módulos"
        assert layer8.status == LayerStatus.ACTIVE
        assert len(layer8.modules) == 5
        assert "core.operator.builder.code_reader" in layer8.modules
        assert "core.operator.builder.integrator" in layer8.modules

    def test_bus_channel_exists(self):
        """Verify that the builder bus channel is defined."""
        from core.operator.universal_bus import Channels

        assert hasattr(Channels, "BUILDER")
        assert Channels.BUILDER == "layer.8.builder"
        assert Channels.for_layer(8) == "layer.8.builder"
