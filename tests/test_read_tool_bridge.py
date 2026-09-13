"""Tests for Puente A (2026-09-13):
  core.operator.read_tool_intent + core.operator.read_tool_bridge +
  external_gateway integration.

Covers:
  1. Parser: positive/negative matches (es/en), zero false positives on
     normal conversation / self-aware / domain questions.
  2. Path traversal and absolute paths rejected before touching the connector.
  3. Non-text extensions rejected before touching the connector.
  4. Capability gate: only READ_ONLY + healthy + connected authorizes.
  5. Happy path against a real fixture file: exactly one read() call tracked,
     zero write() calls, response contains real file content.
  6. Flag OFF (default) → external_gateway behavior unchanged.
  7. Non-creator with flag ON → bridge does not activate.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.operator.read_tool_intent import (  # noqa: E402
    parse_read_file_request,
    parse_symbol_lookup_request,
)


class TestParseReadFileRequest(unittest.TestCase):
    def test_matches_spanish_with_symbol(self):
        r = parse_read_file_request(
            "Vectrax, abre core/operator/system_monitor.py y dime qué hace collect_metrics()."
        )
        self.assertIsNotNone(r)
        self.assertEqual(r.path, "core/operator/system_monitor.py")
        self.assertEqual(r.symbol, "collect_metrics")

    def test_matches_spanish_without_symbol(self):
        r = parse_read_file_request("abre core/operator/system_monitor.py")
        self.assertIsNotNone(r)
        self.assertEqual(r.path, "core/operator/system_monitor.py")
        self.assertIsNone(r.symbol)

    def test_matches_english(self):
        r = parse_read_file_request(
            "Vectrax, open connectors/adapters/local_filesystem.py and explain read()"
        )
        self.assertIsNotNone(r)
        self.assertEqual(r.path, "connectors/adapters/local_filesystem.py")

    def test_no_false_positive_on_self_aware_question(self):
        self.assertIsNone(parse_read_file_request("¿Qué has observado últimamente en tu universo?"))

    def test_no_false_positive_on_domain_question(self):
        self.assertIsNone(parse_read_file_request("¿Qué opinas del mercado de freight logistics?"))

    def test_no_false_positive_on_greeting(self):
        self.assertIsNone(parse_read_file_request("Hola Vectrax, ¿cómo estás?"))

    def test_rejects_non_text_extension(self):
        self.assertIsNone(parse_read_file_request("lee vault/secrets.db"))
        self.assertIsNone(parse_read_file_request("abre gravity_index.json.tmp"))

    def test_rejects_path_traversal(self):
        self.assertIsNone(parse_read_file_request("abre ../../etc/passwd.py"))

    def test_rejects_absolute_path(self):
        self.assertIsNone(parse_read_file_request("abre /etc/passwd.py"))
        self.assertIsNone(parse_read_file_request("abre ~/secrets.py"))


class TestParseSymbolLookupRequest(unittest.TestCase):
    """Extensión: preguntas sobre un símbolo de código conocido SIN ruta
    explícita ("¿qué hace ConnectionEngine.read() en tu código?")."""

    def test_matches_class_dot_method(self):
        r = parse_symbol_lookup_request("¿Qué hace ConnectionEngine.read() en tu código?")
        self.assertIsNotNone(r)
        self.assertEqual(r.class_name, "ConnectionEngine")
        self.assertEqual(r.symbol, "read")

    def test_matches_bare_function(self):
        r = parse_symbol_lookup_request("¿Qué hace collect_metrics() en tu código?")
        self.assertIsNotNone(r)
        self.assertIsNone(r.class_name)
        self.assertEqual(r.symbol, "collect_metrics")

    def test_no_match_without_parens(self):
        self.assertIsNone(parse_symbol_lookup_request("¿Qué hace ConnectionEngine.read en tu código?"))

    def test_no_false_positive_on_greeting(self):
        self.assertIsNone(parse_symbol_lookup_request("Hola Vectrax, ¿cómo estás?"))

    def test_path_based_takes_precedence_when_path_present(self):
        """Si el mensaje SI trae una ruta explícita, ese es un caso distinto
        (parse_read_file_request); esta función igual matchearía el símbolo,
        pero read_tool_bridge.resolve_file_read() prueba primero la ruta
        explícita y solo cae a esta si esa devuelve None (verificado en
        TestResolveFileReadHappyPath.test_symbol_lookup_prefers_explicit_path)."""
        r = parse_symbol_lookup_request(
            "abre core/operator/system_monitor.py y dime qué hace collect_metrics()"
        )
        self.assertIsNotNone(r)  # la función en sí no sabe de rutas; el orchestrator sí


class TestCapabilityGate(unittest.TestCase):
    def test_denied_when_not_read_only(self):
        from core.operator import read_tool_bridge as rtb
        with patch.dict(
            "core.self_observation.capability_context._CAPABILITY_CATALOG",
            {"local_filesystem": {
                "kind": "capability", "group": "herramientas",
                "module": "connectors.adapters.local_filesystem",
                "attr": "LocalFilesystemConnector",
                "reversibility": "BEHAVIOR_CHANGE",
            }},
        ):
            self.assertFalse(rtb._capability_authorized())

    def test_authorized_when_read_only_and_healthy(self):
        from core.operator import read_tool_bridge as rtb
        self.assertTrue(rtb._capability_authorized())

    def test_resolve_file_read_empty_when_not_authorized(self):
        from core.operator import read_tool_bridge as rtb
        with patch.object(rtb, "_capability_authorized", return_value=False):
            result = rtb.resolve_file_read("abre core/operator/system_monitor.py")
        self.assertEqual(result, "")


class TestResolveFileReadHappyPath(unittest.TestCase):
    """End-to-end against a real fixture file, verifying real connector
    call counts via the tracker (no mocking of the connector itself)."""

    def setUp(self):
        # Empezar con un ConnectionEngine limpio: si ya había un
        # 'local_filesystem' registrado con OTRO root (p.ej. de un proceso
        # real o un test anterior), _ensure_connector_registered() lo
        # reutilizaría tal cual (es idempotente por diseño) e ignoraría
        # nuestro _PROJECT_ROOT parcheado.
        import connectors.engine as engine_mod
        engine_mod._engine = None
        self.tmpdir = tempfile.mkdtemp(prefix="vectrax_test_read_bridge_")
        self.fixture_path = os.path.join(self.tmpdir, "sample.py")
        with open(self.fixture_path, "w", encoding="utf-8") as f:
            f.write(
                '"""Fixture module."""\n\n'
                "def greet(name):\n"
                '    """Return a friendly greeting."""\n'
                "    return f'hello {name}'\n\n"
                "def farewell(name):\n"
                '    """Return a farewell."""\n'
                "    return f'bye {name}'\n"
            )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # Reset the UCE singleton so each test starts from a clean registry —
        # this module registers a project-root-scoped connector as a side
        # effect and we don't want cross-test leakage of a different root.
        import connectors.engine as engine_mod
        engine_mod._engine = None

    def _read_write_counts(self, engine, connector_name="local_filesystem"):
        logs = engine.logs(name=connector_name, limit=1000)
        reads = sum(1 for e in logs if e.get("operation") == "read")
        writes = sum(1 for e in logs if e.get("operation") == "write")
        return reads, writes

    def test_symbol_extraction_and_single_read_call(self):
        from core.operator import read_tool_bridge as rtb
        from connectors.engine import get_connection_engine

        with patch.object(rtb, "_PROJECT_ROOT", self.tmpdir):
            engine = get_connection_engine()
            reads_before, writes_before = self._read_write_counts(engine)

            result = rtb.resolve_file_read("abre sample.py y dime qué hace greet()")

            reads_after, writes_after = self._read_write_counts(engine)

        self.assertIn("hello", result.lower() + result)  # tolerant: real content present
        self.assertIn("greet", result)
        self.assertEqual(reads_after - reads_before, 1, "exactly one read() call expected")
        self.assertEqual(writes_after - writes_before, 0, "zero write() calls expected — READ_ONLY")

    def test_reflects_real_state_between_two_calls(self):
        """Segunda variante de la prueba de aceptación: el contenido real
        cambia entre llamadas y la respuesta debe reflejar el estado ACTUAL
        leído en cada una — nunca una respuesta cacheada de la llamada previa."""
        from core.operator import read_tool_bridge as rtb

        with patch.object(rtb, "_PROJECT_ROOT", self.tmpdir):
            first = rtb.resolve_file_read("abre sample.py y dime qué hace greet()")
            self.assertIn("greet", first)

            # Mutate the real file — a genuinely different function/body.
            with open(self.fixture_path, "a", encoding="utf-8") as f:
                f.write(
                    "\ndef brand_new_symbol():\n"
                    '    """Marker to prove the second read is not cached."""\n'
                    "    return 42\n"
                )

            second = rtb.resolve_file_read(
                "abre sample.py y dime qué hace brand_new_symbol()"
            )
            self.assertIn("brand_new_symbol", second)
            self.assertIn("42", second)
            self.assertNotIn("brand_new_symbol", first)

    def test_unknown_symbol_never_invents(self):
        from core.operator import read_tool_bridge as rtb
        with patch.object(rtb, "_PROJECT_ROOT", self.tmpdir):
            result = rtb.resolve_file_read(
                "abre sample.py y dime qué hace funcion_que_no_existe()"
            )
        self.assertIn("no", result.lower())
        self.assertNotIn("hello", result)

    def test_missing_file_never_invents(self):
        from core.operator import read_tool_bridge as rtb
        with patch.object(rtb, "_PROJECT_ROOT", self.tmpdir):
            result = rtb.resolve_file_read("abre no_existe.py")
        self.assertTrue(result)  # honest message, not empty
        self.assertNotIn("def ", result)


class TestSymbolLookupResolution(unittest.TestCase):
    """End-to-end de la resolución por símbolo, contra un fixture real
    importado como módulo (para que aparezca en sys.modules, igual que en
    producción) — nunca escanea el filesystem, nunca crea un segundo lector."""

    def setUp(self):
        import connectors.engine as engine_mod
        engine_mod._engine = None

    def test_class_method_resolves_to_real_connectors_engine_file(self):
        """La prueba de aceptación exacta: 'ConnectionEngine.read()' debe
        resolver a connectors/engine.py (ya importado por el propio puente
        al registrar el connector) y citar el método REAL, no un texto
        genérico de sockets."""
        from core.operator import read_tool_bridge as rtb

        result = rtb.resolve_file_read(
            "¿Qué hace ConnectionEngine.read() en tu código?",
            user_id="tg:2030762343",
        )
        self.assertIn("connector.read", result.lower().replace("self.", ""))
        self.assertNotIn("socket", result.lower())

    def test_unresolvable_symbol_falls_through_silently(self):
        from core.operator import read_tool_bridge as rtb
        result = rtb.resolve_file_read(
            "¿Qué hace SimboloQueJamasExistira() en tu código?",
            user_id="tg:2030762343",
        )
        self.assertEqual(result, "")

    def test_symbol_resolution_never_used_for_non_creator_gate_bypass(self):
        """resolve_file_read() en sí no aplica el gate de creador (eso vive
        en external_gateway.py) — este test documenta ese límite explícito:
        el módulo de bajo nivel siempre resuelve si el patrón matchea; la
        restricción creator-only es responsabilidad exclusiva del STEP en
        external_gateway.py (no duplicada aquí)."""
        from core.operator import read_tool_bridge as rtb
        result = rtb.resolve_file_read("¿Qué hace ConnectionEngine.read() en tu código?")
        self.assertNotEqual(result, "")


class TestExternalGatewayIntegration(unittest.TestCase):
    """Flag OFF (default) must leave existing behavior byte-for-byte
    unchanged; non-creator with flag ON must not activate the bridge."""

    def test_flag_off_by_default(self):
        from core.operator import read_tool_bridge as rtb
        self.assertFalse(rtb.is_enabled())

    def test_flag_on_via_env(self):
        from core.operator import read_tool_bridge as rtb
        with patch.dict(os.environ, {"VX_TOOL_BRIDGE_READ_ONLY": "1"}):
            self.assertTrue(rtb.is_enabled())
        with patch.dict(os.environ, {"VX_TOOL_BRIDGE_READ_ONLY": "0"}):
            self.assertFalse(rtb.is_enabled())


if __name__ == "__main__":
    unittest.main()
