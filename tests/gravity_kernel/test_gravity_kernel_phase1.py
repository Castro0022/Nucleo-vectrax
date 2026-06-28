"""
Gravity Kernel — Tests Fase 1 (Ritmo + Causa/Efecto, shadow mode).

Casos sintéticos NUEVOS (no copiados de la especificación) para verificar que
las fórmulas producen decisiones razonables, que las señales de "fallo" se
anclan en la reacción del usuario (nunca en autoevaluación del LLM), el clamping
a [0,1], la invariancia del shadow (no muta, no escribe con flag apagado) y el
manejo neutro de señales ausentes.
"""
import pytest

from core.gravity_kernel import rhythm, cause_effect, signals, evaluation, shadow
from core.gravity_kernel.loader import get_config

CFG = get_config()


# ── Config carga real (no fallback silencioso) ───────────────────────────────

def test_yaml_actually_loaded_not_silent_default():
    # Si el YAML se rompe, el loader cae a defaults vacíos y el kernel queda
    # ciego. Este test detecta esa degradación silenciosa.
    assert CFG["correction_markers"], "correction_markers vacío → YAML no cargó"
    assert "real_money_trade" in CFG["domain_risk_table"]


# ── Ritmo ────────────────────────────────────────────────────────────────────

def test_rhythm_clean_message_advances():
    ctx = signals.build_rhythm_ctx("vamos a revisar el despliegue del worker", cfg=CFG)
    score = rhythm.calculate_rhythm_score(ctx, CFG)
    assert score == 1.0
    assert rhythm.decide(score, CFG) == "advance"


def test_rhythm_corrections_and_frustration_drop_to_low_band():
    # Usuario corrige y muestra frustración tras una respuesta previa.
    text = "no es eso, te equivocas, ya te dije que estamos hablando de otra cosa"
    ctx = signals.build_rhythm_ctx(text, had_previous_response=True, cfg=CFG)
    score = rhythm.calculate_rhythm_score(ctx, CFG)
    assert score < 0.50
    assert rhythm.decide(score, CFG) in (
        "pause_and_calibrate", "stop_generation_and_ask_axis",
    )


def test_rhythm_score_is_clamped():
    # Inputs extremos no pueden salirse de [0,1].
    ctx = signals.RhythmCtx(
        user_corrections_recent=99, frustration_markers=99,
        topic_switch_count=99, system_misread_count=99,
        same_axis_repetition=0, unanswered_confusion=True,
    )
    score = rhythm.calculate_rhythm_score(ctx, CFG)
    assert 0.0 <= score <= 1.0
    assert score == 0.0


# ── Anclaje externo (no autoevaluación del LLM) ──────────────────────────────

def test_misread_requires_previous_response_not_llm_judgment():
    text = "no entendiste, eso no"
    # Sin respuesta previa: el usuario no puede estar reaccionando → misread 0.
    first_turn = signals.build_rhythm_ctx(text, had_previous_response=False, cfg=CFG)
    assert first_turn.system_misread_count == 0
    # Con respuesta previa: el misread se infiere de la corrección del usuario.
    reaction = signals.build_rhythm_ctx(text, had_previous_response=True, cfg=CFG)
    assert reaction.system_misread_count > 0


# ── Causa/Efecto ─────────────────────────────────────────────────────────────

def test_cause_effect_high_risk_low_confidence_holds_or_blocks():
    # Acción peligrosa (dinero real) sin evidencia favorable.
    ctx = signals.build_cause_effect_ctx(
        result_source=None, cfg=CFG,
    )
    # Forzamos el tipo de acción de alto riesgo vía la tabla.
    ctx.action_type = "real_money_trade"
    risk = CFG["domain_risk_table"]["real_money_trade"]
    ctx.cost_of_error = risk["cost_of_error"]
    ctx.domain_risk_level = risk["domain_risk_level"]
    ctx.reversibility_score = risk["reversibility"]
    ctx.win_rate = 0.30  # historial desfavorable
    ctx.confidence_score = 0.20
    score = cause_effect.calculate_cause_effect_score(ctx, CFG)
    assert score < 0.45
    assert cause_effect.decide(score, CFG) in (
        "hold_and_collect_more_evidence", "block",
    )


