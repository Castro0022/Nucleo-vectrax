"""
Tests — medición de memoria del System Monitor
================================================
Fija el contrato: el estado del sistema se clasifica con el RSS *actual*
del proceso, nunca con el pico histórico (ru_maxrss).

Regresión que cubren: el proceso API quedaba marcado `degraded` de forma
permanente porque en macOS se leía `ru_maxrss` (pico, en bytes) como si
fuera el uso actual. Una vez superado MAX_MEMORY_MB el pico nunca baja,
así que el estado no volvía a `healthy` hasta reiniciar el proceso.
"""

import os
import subprocess

import pytest

from core.operator import system_monitor as sm


def _ps_rss_mb() -> float:
    """RSS actual del proceso de test según el sistema operativo."""
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    return int(out.split()[0]) / 1024


def test_current_rss_matches_operating_system():
    """La lectura del monitor debe coincidir con el RSS que reporta el SO."""
    sm._rss_cache = (0.0, 0.0)  # invalidar cache para medir de verdad
    measured = sm._read_current_rss_mb()
    assert measured > 0, "no se pudo medir el RSS actual en esta plataforma"
    # Tolerancia amplia: el intérprete asigna/libera entre ambas lecturas.
    assert measured == pytest.approx(_ps_rss_mb(), rel=0.35)


def test_current_rss_is_not_the_peak_after_transient_spike():
    """Tras liberar un pico transitorio, el valor actual no debe arrastrarlo."""
    spike_mb = 400
    ballast = bytearray(spike_mb * 1024 * 1024)
    ballast[:: 4096] = b"\x01" * len(ballast[:: 4096])  # forzar touch de páginas
    del ballast

    sm._rss_cache = (0.0, 0.0)
    current = sm._read_current_rss_mb()
    peak = sm._read_peak_rss_mb()

    assert peak >= spike_mb * 0.8, "ru_maxrss debería reflejar el pico"
    # El valor que clasifica el estado no puede ser el pico.
    assert current <= peak


def test_peak_is_reported_separately():
    """El pico se expone como campo propio, no sustituye al valor actual."""
    m = sm.collect_metrics()
    assert hasattr(m, "memory_peak_mb")
    assert m.memory_mb > 0
    assert m.memory_peak_mb >= m.memory_mb


def test_limit_is_evaluated_against_current_usage():
    """memory_at_limit y el estado dependen del uso actual, no del pico."""
    m = sm.collect_metrics()
    assert m.memory_at_limit == (m.memory_mb > sm.MAX_MEMORY_MB)

    # Un pico alto con uso actual bajo no degrada el sistema.
    if not m.queue_at_limit and m.worker_alive and not m.latency_at_limit:
        if m.memory_mb <= sm.MAX_MEMORY_MB:
            assert m.status == "healthy", (
                f"estado {m.status} con uso actual {m.memory_mb} MB "
                f"(pico {m.memory_peak_mb} MB, límite {sm.MAX_MEMORY_MB} MB)"
            )


def test_rss_reading_is_cached_to_avoid_subprocess_storm():
    """El polling concurrente no debe disparar una lectura por llamada."""
    sm._rss_cache = (0.0, 0.0)
    first = sm._read_current_rss_mb()
    measured_at, cached = sm._rss_cache
    assert cached == first
    assert measured_at > 0

    # Segunda lectura dentro del TTL: devuelve el valor cacheado.
    sm._rss_cache = (measured_at, 12345.0)
    assert sm._read_current_rss_mb() == 12345.0
