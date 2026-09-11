"""
Tests — Motor de Criterio Aprendido (core/learn/criterion.py).

Cubre: detección de pedido de opinión, detección de dominio, ranking por
métricas reales, opinión grounded que expresa preferencia (no fija), abstención
constructiva ante entidades ausentes (caso route_A), y el verificador +
fallback determinista cuando el LLM no está grounded.

Run:  python -m pytest tests/test_criterion.py -v
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.learn import criterion as C


def _prior(pt, wr, e, n, conf="HIGH"):
    return types.SimpleNamespace(
        pattern_type=pt, win_rate=wr, expectancy=e, sample_size=n,
        confidence=conf, contributing_tenants=2,
    )


def _grav(intent, hits, cc=0.9, tier="HOT"):
    return types.SimpleNamespace(
        intent=intent, hits=hits, cc_score=cc, tier=tier, fingerprint="fp", summary="",
    )


class _FakeGI:
    def __init__(self, by_domain_map=None, domain_stats=None):
        self._bd = by_domain_map or {}
        self._ds = domain_stats or {}

    def by_domain(self, d):
        return self._bd.get(d, [])

    def domain_stats(self):
        return self._ds


def _patch_stores(priors_map, domains, gi):
    return [
        patch("core.domain_knowledge.list_domains", return_value=domains),
        patch("core.domain_knowledge.get_domain_priors",
              side_effect=lambda d: priors_map.get(d, [])),
        patch("core.domain_knowledge.get_domain_summary",
              side_effect=lambda d: {"domain": d, "total_observations": 1000}),
        patch("core.learn.gravity_engine.get_gravity_index", return_value=gi),
    ]


@pytest.fixture(autouse=True)
def _criterion_llm_offline(monkeypatch):
    """Por defecto, la presentación LLM del criterio NO está disponible (sin red).

    Los tests que ejercen el render inyectan `llm=...` (que no pasa por
    `complete`) o parchean `core.llm_call.complete` explícitamente. Así los tests
    sin LLM son deterministas y herméticos aunque el entorno tenga OPENAI_API_KEY.
    """
    from core import llm_call
    monkeypatch.setattr(
        llm_call, "complete",
        lambda *a, **k: llm_call.LLMResult(False, "", "no_key"),
    )
    yield


# ── 1. Detección de pedido de opinión/criterio ────────────────────────────

def test_detect_criterion_request():
    assert C.detect_criterion_request("¿qué opinas de la bolsa?")
    assert C.detect_criterion_request("según lo que aprendiste, ¿qué es mejor?")
    assert C.detect_criterion_request("compara los patrones de logística")
    assert C.detect_criterion_request("¿cuál preferirías?")
    assert C.detect_criterion_request("¿qué has aprendido?")
    assert C.detect_criterion_request("What have you learned?")
    assert not C.detect_criterion_request("hola, ¿cómo estás?")
    assert not C.detect_criterion_request("guarda esta nota")


# ── 1b. is_learning_inquiry() — sub-caso "¿qué has aprendido?" ────────────────

def test_is_learning_inquiry_positive():
    assert C.is_learning_inquiry("¿Qué has aprendido?")
    assert C.is_learning_inquiry("¿Qué aprendiste?")
    assert C.is_learning_inquiry("What have you learned?")
    assert C.is_learning_inquiry("¿Qué has aprendido del mercado?")
    assert C.is_learning_inquiry("qué has aprendido de ciberseguridad")


def test_is_learning_inquiry_negative():
    # Pedidos de opinión genéricos NO son "is_learning_inquiry" (son un eje
    # distinto: ése conserva el fallback a strongest_domain()).
    assert not C.is_learning_inquiry("¿Qué opinas?")
    assert not C.is_learning_inquiry("¿cuál preferirías?")
    assert not C.is_learning_inquiry("")
    assert not C.is_learning_inquiry("hola, ¿cómo estás?")


def test_learning_inquiry_without_topic_has_no_topic_tokens():
    # Regresión del defecto: las formas verbales de "aprendiste/has aprendido/
    # have/learned" NO deben filtrarse como si fueran el TEMA concreto de la
    # pregunta (antes "has"/"aprendiste" colaban como topic_tokens espurios,
    # impidiendo detectar la ausencia real de tema).
    assert C.extract_topic_tokens("¿Qué has aprendido?") == []
    assert C.extract_topic_tokens("¿Qué aprendiste?") == []
    assert C.extract_topic_tokens("What have you learned?") == []


def test_learning_inquiry_with_domain_keeps_topic_tokens_empty_but_domain_detected():
    # El dominio nombrado (mercado/ciberseguridad/freight) se resuelve por
    # `_resolve_query_domain`/`detect_domain`, no por `extract_topic_tokens`
    # (los indicadores de dominio se descartan a propósito ahí). Aquí solo
    # confirmamos que la ausencia de VERBOS de aprendizaje como topic_tokens
    # espurios se mantiene con dominio presente en la frase.
    assert C.extract_topic_tokens("¿Qué has aprendido del mercado?") == []
    assert C.extract_topic_tokens("qué has aprendido de freight") == []


# ── 2. Detección de dominio ───────────────────────────────────────────────

def test_detect_domain():
    gi = _FakeGI(domain_stats={"market": {}, "freight_logistics": {}})
    with patch("core.domain_knowledge.list_domains",
               return_value=["market", "freight_logistics"]), \
         patch("core.learn.gravity_engine.get_gravity_index", return_value=gi):
        assert C.detect_domain("¿qué opinas de la bolsa y las acciones?") == "market"
        assert C.detect_domain("¿cuál es la mejor carga en logística?") == "freight_logistics"
        assert C.detect_domain("háblame del clima") is None


# ── 3. Ranking por métricas reales ────────────────────────────────────────

def test_rank_orders_by_metrics():
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("WEAKONE", 60.0, 1.0, 20, "LOW"),
    ]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        ranked = C.rank_domain_evidence("market")
    finally:
        for p in ps:
            p.stop()
    assert ranked[0]["name"] == "AAPL"          # mayor score por E×LB×conf
    assert ranked[0]["score"] > ranked[1]["score"]


# ── 4. Opinión grounded que expresa preferencia (no fija) ─────────────────

def test_build_criterion_expresses_preference():
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("TSLA", 90.0, 8.0, 100, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"market": [_grav("AAPL", 69)]},
                 domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        # sin LLM inyectado y sin bridge listo → texto determinista grounded
        resp = C.build_criterion("market", "¿qué opinas de la bolsa?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "aapl" in low                        # ancla la posición en el patrón real
    assert "emerge" in low                       # posición emergente (no "me inclino"/"elijo")
    assert ("expectancy" in low or "wr" in low)          # cita métrica real
    # sin coletilla fija: la conclusión termina en la evidencia
    assert "no es una regla" not in low
    assert "elección de menú" not in low


# ── 5. Abstención constructiva ante entidades ausentes (caso route_A) ─────

def test_build_criterion_route_a_constructive():
    priors = {"freight_logistics": [
        _prior("empty_miles", 90.0, 49.7, 56, "HIGH"),
        _prior("load_booking", 90.0, 42.5, 48, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"freight_logistics": []},
                 domain_stats={"freight_logistics": {}})
    q = ("¿Por qué route_A y no route_B o route_C? Defiende esa decisión "
         "exclusivamente con la evidencia persistida en freight_logistics")
    ps = _patch_stores(priors, ["freight_logistics"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("freight_logistics", q)
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    # Reconoce que route_A/B/C no existen…
    assert "route_a" in low and "route_b" in low and "route_c" in low
    assert "no tengo" in low
    assert "no opino" not in low                 # ya NO se calla: siempre da posición
    # …y AUN ASÍ da su posición emergente sobre lo observado
    assert "empty_miles" in low
    assert "emerge" in low
    # sin reintroducir las afirmaciones fabricadas del incidente
    for claim in ("repeticiones exitosas", "menor variabilidad", "más consistentes"):
        assert claim not in low


# ── 6. Verificador de grounding ───────────────────────────────────────────

def test_verify_grounded():
    ranked = [{"name": "load_booking", "win_rate": 90, "wilson_lb": 80}]
    dom = "freight_logistics"
    assert C._verify_grounded("Prefiero load_booking con WR 90%.", ranked, dom)
    # entidad no soportada
    assert not C._verify_grounded("Creo que route_x es mejor.", ranked, dom)
    # porcentaje que no coincide con la evidencia
    assert not C._verify_grounded("load_booking tiene WR 50%.", ranked, dom)


# ── 7. LLM no-grounded → fallback determinista ────────────────────────────

def test_build_criterion_falls_back_when_llm_ungrounded():
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        # LLM inventa una entidad ausente → verificador rechaza → determinista
        resp = C.build_criterion(
            "market", "¿qué opinas?",
            llm=lambda prompt: "Creo que route_x es mejor por sus convergencias.",
        )
    finally:
        for p in ps:
            p.stop()
    assert "route_x" not in resp.lower()        # no se propaga la fabricación
    assert "aapl" in resp.lower()               # cae al criterio determinista real


# ── 8. LLM grounded → se usa su fraseo ────────────────────────────────────

def test_build_criterion_uses_grounded_llm():
    # El LLM es SOLO presentación: re-redacta la conclusión determinista
    # conservando ancla + cifras y sin recomendaciones → origin llm_rendered.
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        rendered = "Lo que veo en AAPL sostiene mi lectura: expectancy +16.00, WR 100%."
        res = C.build_criterion_result(
            "market", "¿qué opinas de la bolsa?", llm=lambda prompt: rendered,
        )
        wrapped = C.build_criterion(
            "market", "¿qué opinas de la bolsa?", llm=lambda prompt: rendered,
        )
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "llm_rendered"
    assert res.text == rendered
    assert res.render_attempt.grounding_passed is True
    assert res.render_attempt.preservation_passed is True
    assert wrapped == rendered            # el wrapper -> str devuelve el texto autorizado


# ── 9. Tema concreto: extracción y criterio centrado en el tema ───────────

def test_extract_topic_tokens():
    assert "nvda" in C.extract_topic_tokens("¿qué opinas de NVDA?")
    # indicador de dominio se descarta (no es el tema concreto)
    assert "bolsa" not in C.extract_topic_tokens("¿qué opinas de la bolsa?")
    # sinónimo ES→esquema (carga → load/booking)
    assert "load" in C.extract_topic_tokens("¿cuál es la mejor carga?")
    assert "route_a" in C.extract_topic_tokens("por qué route_A")


def test_build_criterion_topic_scoped():
    # AAPL es el top global por score, pero la pregunta es sobre NVDA → el
    # criterio debe CENTRARSE en NVDA (experiencia relacionada al tema),
    # no arrastrar el top global del dominio.
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("NVDA", 90.0, 5.0, 60, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué opinas de NVDA?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "«nvda»" in low                   # posición centrada en el tema
    assert "emerge" in low                   # posición emergente
    assert "aapl" not in low                # no arrastra el top global


def test_build_criterion_always_opines_low_data():
    # Con MUY poca evidencia (1 patrón, N bajo, confianza LOW) igual da su
    # posición — no se abstiene por "datos insuficientes".
    priors = {"market": [_prior("AAPL", 60.0, 2.0, 3, "LOW")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "aapl" in low                         # opina con lo que tiene
    # posición calibrada como preliminar (evidencia insuficiente para ser firme)
    assert "señal aún no concluyente" in low
    assert "todavía no tengo datos" not in low    # NO se abstiene
    assert "no es una regla" not in low           # sin coletilla fija


# ── 10. RenderAttempt.__post_init__ — una rama por cada estado de la tabla ──

def test_render_attempt_not_attempted():
    # Rama 1: no intentado → todo None.
    assert C.RenderAttempt(attempted=False).failure_reason is None
    with pytest.raises(ValueError):
        C.RenderAttempt(attempted=False, grounding_passed=True)
    with pytest.raises(ValueError):
        C.RenderAttempt(attempted=False, rejected_text="x")


def test_render_attempt_no_grounding_eval():
    # Rama 2: intentado sin evaluar grounding → reason ∈ {empty, llm_error}.
    assert C.RenderAttempt(True, failure_reason="empty").failure_reason == "empty"
    assert C.RenderAttempt(True, failure_reason="llm_error").failure_reason == "llm_error"
    with pytest.raises(ValueError):
        C.RenderAttempt(True)                              # sin reason
    with pytest.raises(ValueError):
        C.RenderAttempt(True, failure_reason="not_grounded")  # reason inválida aquí


def test_render_attempt_grounding_failed():
    # Rama 3: grounding falló → preservation=None, reason=not_grounded.
    ra = C.RenderAttempt(True, grounding_passed=False, failure_reason="not_grounded")
    assert ra.grounding_passed is False and ra.preservation_passed is None
    with pytest.raises(ValueError):
        C.RenderAttempt(True, grounding_passed=False, failure_reason="empty")
    with pytest.raises(ValueError):
        C.RenderAttempt(True, grounding_passed=False, preservation_passed=True,
                        failure_reason="not_grounded")


def test_render_attempt_preservation_failed():
    # Rama 4: grounding True, preservation False → reason=conclusion_altered.
    ra = C.RenderAttempt(True, grounding_passed=True, preservation_passed=False,
                         failure_reason="conclusion_altered")
    assert ra.preservation_passed is False
    with pytest.raises(ValueError):
        C.RenderAttempt(True, grounding_passed=True, preservation_passed=False,
                        failure_reason="not_grounded")


def test_render_attempt_both_passed():
    # Rama 5: ambas True → sin failure_reason.
    assert C.RenderAttempt(True, grounding_passed=True,
                           preservation_passed=True).failure_reason is None
    with pytest.raises(ValueError):
        C.RenderAttempt(True, grounding_passed=True, preservation_passed=True,
                        failure_reason="empty")
    with pytest.raises(ValueError):
        C.RenderAttempt(True, grounding_passed=True)       # preservation=None inválido


# ── 11. CriterionResult — invariantes de origin ────────────────────────────

def _ok_attempt():
    return C.RenderAttempt(True, grounding_passed=True, preservation_passed=True)


def test_criterion_result_text_selection():
    det, rnd = "Mi posición determinista.", "Mi posición, redactada."
    assert C.CriterionResult("llm_rendered", det, rnd, _ok_attempt()).text == rnd
    assert C.CriterionResult("deterministic", det, None,
                             C.RenderAttempt(attempted=False)).text == det


def test_criterion_result_requires_deterministic_conclusion():
    with pytest.raises(ValueError):
        C.CriterionResult("deterministic", "   ", None, C.RenderAttempt(attempted=False))


def test_criterion_result_llm_rendered_invariants():
    det = "Mi posición determinista."
    with pytest.raises(ValueError):                        # sin rendered_text
        C.CriterionResult("llm_rendered", det, None, _ok_attempt())
    with pytest.raises(ValueError):                        # verificaciones no pasaron
        C.CriterionResult("llm_rendered", det, "x",
                          C.RenderAttempt(True, grounding_passed=True,
                                          preservation_passed=False,
                                          failure_reason="conclusion_altered"))


def test_criterion_result_deterministic_invariants():
    det = "Mi posición determinista."
    with pytest.raises(ValueError):                        # no debe llevar rendered_text
        C.CriterionResult("deterministic", det, "algo", C.RenderAttempt(attempted=False))
    with pytest.raises(ValueError):                        # éxito no puede ser deterministic
        C.CriterionResult("deterministic", det, None, _ok_attempt())
    r = C.CriterionResult("deterministic", det, None,
                          C.RenderAttempt(True, grounding_passed=False,
                                          failure_reason="not_grounded"))
    assert r.origin == "deterministic"


def test_criterion_result_insufficient_invariants():
    det = "Todavía no tengo datos observados en market..."
    assert C.CriterionResult("insufficient_evidence", det, None,
                             C.RenderAttempt(attempted=False)).text == det
    with pytest.raises(ValueError):                        # no representa intento de render
        C.CriterionResult("insufficient_evidence", det, None,
                          C.RenderAttempt(True, failure_reason="empty"))


# ── 12. build_criterion_result — los cuatro caminos de falla + ausencia ────

def _raise_llm(_):
    raise RuntimeError("provider boom")


def _market_stores():
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    return _patch_stores(priors, ["market"], gi)


def test_bcr_origin_empty():
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?", llm=lambda p: "   ")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.attempted is True
    assert res.render_attempt.failure_reason == "empty"
    assert res.render_attempt.grounding_passed is None
    assert "aapl" in res.text.lower()


def test_bcr_origin_llm_error():
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?", llm=_raise_llm)
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.failure_reason == "llm_error"


def test_bcr_origin_not_grounded():
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result(
            "market", "¿qué opinas?", llm=lambda p: "route_x es superior.",
        )
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.grounding_passed is False
    assert res.render_attempt.failure_reason == "not_grounded"
    assert res.render_attempt.rejected_text == "route_x es superior."
    assert "route_x" not in res.text.lower()


def test_bcr_origin_conclusion_altered():
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        # grounded (sin ids/% falsos) pero con recomendación → altera la conclusión.
        res = C.build_criterion_result(
            "market", "¿qué opinas?", llm=lambda p: "Deberías comprar AAPL ahora.",
        )
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.grounding_passed is True
    assert res.render_attempt.preservation_passed is False
    assert res.render_attempt.failure_reason == "conclusion_altered"


def test_bcr_origin_deterministic_no_llm():
    # Sin LLM disponible (complete → no_key vía fixture _criterion_llm_offline)
    # → determinista, attempted=False.
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.attempted is False


def test_bcr_origin_insufficient_evidence():
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores({}, ["market"], gi)
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "insufficient_evidence"
    assert res.render_attempt.attempted is False
    assert "todavía no tengo datos" in res.text.lower()


# ── 13. _preserves_conclusion — gaps de regex cerrados + ancla fail-closed ──

_DET_AAPL = "«AAPL» (expectancy +16.00, WR 100%, LB 98% sobre 221 obs)."
_RANKED_AAPL = [{"name": "AAPL", "win_rate": 100, "wilson_lb": 98}]


def test_preserves_rejects_recomend_root_variants():
    # Raíz recomend-/suger- (NO recomiend-/sugier-): el gap del regex que cerramos.
    assert not C._preserves_conclusion(
        "Sobre AAPL, esto sería recomendable.", _DET_AAPL, _RANKED_AAPL)
    assert not C._preserves_conclusion(
        "AAPL: mi recomendación es clara.", _DET_AAPL, _RANKED_AAPL)
    assert not C._preserves_conclusion(
        "AAPL: la sugerencia sería clara.", _DET_AAPL, _RANKED_AAPL)
    # Las formas -iend-/-ier- que ya se cubrían siguen cubiertas.
    assert not C._preserves_conclusion(
        "AAPL: te recomiendo esto.", _DET_AAPL, _RANKED_AAPL)


def test_preserves_accepts_faithful_render():
    assert C._preserves_conclusion(
        "Mi lectura sobre AAPL: WR 100%, LB 98%.", _DET_AAPL, _RANKED_AAPL)


def test_preserves_fail_closed_without_anchor():
    # Punto #4: sin ancla presente (o sin ranked/name) no hay nada que validar
    # → rechaza (fail-closed), aunque no haya recomendación ni cifras nuevas.
    assert not C._preserves_conclusion(
        "Un texto neutral, sin ancla.", _DET_AAPL, _RANKED_AAPL)
    assert not C._preserves_conclusion("cualquier cosa", _DET_AAPL, [])
    assert not C._preserves_conclusion("cualquier cosa", _DET_AAPL, [{"name": ""}])


def test_bcr_conclusion_altered_recomendable():
    # Integración: "recomendable" (raíz recomend-) ahora se rechaza end-to-end.
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result(
            "market", "¿qué opinas?", llm=lambda p: "AAPL sería recomendable.",
        )
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.preservation_passed is False
    assert res.render_attempt.failure_reason == "conclusion_altered"


# ── 14. Guarda de polaridad/negación (gap de negación con ancla+cifras intactas) ──

def test_preserves_rejects_polarity_flip():
    # Valencia negativa introducida (el determinista es una posición de apoyo).
    assert not C._preserves_conclusion(
        "AAPL es la peor lectura, WR 100%.", _DET_AAPL, _RANKED_AAPL)
    assert not C._preserves_conclusion(
        "Descartaría AAPL pese a su WR 100%.", _DET_AAPL, _RANKED_AAPL)
    # Negación del apoyo con ancla + cifras intactas: el gap que este guard cierra.
    assert not C._preserves_conclusion(
        "Mi posición no se apoya en AAPL, WR 100%.", _DET_AAPL, _RANKED_AAPL)
    # Control: apoyo afirmativo con las mismas cifras SÍ pasa.
    assert C._preserves_conclusion(
        "Mi posición se apoya en AAPL: WR 100%, LB 98%.", _DET_AAPL, _RANKED_AAPL)


def test_bcr_polarity_flip_altered():
    # Integración: giro de polaridad (grounded, ancla+cifras ok) → conclusion_altered.
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result(
            "market", "¿qué opinas?", llm=lambda p: "AAPL es la peor opción, WR 100%.",
        )
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.grounding_passed is True
    assert res.render_attempt.preservation_passed is False
    assert res.render_attempt.failure_reason == "conclusion_altered"


# ── 15. Integración con core.llm_call (camino de producción del render) ────

def test_bcr_llm_rendered_via_complete(monkeypatch):
    # complete devuelve un render fiel (grounded, sin flip) → origin llm_rendered.
    from core import llm_call
    rendered = "Mi lectura sobre AAPL sostiene la posición: WR 100%, LB 98%."
    monkeypatch.setattr(
        llm_call, "complete",
        lambda *a, **k: llm_call.LLMResult(True, rendered, "ok"),
    )
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "llm_rendered"
    assert res.text == rendered
    assert res.render_attempt.grounding_passed is True
    assert res.render_attempt.preservation_passed is True


def test_bcr_complete_unavailable_not_attempted(monkeypatch):
    # complete no disponible (gate_closed) → attempted=False, determinista.
    from core import llm_call
    monkeypatch.setattr(
        llm_call, "complete",
        lambda *a, **k: llm_call.LLMResult(False, "", "gate_closed"),
    )
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.attempted is False


def test_bcr_complete_empty_falls_back(monkeypatch):
    from core import llm_call
    monkeypatch.setattr(
        llm_call, "complete",
        lambda *a, **k: llm_call.LLMResult(False, "", "empty"),
    )
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.attempted is True
    assert res.render_attempt.failure_reason == "empty"


def test_bcr_complete_http_error_falls_back(monkeypatch):
    from core import llm_call
    monkeypatch.setattr(
        llm_call, "complete",
        lambda *a, **k: llm_call.LLMResult(False, "", "http_error", error="500"),
    )
    ps = _market_stores()
    for p in ps:
        p.start()
    try:
        res = C.build_criterion_result("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    assert res.origin == "deterministic"
    assert res.render_attempt.attempted is True
    assert res.render_attempt.failure_reason == "llm_error"


# ── 16. Polaridad de la pregunta + calibración de suficiencia ──────────────

def test_detect_query_polarity():
    assert C._detect_query_polarity("¿qué patrones de propiedad pierden?") == "weakness"
    assert C._detect_query_polarity("¿cuál es el mejor patrón?") == "support"
    assert C._detect_query_polarity("¿qué opinas del mercado?") == "neutral"
    assert C._detect_query_polarity("what loses and what wins") == "neutral"  # mixta
    assert C._detect_query_polarity("") == "neutral"


def test_weakness_thin_evidence_is_calibrated():
    # Reproduce el caso new_listing: N bajo, sin ventaja. Ante «¿qué pierde?»
    # sigue opinando (never-block) pero calibra: NO vende ruido como posición.
    priors = {"florida_real_estate": [_prior("new_listing", 100.0, 0.0, 3, "")]}
    gi = _FakeGI(by_domain_map={"florida_real_estate": [_grav("new_listing", 10)]},
                 domain_stats={"florida_real_estate": {}})
    ps = _patch_stores(priors, ["florida_real_estate"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("florida_real_estate", "¿qué patrones de propiedad pierden?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "new_listing" in low                       # opina con lo que hay
    assert "concluyente" in low                        # marcada como preliminar
    assert "se apoya en" not in low                    # NO vende ruido como posición firme
    assert "todavía no tengo datos" not in low         # NO se abstiene (never-block)


def test_weakness_picks_losing_pattern():
    # Con polaridad negativa, ancla en el PERDEDOR real, no en el top ganador.
    priors = {"market": [
        _prior("AAPL", 100.0, 16.0, 221, "HIGH"),
        _prior("BADX", 20.0, -6.0, 40, "HIGH"),
    ]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué patrón pierde más?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "más flojo que observo es «badx»" in low     # ancla en el perdedor
    assert "-6.00" in low                               # cita su expectancy negativa real


def test_support_firm_uses_apoyo_phrasing():
    # Polaridad positiva con evidencia madura → posición de apoyo firme.
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿cuál es el mejor patrón?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "se apoya en «aapl»" in low


def test_neutral_thin_evidence_is_preliminary():
    # Neutra con muy poca muestra → calibra como preliminar (no "se apoya en").
    priors = {"market": [_prior("AAPL", 60.0, 2.0, 3, "LOW")]}
    gi = _FakeGI(by_domain_map={"market": []}, domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué opinas?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "aapl" in low
    assert "no concluyente" in low
    assert "se apoya en" not in low


def test_mature_neutral_still_uses_apoyo_phrasing():
    # Regresión: evidencia madura neutra conserva la frase de apoyo firme.
    priors = {"market": [_prior("AAPL", 100.0, 16.0, 221, "HIGH")]}
    gi = _FakeGI(by_domain_map={"market": [_grav("AAPL", 69)]},
                 domain_stats={"market": {}})
    ps = _patch_stores(priors, ["market"], gi)
    for p in ps:
        p.start()
    try:
        resp = C.build_criterion("market", "¿qué opinas de la bolsa?")
    finally:
        for p in ps:
            p.stop()
    low = resp.lower()
    assert "se apoya en «aapl»" in low
    assert "emerge" in low
