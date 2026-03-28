"""
Vectrax Market Data — Vigilancia Activa.

Monitor silencioso que evalúa condiciones de mercado en loop.
Solo genera salida cuando detecta un escenario operable.
Sin ruido. Sin repetición. Solo señal.

Clasificación de señales:
  NO_OPERABLE   — menos de 3 condiciones core cumplidas. Esperar.
  PRE_OPERABLE  — 3 de 4 condiciones core. Atención, pero NO actuar.
  OPERABLE      — 4 de 4 condiciones core + datos suficientes. Escenario válido.

Condiciones core (todas obligatorias para OPERABLE):
  1. Posición en extremo del rango (≤20% inferior o ≥80% superior)
  2. Alineación temporal (24h y 7d en la misma dirección)
  3. Volumen relativo elevado (spike vs promedio)
  4. Claridad direccional (timeframes concordantes)

Condición bonus:
  5. Datos suficientes (precio, rango, volumen presentes)

Modos de operación:
  ESTRICTO (default)   — solo 4/4 core → operable. PRE_OPERABLE = no actuar.
  EXPLORACIÓN (manual) — 3/4 core → entrada parcial (máx 25%), riesgo controlado.
                         Debe activarse explícitamente. Nunca se activa solo.

Uso:
  python -m services.market_vigilance                    # modo estricto
  python -m services.market_vigilance --exploracion      # modo exploración
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from connectors.market.coingecko_client import get_coin_details

logger = logging.getLogger("vectrax.market.vigilance")

# ── Config ──────────────────────────────────────────────────────────

POLL_INTERVAL = 120          # seconds between checks (respect CoinGecko limits)
SYMBOLS = ["BTCUSDT"]        # monitored symbols
RANGE_EXTREME_PCT = 0.20     # top/bottom 20% = extreme zone
VOLUME_SPIKE_FACTOR = 1.25   # 25% above rolling avg = spike
ALIGNMENT_THRESHOLD = 0.3    # 24h and 7d both > +0.3% or both < -0.3%

# Exploration mode limits
EXPLORATION_MAX_EXPOSURE = 0.25  # max 25% of position size
EXPLORATION_MIN_CORE = 3         # minimum 3/4 core conditions


@dataclass
class MarketState:
    """Snapshot of evaluated market conditions."""
    symbol: str
    price: float
    high_24h: float
    low_24h: float
    change_24h: float
    change_7d: float
    volume_24h: float
    ath: float
    timestamp: float = field(default_factory=time.time)

    @property
    def range_size(self) -> float:
        return self.high_24h - self.low_24h if self.high_24h > self.low_24h else 1

    @property
    def position_in_range(self) -> float:
        """0.0 = at low, 1.0 = at high."""
        return (self.price - self.low_24h) / self.range_size

    @property
    def in_lower_extreme(self) -> bool:
        return self.position_in_range <= RANGE_EXTREME_PCT

    @property
    def in_upper_extreme(self) -> bool:
        return self.position_in_range >= (1 - RANGE_EXTREME_PCT)

    @property
    def in_extreme(self) -> bool:
        return self.in_lower_extreme or self.in_upper_extreme

    @property
    def timeframes_aligned(self) -> bool:
        both_up = self.change_24h > ALIGNMENT_THRESHOLD and self.change_7d > ALIGNMENT_THRESHOLD
        both_down = self.change_24h < -ALIGNMENT_THRESHOLD and self.change_7d < -ALIGNMENT_THRESHOLD
        return both_up or both_down

    @property
    def direction(self) -> str:
        if self.change_24h > ALIGNMENT_THRESHOLD and self.change_7d > ALIGNMENT_THRESHOLD:
            return "alcista"
        if self.change_24h < -ALIGNMENT_THRESHOLD and self.change_7d < -ALIGNMENT_THRESHOLD:
            return "bajista"
        return "indefinida"

    @property
    def range_pct(self) -> float:
        """Range as % of price."""
        return (self.range_size / self.price) * 100 if self.price > 0 else 0


class SignalState:
    """Three-tier signal classification."""
    NO_OPERABLE = "NO_OPERABLE"
    PRE_OPERABLE = "PRE_OPERABLE"
    OPERABLE = "OPERABLE"


@dataclass
class FilterResult:
    """Result of evaluating the 5-condition checklist."""
    position_extreme: bool = False
    timeframe_aligned: bool = False
    volume_signal: bool = False
    directional_clarity: bool = False
    data_sufficient: bool = False
    scenario_type: str = ""       # "long_support", "long_breakout", "short_breakdown"
    entry_zone: str = ""
    invalidation: str = ""
    reason: str = ""
    missing: list = field(default_factory=list)  # which conditions failed

    @property
    def core_conditions(self) -> list:
        return [
            self.position_extreme,
            self.timeframe_aligned,
            self.volume_signal,
            self.directional_clarity,
        ]

    @property
    def core_score(self) -> int:
        return sum(self.core_conditions)

    @property
    def score(self) -> int:
        return self.core_score + int(self.data_sufficient)

    @property
    def signal_state(self) -> str:
        """Strict three-tier classification.
        OPERABLE:      4/4 core + data sufficient
        PRE_OPERABLE:  3/4 core (close, but do NOT act)
        NO_OPERABLE:   <3/4 core (no edge, wait)
        """
        if self.core_score == 4 and self.data_sufficient:
            return SignalState.OPERABLE
        if self.core_score >= 3:
            return SignalState.PRE_OPERABLE
        return SignalState.NO_OPERABLE

    @property
    def operable(self) -> bool:
        """Strict: only True when ALL 4 core conditions + data sufficiency pass."""
        return self.signal_state == SignalState.OPERABLE


class OperationMode:
    """Dual operation mode."""
    STRICT = "estricto"
    EXPLORATION = "exploracion"


class MarketVigilance:
    """
    Silent market monitor. Evaluates conditions on each tick.
    Only produces output when scenario becomes operable.

    Modes:
      estricto (default) — 4/4 core required. PRE_OPERABLE = silence.
      exploracion         — 3/4 core allowed for partial entry (max 25%).
                           Must be activated manually.
    """

    def __init__(self, mode: str = OperationMode.STRICT) -> None:
        self._mode = mode
        self._volume_history: Dict[str, List[float]] = {}
        self._last_alert_time: Dict[str, float] = {}
        self._alert_cooldown = 600  # don't repeat same alert within 10 min
        self._running = False

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch operation mode. Only 'estricto' and 'exploracion' are valid."""
        if mode not in (OperationMode.STRICT, OperationMode.EXPLORATION):
            raise ValueError(f"Modo inválido: {mode}. Usar 'estricto' o 'exploracion'.")
        prev = self._mode
        self._mode = mode
        if prev != mode:
            logger.warning("[VIGILANCE] Modo cambiado: %s → %s", prev, mode)

    def fetch_state(self, symbol: str) -> Optional[MarketState]:
        """Fetch current market state from available sources."""
        data = get_coin_details(symbol)
        if not data.get("success"):
            return None

        return MarketState(
            symbol=symbol,
            price=data.get("price", 0),
            high_24h=data.get("high_24h", 0),
            low_24h=data.get("low_24h", 0),
            change_24h=data.get("change_pct_24h", 0),
            change_7d=data.get("change_pct_7d", 0),
            volume_24h=data.get("volume_24h", 0),
            ath=data.get("ath", 0),
        )

    def evaluate(self, state: MarketState) -> FilterResult:
        """Run the 5-condition filter against current state."""
        result = FilterResult()

        # ── Condition 1: Position in range extreme ──
        result.position_extreme = state.in_extreme
        if not result.position_extreme:
            result.missing.append(
                f"Posición en rango: {state.position_in_range:.0%} (necesita ≤20% o ≥80%)"
            )

        # ── Condition 2: Timeframe alignment ──
        result.timeframe_aligned = state.timeframes_aligned
        if not result.timeframe_aligned:
            result.missing.append(
                f"Alineación temporal: 24h={state.change_24h:+.2f}% vs 7d={state.change_7d:+.2f}% (sentidos opuestos)"
            )

        # ── Condition 3: Volume signal ──
        history = self._volume_history.setdefault(state.symbol, [])
        history.append(state.volume_24h)
        if len(history) > 30:
            history.pop(0)

        if len(history) >= 3:
            avg_vol = sum(history[:-1]) / len(history[:-1])
            result.volume_signal = state.volume_24h > avg_vol * VOLUME_SPIKE_FACTOR
            if not result.volume_signal:
                ratio = state.volume_24h / avg_vol if avg_vol > 0 else 0
                result.missing.append(
                    f"Volumen relativo: {ratio:.2f}x promedio (necesita ≥{VOLUME_SPIKE_FACTOR}x)"
                )
        else:
            result.volume_signal = False
            result.missing.append(
                f"Volumen: historial insuficiente ({len(history)}/3 lecturas mínimas)"
            )

        # ── Condition 4: Directional clarity ──
        result.directional_clarity = state.direction != "indefinida"
        if not result.directional_clarity:
            result.missing.append(
                f"Claridad direccional: dirección indefinida (24h y 7d no concuerdan)"
            )

        # ── Condition 5: Data sufficiency ──
        result.data_sufficient = (
            state.price > 0 and
            state.high_24h > 0 and
            state.low_24h > 0 and
            state.volume_24h > 0
        )
        if not result.data_sufficient:
            result.missing.append("Datos insuficientes (faltan campos de precio/rango/volumen)")

        # ── Scenario classification (only meaningful if at least PRE_OPERABLE) ──
        if result.core_score >= 3:
            if state.in_lower_extreme and state.direction == "alcista":
                result.scenario_type = "long_support"
                result.entry_zone = f"${state.low_24h:,.0f} – ${state.low_24h + state.range_size * 0.15:,.0f}"
                result.invalidation = f"${state.low_24h * 0.99:,.0f} (1% debajo del low 24h)"
                result.reason = "Precio en soporte extremo del rango con timeframes alineados al alza"

            elif state.in_upper_extreme and state.direction == "alcista":
                result.scenario_type = "long_breakout"
                result.entry_zone = f"${state.high_24h:,.0f} – ${state.high_24h * 1.005:,.0f} (tras confirmación)"
                result.invalidation = f"${state.high_24h * 0.98:,.0f} (regreso 2% debajo del high)"
                result.reason = "Precio en zona de ruptura alcista con confirmación de timeframes"

            elif state.in_lower_extreme and state.direction == "bajista":
                result.scenario_type = "short_breakdown"
                result.entry_zone = f"${state.low_24h:,.0f} – ${state.low_24h * 0.995:,.0f} (tras pérdida)"
                result.invalidation = f"${state.low_24h * 1.02:,.0f} (recuperación 2% sobre el low)"
                result.reason = "Precio perdiendo soporte con timeframes alineados a la baja"

            elif state.in_upper_extreme and state.direction == "bajista":
                result.scenario_type = "short_resistance"
                result.entry_zone = f"${state.high_24h:,.0f} (rechazo en resistencia)"
                result.invalidation = f"${state.high_24h * 1.01:,.0f} (1% sobre el high)"
                result.reason = "Precio en resistencia extrema con presión bajista confirmada"

            else:
                result.scenario_type = "none"
                result.reason = "Condiciones parciales pero sin escenario claro"
        else:
            result.scenario_type = "none"
            result.reason = "Condiciones insuficientes"

        return result

    def should_alert(self, symbol: str) -> bool:
        """Check cooldown to avoid repeated alerts."""
        last = self._last_alert_time.get(symbol, 0)
        return (time.time() - last) > self._alert_cooldown

    def record_alert(self, symbol: str) -> None:
        self._last_alert_time[symbol] = time.time()

    def _format_checklist(self, result: FilterResult) -> List[str]:
        """Format the 5-condition checklist with pass/fail markers."""
        return [
            f"    {'✓' if result.position_extreme else '✗'} Posición en extremo del rango",
            f"    {'✓' if result.timeframe_aligned else '✗'} Alineación temporal (24h + 7d)",
            f"    {'✓' if result.volume_signal else '✗'} Volumen relativo elevado",
            f"    {'✓' if result.directional_clarity else '✗'} Claridad direccional",
            f"    {'✓' if result.data_sufficient else '✗'} Datos suficientes",
        ]

    def format_alert(self, state: MarketState, result: FilterResult) -> str:
        """Format OPERABLE scenario alert — only called when all conditions pass."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sym = state.symbol.replace("USDT", "")

        lines = [
            "",
            "═" * 60,
            f"  ⚡ ESCENARIO OPERABLE DETECTADO — {sym}",
            f"  {ts}",
            "═" * 60,
            "",
            f"  Estado:     {result.signal_state}",
            f"  Filtros:    {result.score}/5 (core: {result.core_score}/4)",
            "",
        ]
        lines.extend(self._format_checklist(result))
        lines.extend([
            "",
            "─" * 60,
            "",
            f"  Precio:     ${state.price:,.2f}",
            f"  Rango 24h:  ${state.low_24h:,.0f} – ${state.high_24h:,.0f}",
            f"  Posición:   {state.position_in_range:.0%} del rango",
            f"  24h:        {state.change_24h:+.2f}%",
            f"  7d:         {state.change_7d:+.2f}%",
            f"  Volumen:    ${state.volume_24h/1e9:.1f}B",
            "",
            f"  Tipo:       {result.scenario_type}",
            f"  Dirección:  {state.direction}",
            f"  Motivo:     {result.reason}",
            "",
            f"  Entrada:    {result.entry_zone}",
            f"  Invalidar:  {result.invalidation}",
            "",
            "═" * 60,
            "",
        ])
        return "\n".join(lines)

    def format_pre_operable(self, state: MarketState, result: FilterResult) -> str:
        """Format PRE_OPERABLE notice — behavior depends on mode."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sym = state.symbol.replace("USDT", "")

        if self._mode == OperationMode.EXPLORATION:
            return self._format_exploration_entry(state, result, ts, sym)

        # Strict mode: visible warning, no action
        lines = [
            "",
            "─" * 60,
            f"  ⏳ PRE-OPERABLE — {sym} ({result.core_score}/4 condiciones core)",
            f"  {ts}",
            f"  ⚠ NO ACTUAR — faltan condiciones para escenario válido",
            "─" * 60,
            "",
            f"  Precio:     ${state.price:,.2f} ({state.position_in_range:.0%} del rango)",
            f"  24h:        {state.change_24h:+.2f}%  |  7d: {state.change_7d:+.2f}%",
            "",
        ]
        lines.extend(self._format_checklist(result))
        lines.append("")
        if result.missing:
            lines.append("  Falta:")
            for m in result.missing:
                lines.append(f"    → {m}")
            lines.append("")
        if result.scenario_type and result.scenario_type != "none":
            lines.append(f"  Escenario potencial: {result.scenario_type}")
            lines.append(f"  (se activaría si se cumplen todas las condiciones)")
            lines.append("")
        lines.extend(["─" * 60, ""])
        return "\n".join(lines)

    def _format_exploration_entry(self, state: MarketState, result: FilterResult, ts: str, sym: str) -> str:
        """Format PRE_OPERABLE as exploration entry — partial, risk-controlled."""
        max_pct = int(EXPLORATION_MAX_EXPOSURE * 100)

        lines = [
            "",
            "═" * 60,
            f"  🔍 ENTRADA EXPLORACIÓN — {sym} ({result.core_score}/4 core)",
            f"  {ts}",
            f"  Modo: EXPLORACIÓN — riesgo controlado",
            "═" * 60,
            "",
            f"  Estado:     PRE_OPERABLE (no todas las condiciones cumplidas)",
            f"  Exposición: máximo {max_pct}% del tamaño normal de posición",
            "",
        ]
        lines.extend(self._format_checklist(result))
        lines.extend([
            "",
            "─" * 60,
            "",
            f"  Precio:     ${state.price:,.2f}",
            f"  Rango 24h:  ${state.low_24h:,.0f} – ${state.high_24h:,.0f}",
            f"  Posición:   {state.position_in_range:.0%} del rango",
            f"  24h:        {state.change_24h:+.2f}%",
            f"  7d:         {state.change_7d:+.2f}%",
            f"  Volumen:    ${state.volume_24h/1e9:.1f}B",
            "",
        ])
        if result.scenario_type and result.scenario_type != "none":
            lines.extend([
                f"  Tipo:       {result.scenario_type}",
                f"  Dirección:  {state.direction}",
                f"  Motivo:     {result.reason}",
                "",
                f"  Entrada:    {result.entry_zone}",
                f"  Invalidar:  {result.invalidation}",
                "",
            ])
        if result.missing:
            lines.append("  ⚠ Condiciones faltantes:")
            for m in result.missing:
                lines.append(f"    → {m}")
            lines.append("")
        lines.extend([
            f"  ⚠ RIESGO: entrada parcial ({max_pct}% máx). Si la condición",
            f"    faltante no se confirma, cerrar posición.",
            "",
            "═" * 60,
            "",
        ])
        return "\n".join(lines)

    def run_once(self) -> Optional[str]:
        """Single evaluation cycle. Returns alert string or None (silence)."""
        for symbol in SYMBOLS:
            state = self.fetch_state(symbol)
            if state is None:
                continue

            result = self.evaluate(state)
            sig = result.signal_state

            if sig == SignalState.OPERABLE and self.should_alert(symbol):
                self.record_alert(symbol)
                return self.format_alert(state, result)

            if sig == SignalState.PRE_OPERABLE and self.should_alert(symbol):
                self.record_alert(symbol)
                return self.format_pre_operable(state, result)

            # NO_OPERABLE → silence

        return None

    def run(self) -> None:
        """Main loop. Runs until interrupted."""
        self._running = True
        ts = datetime.now().strftime("%H:%M:%S")
        mode_label = self._mode.upper()
        print(f"[{ts}] Vigilancia activa iniciada. Modo: {mode_label}")
        print(f"[{ts}] Monitoreando {SYMBOLS}. Intervalo: {POLL_INTERVAL}s.")
        if self._mode == OperationMode.EXPLORATION:
            print(f"[{ts}] ⚠ Modo exploración: entradas parciales (máx {int(EXPLORATION_MAX_EXPOSURE*100)}%) en PRE_OPERABLE.")
        else:
            print(f"[{ts}] Silencio = sin oportunidad. Solo alertas cuando hay señal real.")
        print()

        while self._running:
            try:
                alert = self.run_once()
                if alert:
                    print(alert, flush=True)
                # Silent dot every 10 cycles to show it's alive
            except KeyboardInterrupt:
                break
            except Exception as exc:
                logger.error("[VIGILANCE] Error: %s", exc)

            try:
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                break

        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] Vigilancia detenida.")

    def stop(self) -> None:
        self._running = False


# ── Module entry point ──────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Vectrax Market Vigilance")
    parser.add_argument(
        "--exploracion", action="store_true",
        help="Activar modo exploración (entradas parciales en PRE_OPERABLE, máx 25%%)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    mode = OperationMode.EXPLORATION if args.exploracion else OperationMode.STRICT
    v = MarketVigilance(mode=mode)
    try:
        v.run()
    except KeyboardInterrupt:
        v.stop()


if __name__ == "__main__":
    main()
