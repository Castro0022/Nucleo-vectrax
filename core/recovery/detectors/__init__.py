"""
Recovery detectors — read-only probes that emit HealthEvent.

Each module in this package defines a probe function (and optionally
helpers).  Probes are registered with the orchestrator via registry.py.

Contract for probes:
  - Signature: () -> HealthEvent
  - Must NEVER raise (catch internally and return DEGRADED with evidence).
  - Must NEVER modify state of the system being observed.
  - Should complete in a fraction of the interval (e.g. probe every 60s
    should complete in < 10s).
"""
