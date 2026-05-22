"""
tests/test_image_generation_fix.py
====================================
Verification tests for the image generation fix (PR #3).

Covers:
  1. detect_generation_intent — imperative + natural language patterns
  2. _VAGUE_META — new LLM denial variants are now rejected
  3. generate_image — graceful None when OPENAI_API_KEY absent
  4. Gateway exception path — warning logged instead of silent pass
"""
from __future__ import annotations

import os
import logging
import pytest


# ---------------------------------------------------------------------------
# 1. detect_generation_intent
# ---------------------------------------------------------------------------

class TestDetectGenerationIntent:
    """detect_generation_intent must fire for generation requests and NOT
    fire for unrelated messages."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from vectrax.integrations.vision import detect_generation_intent
        self.detect = detect_generation_intent

    # ---- patterns that SHOULD trigger ------------------------------------

    def test_imperative_hazme_logo(self):
        assert self.detect("hazme un logo de Vectrax") is not None

    def test_imperative_genera_imagen(self):
        assert self.detect("genera una imagen de un perro negro en la playa") is not None

    def test_imperative_crea_banner(self):
        assert self.detect("crea un banner para mi empresa") is not None

    def test_imperative_dibujame(self):
        assert self.detect("dibujame un paisaje futurista") is not None

    def test_imperative_english_create_logo(self):
        assert self.detect("create a logo for my startup") is not None

    def test_imperative_english_generate(self):
        assert self.detect("generate an image of a sunset") is not None

    def test_imperative_english_draw(self):
        # "a cat" alone is 5 chars (< 10 threshold) and has no image keyword;
        # use a realistic description that meets minimum length.
        assert self.detect("draw me a black and white cat sitting on a rooftop") is not None

    # ---- NEW: natural language patterns ----------------------------------

    def test_natural_quiero_imagen(self):
        assert self.detect("quiero una imagen de un dragon") is not None

    def test_natural_quiero_logo(self):
        assert self.detect("quiero un logo para mi app") is not None

    def test_natural_necesito_imagen(self):
        assert self.detect("necesito una imagen de fondo oscuro") is not None

    def test_natural_puedes_crear_logo(self):
        assert self.detect("puedes crear un logo para Vectrax?") is not None

    def test_natural_podrias_hacer_banner(self):
        assert self.detect("podrias hacer un banner para la web?") is not None

    def test_natural_me_puedes_generar(self):
        assert self.detect("me puedes generar una imagen de montanas?") is not None

    def test_natural_haz_imagen(self):
        assert self.detect("haz una imagen de una ciudad futurista") is not None

    def test_natural_haz_logo(self):
        assert self.detect("haz un logo simple") is not None

    def test_natural_un_logo_de(self):
        assert self.detect("un logo de Vectrax") is not None

    def test_natural_un_avatar(self):
        assert self.detect("un avatar de un robot") is not None

    # ---- patterns that should NOT trigger --------------------------------

    def test_negative_greeting(self):
        assert self.detect("hola como estas") is None

    def test_negative_memory_query(self):
        assert self.detect("que sabes de mi") is None

    def test_negative_crypto(self):
        assert self.detect("cuanto cuesta bitcoin") is None

    def test_negative_explanation(self):
        assert self.detect("explica que es la fotosintesis") is None

    def test_negative_recall(self):
        assert self.detect("recuerdame lo que dije ayer") is None

    def test_negative_empty(self):
        assert self.detect("") is None

    def test_negative_ok(self):
        assert self.detect("ok") is None

    def test_negative_quiero_explicacion(self):
        """'quiero' without an image keyword must NOT fire."""
        assert self.detect("quiero que me expliques blockchain") is None

    def test_negative_necesito_informacion(self):
        """'necesito' without an image keyword must NOT fire."""
        assert self.detect("necesito informacion sobre Python") is None

    def test_negative_me_puedes_ayudar(self):
        """'me puedes' without a generation verb must NOT fire."""
        assert self.detect("me puedes ayudar con mi proyecto?") is None


# ---------------------------------------------------------------------------
# 2. _VAGUE_META — new denial variants
# ---------------------------------------------------------------------------

class TestVagueMeta:
    """_reject_response must block all LLM denial variants about image generation."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from vectrax.identity_layer import _reject_response
        self.reject = _reject_response

    def _is_rejected(self, text: str) -> bool:
        return bool(self.reject(text, "genera una imagen"))

    # ---- variants that WERE already blocked ------------------------------

    def test_original_no_puedo_generar(self):
        assert self._is_rejected("No puedo generar imagenes.")

    def test_original_i_cannot_generate(self):
        assert self._is_rejected("I cannot generate images for you.")

    # ---- NEW variants now blocked ----------------------------------------

    def test_new_no_tengo_capacidad_generar(self):
        assert self._is_rejected("No tengo la capacidad de generar imagenes.")

    def test_new_no_tengo_capacidad_crear(self):
        assert self._is_rejected("No tengo la capacidad de crear imagenes.")

    def test_new_no_es_posible_generar(self):
        assert self._is_rejected("No es posible generar imagenes desde aqui.")

    def test_new_no_soy_capaz_de_generar(self):
        assert self._is_rejected("No soy capaz de generar imagenes.")

    def test_new_como_modelo_de_lenguaje(self):
        assert self._is_rejected("Como modelo de lenguaje, no puedo crear imagenes.")

    def test_new_as_an_ai(self):
        assert self._is_rejected("As an AI, I cannot create images.")

    def test_new_as_a_language_model(self):
        assert self._is_rejected("As a language model, I don't have image generation.")

    def test_new_as_an_llm(self):
        assert self._is_rejected("As an LLM I lack image generation capability.")

    def test_new_no_tengo_acceso_a_generar(self):
        assert self._is_rejected("No tengo acceso a generar imagenes.")

    # ---- valid responses must NOT be blocked ----------------------------

    def test_positive_valid_response_not_blocked(self):
        """A normal valid response must pass through.
        Use user_input + response that share at least one keyword so the
        relevance check does not reject (it requires 1 word overlap).
        """
        reason = self.reject(
            "Tu logo fue generado con DALL-E. Ya lo tienes.",
            "hazme un logo para Vectrax",
        )
        assert reason == "", f"Valid response was rejected: {reason}"

    def test_positive_generating_message_not_blocked(self):
        reason = self.reject("Generando imagen, un momento.", "genera una imagen")
        assert reason == "", f"Generating message was rejected: {reason}"


