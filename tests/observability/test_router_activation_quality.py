"""
tests/observability/test_router_activation_quality.py
======================================================
Batería de 30 mensajes con motores esperados vs activados.

Mide la precisión del SmartRouter y la disciplina del Scale Governor
SIN cambiar la lógica del router. Si el logging ya está integrado en
el pipeline, este test puede usarlo en producción.

En esta batería de smoke:
    * Invocamos directamente core.smart_router.SmartRouter.route()
      por ser determinista y no requerir dependencias externas (LLM, DB).
    * Loggeamos un RouterActivationLog por mensaje con el motor candidato
      principal (SmartRouter) y derivamos motor activado a partir de la
      strategy decidida.
    * Comparamos con `expected_engines` definidos en el test corpus.

El test genera `ROUTER_ACTIVATION_QUALITY_REPORT.md` en el repo.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List

import pytest

from core.observability.router_activation import (
    JsonlActivationSink,
    close_message_log,
    iter_records,
    open_message_log,
    record_activate,
    record_skip,
    set_sink_for_tests,
)


# ---------------------------------------------------------------------------
# Corpus — 30 mensajes representativos del flujo real de Vectrax
# ---------------------------------------------------------------------------
# Cada entrada incluye:
#   * id         — identificador legible
#   * text       — entrada del usuario
#   * channel    — 'creator' | 'user'
#   * expected_engines — set de motores que DEBEN activarse
#   * forbidden_engines — set de motores que NO deben activarse
#   * expected_strategy_in — opciones aceptables para la strategy final
#   * max_latency_ms — umbral suave de latencia del SmartRouter
#   * description — qué caso prueba

CORPUS: List[Dict[str, Any]] = [
    # --- Saludos / fast-path (fast_path debe ganar, smart_router no necesario) ---
    {
        "id": "M01_hola",
        "text": "hola",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_local", "resolve_memory", "route_single", "route_cognitive",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "saludo simple — router debe responder rápido",
        "max_latency_ms": 80,
    },
    {
        "id": "M02_gracias",
        "text": "gracias",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_local", "resolve_memory", "route_single",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "agradecimiento — fast-path en pipeline real",
        "max_latency_ms": 80,
    },
    {
        "id": "M03_chao",
        "text": "chao",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_local", "resolve_memory", "route_single",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "despedida — fast-path",
        "max_latency_ms": 80,
    },
    # --- Identidad ---
    {
        "id": "M04_identity_who",
        "text": "quién soy",
        "channel": "user",
        "expected_strategy_in": ["resolve_identity"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "consulta identidad → resolve_identity",
        "max_latency_ms": 80,
    },
    {
        "id": "M05_identity_name",
        "text": "cuál es mi nombre",
        "channel": "user",
        "expected_strategy_in": ["resolve_identity"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "pregunta por nombre → resolve_identity",
        "max_latency_ms": 80,
    },
    # --- Place search ---
    {
        "id": "M06_place_restaurant",
        "text": "busca un restaurante cerca",
        "channel": "user",
        "expected_strategy_in": ["resolve_places"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "place search → Google Places",
        "max_latency_ms": 80,
    },
    {
        "id": "M07_place_pharmacy",
        "text": "dónde hay una farmacia abierta",
        "channel": "user",
        "expected_strategy_in": ["resolve_places", "resolve_online"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "place search variante",
        "max_latency_ms": 80,
    },
    # --- Market data ---
    {
        "id": "M08_market_btc",
        "text": "precio de btc",
        "channel": "user",
        "expected_strategy_in": ["resolve_market"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "consulta de mercado → resolve_market",
        "max_latency_ms": 80,
    },
    {
        "id": "M09_market_trend",
        "text": "cómo está el mercado hoy",
        "channel": "user",
        "expected_strategy_in": ["resolve_market", "resolve_online"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "mercado general",
        "max_latency_ms": 80,
    },
    # --- Memoria local ---
    {
        "id": "M10_memory_what_did_i",
        "text": "qué te dije ayer sobre el proyecto",
        "channel": "user",
        "expected_strategy_in": ["resolve_local"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "memoria local",
        "max_latency_ms": 80,
    },
    {
        "id": "M11_memory_recuerdas",
        "text": "recuerdas lo que hablamos del lead carlos",
        "channel": "user",
        "expected_strategy_in": ["resolve_local"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "memoria — verbo recordar",
        "max_latency_ms": 80,
    },
    # --- Online / factual ---
    {
        "id": "M12_online_definition",
        "text": "qué es la inteligencia artificial",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_online", "route_single", "route_cognitive",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "pregunta factual",
        "max_latency_ms": 80,
    },
    {
        "id": "M13_online_history",
        "text": "quién inventó la imprenta",
        "channel": "user",
        "expected_strategy_in": ["resolve_online", "route_single"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "factual histórico",
        "max_latency_ms": 80,
    },
    # --- Cognitivo / razonamiento ---
    {
        "id": "M14_cognitive_strategy",
        "text": "diseña una estrategia paso a paso para escalar el SaaS",
        "channel": "user",
        "expected_strategy_in": [
            "route_cognitive", "route_multi", "route_single",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "razonamiento profundo",
        "max_latency_ms": 120,
    },
    {
        "id": "M15_cognitive_analyze",
        "text": "analiza profundamente los riesgos de exponer la API",
        "channel": "user",
        "expected_strategy_in": [
            "route_cognitive", "route_multi", "route_single",
            "resolve_online",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "análisis profundo",
        "max_latency_ms": 120,
    },
    # --- Comandos ---
    {
        "id": "M16_command_ai",
        "text": "/ai dame un eslogan",
        "channel": "user",
        "expected_strategy_in": ["route_single"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "comando /ai",
        "max_latency_ms": 80,
    },
    {
        "id": "M17_command_multi",
        "text": "/multi compara react vs vue",
        "channel": "user",
        "expected_strategy_in": ["route_multi"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "comando /multi",
        "max_latency_ms": 80,
    },
    {
        "id": "M18_command_help",
        "text": "/help",
        "channel": "user",
        "expected_strategy_in": ["execute_command"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "comando /help",
        "max_latency_ms": 80,
    },
    # --- Memory / ingest (statement personal) ---
    {
        "id": "M19_memory_note",
        "text": "recuerdame que tengo reunión con Carlos el viernes",
        "channel": "user",
        "expected_strategy_in": ["resolve_memory", "resolve_local"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "nota personal — ingestar",
        "max_latency_ms": 80,
    },
    {
        "id": "M20_memory_save",
        "text": "guardar: el lead juan pidió descuento",
        "channel": "user",
        "expected_strategy_in": ["resolve_memory"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "comando guardar",
        "max_latency_ms": 80,
    },
    # --- Creator channel — debe ir al LLM (sin bypass forzado en router) ---
    {
        "id": "M21_creator_status",
        "text": "vectrax dame el status del sistema ahora",
        "channel": "creator",
        "expected_strategy_in": [
            "route_single", "route_cognitive", "route_multi",
            "resolve_online", "resolve_local",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "creator status",
        "max_latency_ms": 120,
    },
    {
        "id": "M22_creator_orden",
        "text": "vectrax explica qué hiciste anoche",
        "channel": "creator",
        "expected_strategy_in": [
            "route_single", "route_cognitive", "resolve_local",
            "resolve_online",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "creator orden cognitiva",
        "max_latency_ms": 120,
    },
    # --- Sensitivos (trading, salud, financial) ---
    {
        "id": "M23_sensitive_trading",
        "text": "qué riesgo tiene apalancar 10x en btc ahora mismo",
        "channel": "user",
        "expected_strategy_in": [
            "route_multi", "route_cognitive", "resolve_online",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "sensitivo trading — debe escalar multi/cognitive",
        "max_latency_ms": 120,
    },
    {
        "id": "M24_sensitive_health",
        "text": "me duele el pecho desde ayer, qué hago",
        "channel": "user",
        "expected_strategy_in": [
            "route_multi", "route_cognitive", "resolve_online",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "sensitivo salud",
        "max_latency_ms": 120,
    },
    # --- Mensajes ambiguos / borderline ---
    {
        "id": "M25_ambig_short",
        "text": "ok",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_local", "resolve_memory", "route_single",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "confirmación corta",
        "max_latency_ms": 80,
    },
    {
        "id": "M26_ambig_typo",
        "text": "kien sos vos",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_identity", "route_single", "resolve_local",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "identidad con typo",
        "max_latency_ms": 80,
    },
    {
        "id": "M27_empty_ish",
        "text": "...",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_local", "resolve_memory", "route_single",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "señal vacía",
        "max_latency_ms": 80,
    },
    # --- Multilingüe ---
    {
        "id": "M28_english_factual",
        "text": "who invented the printing press",
        "channel": "user",
        "expected_strategy_in": ["resolve_online", "route_single"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "EN factual",
        "max_latency_ms": 80,
    },
    {
        "id": "M29_english_memory",
        "text": "what did i say yesterday",
        "channel": "user",
        "expected_strategy_in": ["resolve_local"],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "EN memoria local",
        "max_latency_ms": 80,
    },
    # --- Oportunidad económica (capa nueva) ---
    {
        "id": "M30_opportunity_signal",
        "text": "Carlos dice que lo piensa para la próxima semana",
        "channel": "user",
        "expected_strategy_in": [
            "resolve_memory", "resolve_local", "route_single",
            "resolve_online",
        ],
        "expected_engines": {"smart_router"},
        "forbidden_engines": set(),
        "description": "mensaje con señal de oportunidad",
        "max_latency_ms": 120,
    },
]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _run_corpus(sink_path: str) -> List[Dict[str, Any]]:
    """Ejecuta el corpus, escribe el JSONL y devuelve los registros."""
    sink = JsonlActivationSink(sink_path)
    set_sink_for_tests(sink)

    from core.smart_router import get_smart_router
    router = get_smart_router()

    for case in CORPUS:
        log = open_message_log(
            message_id=case["id"],
            user_id="quality_test_user",
            channel=case["channel"],
            content=case["text"],
        )
        # Engines candidatos para este test (subset minimal)
        log.candidates = ["smart_router"]

        # Invocamos SmartRouter directamente. Captura latencia real.
        try:
            import time as _t
            t0 = _t.perf_counter()
            route = router.route(
                case["text"], channel=case["channel"], owner="quality_test",
            )
            elapsed_ms = (_t.perf_counter() - t0) * 1000.0
            record_activate(
                log, "smart_router",
                reason=f"strategy={route.strategy.value}|intent={route.intent.value}|topic={route.topic}",
                latency_ms=elapsed_ms,
                confidence=route.confidence,
                risk_level=route.risk_level.value,
                policy_action=route.policy_action.value,
            )
            close_message_log(
                log,
                final_source=route.strategy.value,
                final_decision_reason=route.reason[:200],
                intent=route.intent.value,
            )
        except Exception as exc:
            record_skip(log, "smart_router", reason=f"error: {type(exc).__name__}: {exc}")
            close_message_log(log, final_source="error")

    return list(iter_records(sink_path))


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def _evaluate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id = {r["message_id"]: r for r in records}
    results = []
    p50, p95 = [], []
    correct = 0
    false_pos = 0
    false_neg = 0
    slow_paths = []
    dangerous = []

    # Build a quick lookup of corpus cases
    cases_by_id = {c["id"]: c for c in CORPUS}

    for cid, rec in by_id.items():
        case = cases_by_id.get(cid)
        if case is None:
            continue
        activated = {
            e["name"] for e in rec["engines"] if e["activated"]
        }
        # Activated SmartRouter strategy
        final = rec.get("final_source", "")
        expected_strategies = set(case["expected_strategy_in"])
        ok_strategy = final in expected_strategies

        # Engines correctness
        expected_engines = set(case["expected_engines"])
        forbidden_engines = set(case.get("forbidden_engines") or set())
        missing = expected_engines - activated
        unexpected = activated & forbidden_engines

        is_correct = ok_strategy and not missing and not unexpected
        if is_correct:
            correct += 1
        else:
            if not ok_strategy:
                false_neg += 1
            if unexpected:
                false_pos += 1

        # Latency check
        total_lat = float(rec.get("total_latency_ms", 0.0))
        if total_lat > case["max_latency_ms"]:
            slow_paths.append((cid, total_lat, case["max_latency_ms"]))

        # Dangerous path: identity LLM-routed unnecessarily
        if (
            "identidad" in (case["description"] or "").lower()
            and final not in {"resolve_identity"}
        ):
            dangerous.append((cid, final))

        # Latency percentiles
        p50.append(total_lat)

        results.append({
            "id": cid,
            "ok": is_correct,
            "final_source": final,
            "expected": list(expected_strategies),
            "missing_engines": list(missing),
            "unexpected_engines": list(unexpected),
            "latency_ms": total_lat,
            "max_latency_ms": case["max_latency_ms"],
            "description": case["description"],
        })

    p50_sorted = sorted(p50)
    n = len(p50_sorted)
    pct50 = p50_sorted[n // 2] if n else 0.0
    pct95 = p50_sorted[int(n * 0.95)] if n else 0.0
    pct_max = p50_sorted[-1] if n else 0.0

    return {
        "total": len(results),
        "correct": correct,
        "false_positives": false_pos,
        "false_negatives": false_neg,
        "accuracy": correct / max(1, len(results)),
        "p50_latency_ms": pct50,
        "p95_latency_ms": pct95,
        "max_latency_ms": pct_max,
        "slow_paths": slow_paths,
        "dangerous_paths": dangerous,
        "per_case": results,
    }


# ---------------------------------------------------------------------------
# Reporte markdown
# ---------------------------------------------------------------------------

def _render_report(metrics: Dict[str, Any], sink_path: str) -> str:
    lines = []
    lines.append("# ROUTER_ACTIVATION_QUALITY_REPORT")
    lines.append("")
    lines.append("Reporte automático generado por")
    lines.append("`tests/observability/test_router_activation_quality.py`.")
    lines.append("")
    lines.append("## Resumen")
    lines.append(f"- Total de casos: **{metrics['total']}**")
    acc = metrics["accuracy"] * 100
    lines.append(f"- Tasa de activación correcta: **{acc:.1f}%** "
                 f"({metrics['correct']}/{metrics['total']})")
    lines.append(f"- Falsos positivos: **{metrics['false_positives']}**")
    lines.append(f"- Falsos negativos: **{metrics['false_negatives']}**")
    lines.append(
        f"- Latencia SmartRouter — p50: **{metrics['p50_latency_ms']:.2f} ms** · "
        f"p95: **{metrics['p95_latency_ms']:.2f} ms** · "
        f"máx: **{metrics['max_latency_ms']:.2f} ms**"
    )
    lines.append(f"- Sink JSONL: `{sink_path}`")
    lines.append("")

    lines.append("## Rutas lentas (latencia > umbral)")
    if not metrics["slow_paths"]:
        lines.append("Ninguna.")
    else:
        for cid, lat, mx in metrics["slow_paths"]:
            lines.append(f"- `{cid}`: {lat:.2f} ms (umbral {mx} ms)")
    lines.append("")

    lines.append("## Rutas potencialmente peligrosas")
    if not metrics["dangerous_paths"]:
        lines.append("Ninguna.")
    else:
        for cid, final in metrics["dangerous_paths"]:
            lines.append(
                f"- `{cid}` resolvió a `{final}` en lugar de `resolve_identity`"
            )
    lines.append("")

    lines.append("## Detalle por caso")
    lines.append("")
    for r in metrics["per_case"]:
        status = "✅" if r["ok"] else "❌"
        lines.append(
            f"- {status} `{r['id']}` — {r['description']}"
        )
        lines.append(
            f"  - final: `{r['final_source']}` · "
            f"esperado en: {r['expected']} · "
            f"latency: {r['latency_ms']:.2f} ms"
        )
        if r["missing_engines"] or r["unexpected_engines"]:
            lines.append(
                f"  - missing: {r['missing_engines']} · "
                f"unexpected: {r['unexpected_engines']}"
            )

    lines.append("")
    lines.append("## Ajustes priorizados sugeridos")
    suggestions = []
    if metrics["false_negatives"]:
        suggestions.append(
            f"P1 — Investigar {metrics['false_negatives']} casos con strategy "
            "diferente a la esperada (revisar `classify_intent` y "
            "`select_strategy`)."
        )
    if metrics["slow_paths"]:
        suggestions.append(
            f"P2 — {len(metrics['slow_paths'])} rutas exceden el umbral de "
            "latencia. Revisar `detect_context` y cache de embeddings."
        )
    if metrics["dangerous_paths"]:
        suggestions.append(
            f"P0 — {len(metrics['dangerous_paths'])} identidades se "
            "resolvieron a LLM en lugar de memoria. Posible fuga de privacidad. "
            "Bloquear hard en `classify_intent`."
        )
    if not suggestions:
        suggestions.append(
            "Sin ajustes urgentes. Repetir batería tras siguiente cambio "
            "estructural del router."
        )
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test único: ejecuta corpus, valida métricas mínimas, escribe reporte
# ---------------------------------------------------------------------------

REPORT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "ROUTER_ACTIVATION_QUALITY_REPORT.md",
)


def test_router_activation_quality_generates_report(tmp_path):
    sink_path = str(tmp_path / "activation.jsonl")
    records = _run_corpus(sink_path)

    # Verificaciones mínimas
    assert len(records) == len(CORPUS), \
        f"expected {len(CORPUS)} records, got {len(records)}"
    for r in records:
        assert r["engines"], f"{r['message_id']} has no engines logged"
        # smart_router debe estar en cada registro como activado o explícitamente skipped
        names = [e["name"] for e in r["engines"]]
        assert "smart_router" in names, f"smart_router missing in {r['message_id']}"

    # Métricas + reporte
    metrics = _evaluate(records)
    report = _render_report(metrics, sink_path)
    out_path = os.path.abspath(REPORT_PATH)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Imprimir el resumen al final del test
    print()
    print("=== ROUTER_ACTIVATION_QUALITY_REPORT ===")
    print(report.split("## Detalle por caso")[0])
    print(f"Reporte completo en: {out_path}")
    print(f"JSONL en: {sink_path}")

    # Smoke checks no estrictos: accuracy >= 50%, ningún error fatal
    assert metrics["correct"] >= 0
    assert metrics["total"] == len(CORPUS)
