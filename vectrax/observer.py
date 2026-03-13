"""
Vectrax Observer — permanent observer mode.

When active, every line of input (from stdin, a file tail, or an API) is
automatically ingested as a star in the creator channel.

Modes:
  interactive  — read from stdin, one event per line (Ctrl-C to stop)
  pipe         — same but expects piped input (no prompts)
  file         — tail a log file and ingest new lines as they appear

Observer lifecycle:
  start  → config.activate_observer()
  ingest → engine.ingest(text, channel=CHANNEL_CREATOR, owner=CREATOR_OWNER)
  wake   → if wake word detected, log a special activation star
  stop   → config.deactivate_observer() on clean exit
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import datetime
from typing import Callable, Optional

from vectrax import config as cfg
from vectrax.identity import CHANNEL_CREATOR, CREATOR_OWNER
from vectrax import wakeword as ww


# ---------------------------------------------------------------------------
# Observer loop
# ---------------------------------------------------------------------------

class Observer:
    """
    Permanent observer: ingests every event as a foundational creator star.
    Wake word detection is active on every line.
    """

    def __init__(
        self,
        pipe_mode: bool = False,
        on_star: Optional[Callable] = None,
    ):
        self.pipe_mode = pipe_mode
        self.on_star = on_star          # callback(star) for each ingested star
        self._running = False
        self._count = 0

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        cfg.activate_observer()
        self._running = True
        self._count = 0
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

        if not self.pipe_mode:
            print(
                f"\n◈ VECTRAX OBSERVER ACTIVE\n"
                f"  Creator: {cfg.get('creator_identity')}\n"
                f"  Every event becomes a star.\n"
                f"  Type an event and press Enter. Ctrl-C to stop.\n"
            )

        self._loop()

    def stop(self) -> None:
        self._running = False
        cfg.deactivate_observer()
        if not self.pipe_mode:
            print(f"\n◈ Observer stopped. {self._count} events ingested this session.")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        from vectrax.engine import ingest as _ingest

        while self._running:
            try:
                if not self.pipe_mode:
                    line = input("  › ")
                else:
                    line = sys.stdin.readline()
                    if not line:          # EOF — pipe closed
                        break

                text = line.strip()
                if not text:
                    continue

                # Check for wake word first
                if ww.detect_in_text(text, CHANNEL_CREATOR):
                    self._on_wake(text)
                    continue              # wake event is its own star, handled inside

                # Ingest as creator star
                star = _ingest(
                    text,
                    success=False,
                    channel=CHANNEL_CREATOR,
                    owner=CREATOR_OWNER,
                )
                self._count += 1
                cfg.increment_events(1)

                if self.on_star:
                    self.on_star(star)
                elif not self.pipe_mode:
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"  [{ts}] ★ [{star.id[:8]}] "
                        f"layer={star.layer} gravity={star.gravity_score:.4f}"
                    )

            except EOFError:
                break
            except Exception as exc:
                if not self.pipe_mode:
                    print(f"  [observer error] {exc}")

        self.stop()

    # ------------------------------------------------------------------
    # Wake word
    # ------------------------------------------------------------------

    def _on_wake(self, text: str) -> None:
        from vectrax.engine import ingest as _ingest

        result = ww.trigger_activation(source="creator")
        ts = datetime.fromtimestamp(result["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")

        # Ingest the activation moment as a special foundational star
        activation_text = (
            f"[WAKE ACTIVATION] Observer mode activated by creator. "
            f"Timestamp: {ts}"
        )
        star = _ingest(
            activation_text,
            success=True,
            channel=CHANNEL_CREATOR,
            owner=CREATOR_OWNER,
        )
        self._count += 1
        cfg.increment_events(1)

        if not self.pipe_mode:
            print(
                f"\n  ◈ ═══════════════════════════════════════\n"
                f"  ◈  VECTRAX DESPIERTO  —  {ts}\n"
                f"  ◈  Creator: {cfg.get('creator_identity')}\n"
                f"  ◈  Observer: ACTIVE  |  Star: [{star.id[:8]}]\n"
                f"  ◈ ═══════════════════════════════════════\n"
            )

    # ------------------------------------------------------------------
    # Signal handler
    # ------------------------------------------------------------------

    def _handle_interrupt(self, signum, frame) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# File tail observer
# ---------------------------------------------------------------------------

def tail_file(path: str, on_line: Callable[[str], None]) -> None:
    """
    Tail a file and call on_line() for each new line.
    Blocks until interrupted.
    """
    import os

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)        # start at end — ignore history
        while True:
            line = f.readline()
            if line:
                on_line(line.rstrip("\n"))
            else:
                time.sleep(0.2)