def test_cause_effect_safe_reply_is_actionable_not_blocked():
    # Respuesta conversacional con señales neutras: NUNCA debe quedar en
    # hold/block, y debe puntuar por encima de una acción de alto riesgo.
    # (El valor absoluto ~0.525 cae en simulate_or_ask_confirmation: los pesos
    #  del documento son conservadores para respuestas de bajo riesgo; es un
    #  punto de calibración a observar en shadow, no algo a forzar aquí.)
    safe = signals.build_cause_effect_ctx(result_source="memory", cfg=CFG)
    assert safe.action_type == "conversation_reply"
    safe_score = cause_effect.calculate_cause_effect_score(safe, CFG)
    assert 0.0 <= safe_score <= 1.0
    assert cause_effect.decide(safe_score, CFG) not in (
        "hold_and_collect_more_evidence", "block",
    )

    risky = signals.CauseEffectCtx(
        action_type="real_money_trade", cost_of_error=0.80,
        domain_risk_level=0.85, reversibility_score=0.10,
        win_rate=0.30, confidence_score=0.20,
    )
    risky_score = cause_effect.calculate_cause_effect_score(risky, CFG)
    assert safe_score > risky_score


def test_cause_effect_score_is_clamped():
    ctx = signals.CauseEffectCtx(
        cost_of_error=1.0, domain_risk_level=1.0, reversibility_score=0.0,
        win_rate=0.0, expectancy=-1.0, confidence_score=0.0,
        user_correction_history=99,
    )
    score = cause_effect.calculate_cause_effect_score(ctx, CFG)
    assert 0.0 <= score <= 1.0


# ── Null-handling ────────────────────────────────────────────────────────────

def test_cause_effect_neutral_when_no_pattern_stats():
    ctx = signals.build_cause_effect_ctx(result_source="llm", pattern_stats=None, cfg=CFG)
    assert ctx.pattern_stats_present is False
    assert ctx.win_rate == CFG["neutral_defaults"]["win_rate"]
    # No lanza y produce score válido.
    score = cause_effect.calculate_cause_effect_score(ctx, CFG)
    assert 0.0 <= score <= 1.0


# ── Objeto de evaluación unificado ───────────────────────────────────────────

def test_evaluation_contract_and_conservative_final_action():
    obj = evaluation.build_evaluation(
        rhythm_score=0.20, rhythm_action="stop_generation_and_ask_axis",
        cause_effect_score=0.95, cause_effect_action="execute",
    )
    # Contrato: los 5 ejes no implementados quedan None.
    for k in ("mentalism_score", "correspondence_score", "vibration_score", "polarity_score"):
        assert obj[k] is None
    assert obj["generation_mode"] is None
    assert obj["audit_required"] is True
    # final_action toma la rama MÁS conservadora (Ritmo dice parar).
    assert obj["final_action"] == "stop_generation_and_ask_axis"


# ── Invariancia del shadow ───────────────────────────────────────────────────

def test_shadow_disabled_returns_none_and_no_side_effects(monkeypatch):
    calls = []
    import core.self_observation.observation_ledger as ol
    monkeypatch.setattr(ol, "record", lambda **kw: calls.append(kw))
    out = shadow.observe(
        content="hola", user_id="u1", result_source="memory",
        response_sent=True, response_len=10, enabled=False,
    )
    assert out is None
    assert calls == []  # flag apagado → no escribe nada


def test_shadow_enabled_logs_without_raising(monkeypatch):
    calls = []
    import core.self_observation.observation_ledger as ol
    monkeypatch.setattr(ol, "record", lambda **kw: calls.append(kw))
    out = shadow.observe(
        content="no es eso, te equivocas",
        user_id="u1", result_source="memory",
        response_sent=True, response_len=42,
        had_previous_response=True, enabled=True,
    )
    assert out is not None
    assert out["rhythm_score"] <= 1.0
    assert len(calls) == 1
    ev = calls[0]["evidence"]
    assert "eval_object" in ev and "agreement" in ev and "signals" in ev


def test_shadow_records_differentiated_source(monkeypatch):
    calls = []
    import core.self_observation.observation_ledger as ol
    monkeypatch.setattr(ol, "record", lambda **kw: calls.append(kw))
    for src in ("telegram_worker", "web_chat", "creator_chat"):
        shadow.observe(
            content="hola", user_id="u1", result_source="memory",
            response_sent=True, response_len=10, source=src, enabled=True,
        )
    recorded = [c["evidence"]["source"] for c in calls]
    assert recorded == ["telegram_worker", "web_chat", "creator_chat"]
    # el source también va en el summary para lectura rápida
    assert all("source=" in c["summary"] for c in calls)