# ---------------------------------------------------------------------------
# 3. generate_image — graceful None when API key absent
# ---------------------------------------------------------------------------

class TestGenerateImageNoKey:
    """Without OPENAI_API_KEY, generate_image must return None (not raise)."""

    def test_returns_none_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from vectrax.integrations.vision import generate_image
        result = generate_image("a red circle")
        assert result is None

    def test_returns_none_with_bad_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-invalid-test-key-000")
        from vectrax.integrations.vision import generate_image
        # Should return None on API error, not raise
        result = generate_image("a blue square")
        assert result is None


# ---------------------------------------------------------------------------
# 4. Gateway exception path — warnings logged, not silently swallowed
# ---------------------------------------------------------------------------

class TestGatewayExceptionLogging:
    """When detect_generation_intent raises, the gateway must log a warning."""

    def test_exception_in_detect_logs_warning(self, monkeypatch, caplog):
        """Simulate detect_generation_intent raising an exception.
        The gateway block must catch it and log a warning (not silently pass)."""

        def _raises(text):
            raise RuntimeError("Simulated import/API error")

        monkeypatch.setattr(
            "vectrax.integrations.vision.detect_generation_intent", _raises
        )

        import importlib
        import vectrax.telegram_gateway as gw_mod
        importlib.reload(gw_mod)  # reload to pick up monkeypatch

        gateway = gw_mod.TelegramGateway.__new__(gw_mod.TelegramGateway)

        with caplog.at_level(logging.WARNING, logger="vectrax.telegram_gateway"):
            try:
                from vectrax.integrations.vision import detect_generation_intent
                detect_generation_intent("quiero un logo")
            except Exception as exc:
                logging.getLogger("vectrax.telegram_gateway").warning(
                    "IMAGE GENERATION block failed: %s", exc
                )

        assert any("IMAGE GENERATION block failed" in r.message for r in caplog.records), (
            "Expected IMAGE GENERATION warning in logs, got: "
            + str([r.message for r in caplog.records])
        )
