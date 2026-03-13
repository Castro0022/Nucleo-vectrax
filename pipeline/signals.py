"""Vectrax - Paso 1: Captura de señales de usuarios."""

from __future__ import annotations

from models.schemas import Signal, SignalCategory


class SignalCollector:
    """Recopila y procesa señales generadas por usuarios."""

    def __init__(self) -> None:
        self._signals: list[Signal] = []

    @property
    def signals(self) -> list[Signal]:
        return list(self._signals)

    @property
    def count(self) -> int:
        return len(self._signals)

    def add(self, signal: Signal) -> None:
        """Agrega una señal al colector."""
        self._signals.append(signal)

    def add_many(self, signals: list[Signal]) -> None:
        """Agrega múltiples señales."""
        self._signals.extend(signals)

    def filter_by_category(self, category: SignalCategory) -> list[Signal]:
        """Filtra señales por categoría."""
        return [s for s in self._signals if s.category == category]

    def get_categories(self) -> dict[SignalCategory, int]:
        """Retorna conteo de señales por categoría."""
        counts: dict[SignalCategory, int] = {}
        for s in self._signals:
            counts[s.category] = counts.get(s.category, 0) + 1
        return counts

    def get_weighted_signals(self) -> list[Signal]:
        """Retorna señales ordenadas por peso (mayor primero)."""
        return sorted(self._signals, key=lambda s: s.weight, reverse=True)

    def clear(self) -> None:
        """Limpia todas las señales."""
        self._signals.clear()
