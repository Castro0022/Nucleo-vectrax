"""
connectors/etoro/live_position_store.py — Registro persistido de
posiciones LIVE.

`PositionRecord`/`EntryThesis` (core/trading/contracts.py) son
deliberadamente agnósticos del broker: no cargan `broker_position_id`
ni `instrument_id`, y no cargan `entry_price`/`hard_stop_price` (esos
viven en `PaperTrade` para PAPER; LIVE no tenía un equivalente hasta
ahora). Este archivo es el complemento LIVE de esos dos huecos —
exactamente lo mínimo que le falta al `PositionRecord` puro para poder
cerrarse contra un broker real.

Igual que `execution_adapter.py`: JSONL append-only,
`~/.vectrax/etoro_live_positions.jsonl`, última línea por `position_id`
= estado vigente. Sobrevive a un reinicio del proceso.

Creado: 2026-09-25
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.trading.contracts import PositionRecord, PositionStatus

logger = logging.getLogger("vectrax.etoro.live_position_store")

_LIVE_POSITIONS_FILE = os.path.join(
    os.path.expanduser("~"), ".vectrax", "etoro_live_positions.jsonl"
)


@dataclass(frozen=True)
class LivePositionEntry:
    """`PositionRecord` + lo que le falta para hablar con el broker."""

    record: PositionRecord
    broker_position_id: str
    instrument_id: int
    entry_price: float
    hard_stop_price: float

    def to_dict(self) -> Dict:
        return {
            "record": self.record.to_dict(),
            "broker_position_id": self.broker_position_id,
            "instrument_id": self.instrument_id,
            "entry_price": self.entry_price,
            "hard_stop_price": self.hard_stop_price,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "LivePositionEntry":
        return cls(
            record=PositionRecord.from_dict(d["record"]),
            broker_position_id=d["broker_position_id"],
            instrument_id=int(d["instrument_id"]),
            entry_price=float(d["entry_price"]),
            hard_stop_price=float(d["hard_stop_price"]),
        )


def save(entry: LivePositionEntry) -> None:
    try:
        os.makedirs(os.path.dirname(_LIVE_POSITIONS_FILE), exist_ok=True)
        with open(_LIVE_POSITIONS_FILE, "a") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("live_position_store: fallo al persistir %s: %s", entry.record.position_id, exc)


def _all_latest() -> Dict[str, LivePositionEntry]:
    """Última entrada conocida por `position_id`."""
    latest: Dict[str, LivePositionEntry] = {}
    if not os.path.exists(_LIVE_POSITIONS_FILE):
        return latest
    try:
        with open(_LIVE_POSITIONS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    entry = LivePositionEntry.from_dict(d)
                    latest[entry.record.position_id] = entry
                except Exception:
                    continue
    except Exception:
        pass
    return latest


def get(position_id: str) -> Optional[LivePositionEntry]:
    return _all_latest().get(position_id)


def open_positions() -> List[LivePositionEntry]:
    """Todas las posiciones LIVE cuyo último estado conocido no es CLOSED."""
    return [
        entry for entry in _all_latest().values()
        if entry.record.status != PositionStatus.CLOSED
    ]
