#!/usr/bin/env python3
"""
Vectrax Freight Logistics Simulator
======================================
Generates synthetic freight events and feeds them through the Vectrax
ingest pipeline, enabling pattern observation without real data feeds.

Events → Gravity Engine → Patterns → Convergences → Proposals

Simulated variables:
  - 6 regions with distinct demand profiles
  - 12 lanes (origin→destination pairs)
  - 8 carriers with performance variability
  - Seasonal demand curves (weekly + monthly)
  - Random disruptions (weather, strikes, fuel spikes)
  - Truck availability cycles
  - Rate fluctuations driven by supply/demand

Usage:
  python scripts/freight_simulator.py                    # 200 events, 1 cycle
  python scripts/freight_simulator.py --events 500       # 500 events
  python scripts/freight_simulator.py --cycles 3         # 3 learning cycles
  python scripts/freight_simulator.py --tenant t_abc123  # specific tenant
  python scripts/freight_simulator.py --dry-run          # print events, don't ingest

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Tuple

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("freight_simulator")

DOMAIN = "freight_logistics"

# ---------------------------------------------------------------------------
# World model
# ---------------------------------------------------------------------------

REGIONS = ["Southeast", "Northeast", "Midwest", "Southwest", "Pacific", "Mountain"]

CITIES = {
    "Southeast": ["Atlanta", "Miami", "Charlotte", "Nashville"],
    "Northeast": ["New York", "Boston", "Philadelphia", "Newark"],
    "Midwest":   ["Chicago", "Detroit", "Indianapolis", "Columbus"],
    "Southwest": ["Dallas", "Houston", "Phoenix", "San Antonio"],
    "Pacific":   ["Los Angeles", "Seattle", "Portland", "San Francisco"],
    "Mountain":  ["Denver", "Salt Lake City", "Albuquerque", "Boise"],
}

CARRIERS = [
    {"name": "FastHaul", "reliability": 0.92, "base_rate_mult": 1.05},
    {"name": "PrimeFreight", "reliability": 0.88, "base_rate_mult": 0.95},
    {"name": "EcoShip", "reliability": 0.85, "base_rate_mult": 0.85},
    {"name": "RapidTrans", "reliability": 0.90, "base_rate_mult": 1.10},
    {"name": "SteadyLine", "reliability": 0.95, "base_rate_mult": 1.00},
    {"name": "BudgetMove", "reliability": 0.78, "base_rate_mult": 0.75},
    {"name": "EliteLogistics", "reliability": 0.96, "base_rate_mult": 1.20},
    {"name": "RegionalExpress", "reliability": 0.82, "base_rate_mult": 0.90},
]

WEATHER_EVENTS = ["snowstorm", "hurricane", "flooding", "heatwave", "fog", "tornado_warning"]
DELAY_CAUSES = ["weather", "traffic", "mechanical", "driver_shortage", "customs", "port_congestion"]

# Base rates per mile by region (USD)
BASE_RATE_PER_MILE = {
    "Southeast": 2.50, "Northeast": 3.20, "Midwest": 2.30,
    "Southwest": 2.60, "Pacific": 3.50, "Mountain": 2.80,
}

# Seasonal demand multiplier (month 0-11)
MONTHLY_DEMAND = [0.85, 0.80, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.15, 1.30, 1.40]

# Truck fleet per region
FLEET_SIZE = {
    "Southeast": 120, "Northeast": 100, "Midwest": 110,
    "Southwest": 90, "Pacific": 95, "Mountain": 60,
}


# ---------------------------------------------------------------------------
# State — mutable world state for the simulation
# ---------------------------------------------------------------------------

class WorldState:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.day = 0
        self.month = 0
        self.events_generated = 0

        # Per-region state
        self.truck_available = {r: int(FLEET_SIZE[r] * 0.7) for r in REGIONS}
        self.demand_level = {r: "normal" for r in REGIONS}
        self.active_weather = {}  # region → {event, severity, remaining_days}
        self.rates = {r: BASE_RATE_PER_MILE[r] for r in REGIONS}
        self.carrier_delays = {c["name"]: 0 for c in CARRIERS}

    def advance_day(self):
        self.day += 1
        self.month = (self.day // 30) % 12

        # Truck availability fluctuation
        for r in REGIONS:
            base = FLEET_SIZE[r]
            seasonal = MONTHLY_DEMAND[self.month]
            noise = self.rng.gauss(0, 0.05)
            available = max(5, int(base * (1.1 - seasonal * 0.3 + noise)))
            self.truck_available[r] = available

            # Update demand level
            util = 1.0 - (available / base)
            self.demand_level[r] = (
                "critical" if util > 0.85 else
                "high" if util > 0.7 else
                "normal" if util > 0.4 else "low"
            )

        # Rate adjustment based on demand
        for r in REGIONS:
            demand_mult = MONTHLY_DEMAND[self.month]
            supply_factor = self.truck_available[r] / FLEET_SIZE[r]
            self.rates[r] = round(
                BASE_RATE_PER_MILE[r] * demand_mult / max(supply_factor, 0.3),
                2,
            )

        # Weather events (5% chance per region per day)
        for r in REGIONS:
            if r in self.active_weather:
                self.active_weather[r]["remaining_days"] -= 1
                if self.active_weather[r]["remaining_days"] <= 0:
                    del self.active_weather[r]
            elif self.rng.random() < 0.05:
                self.active_weather[r] = {
                    "event": self.rng.choice(WEATHER_EVENTS),
                    "severity": self.rng.choice(["moderate", "severe", "extreme"]),
                    "remaining_days": self.rng.randint(1, 4),
                }

        # Carrier delay accumulation
        for c in CARRIERS:
            if self.rng.random() < (1 - c["reliability"]):
                self.carrier_delays[c["name"]] += 1
            else:
                self.carrier_delays[c["name"]] = max(0, self.carrier_delays[c["name"]] - 1)


# ---------------------------------------------------------------------------
# Event generators
# ---------------------------------------------------------------------------

def _pick_lane(ws: WorldState) -> Tuple[str, str, str, str]:
    """Pick origin and destination in different regions."""
    r1, r2 = ws.rng.sample(REGIONS, 2)
    origin = ws.rng.choice(CITIES[r1])
    dest = ws.rng.choice(CITIES[r2])
    return origin, dest, r1, r2


def _pick_carrier(ws: WorldState) -> Dict:
    return ws.rng.choice(CARRIERS)


def gen_quote_request(ws: WorldState) -> Dict[str, Any]:
    origin, dest, r1, r2 = _pick_lane(ws)
    carrier = _pick_carrier(ws)
    distance = ws.rng.randint(200, 2000)
    rate = round(distance * ws.rates[r1] * carrier["base_rate_mult"], 2)
    return {
        "event_type": "quote_request",
        "data": {
            "origin": origin, "destination": dest,
            "weight_kg": ws.rng.randint(500, 25000),
            "carrier": carrier["name"],
            "rate_usd": rate,
            "region": r1,
            "distance_miles": distance,
        },
    }


def gen_load_booking(ws: WorldState) -> Dict[str, Any]:
    origin, dest, r1, r2 = _pick_lane(ws)
    carrier = _pick_carrier(ws)
    rate = round(ws.rng.randint(300, 1800) * ws.rates[r1] / BASE_RATE_PER_MILE[r1], 2)
    return {
        "event_type": "load_booking",
        "data": {
            "origin": origin, "destination": dest,
            "carrier": carrier["name"],
            "rate_usd": rate,
            "trucks_available": ws.truck_available[r1],
            "region": r1,
        },
    }


def gen_delivery(ws: WorldState) -> Dict[str, Any]:
    origin, dest, r1, r2 = _pick_lane(ws)
    carrier = _pick_carrier(ws)
    transit = ws.rng.randint(1, 7)
    on_time = ws.rng.random() < carrier["reliability"]
    # Weather affects on-time
    if r1 in ws.active_weather or r2 in ws.active_weather:
        on_time = ws.rng.random() < (carrier["reliability"] * 0.6)
    return {
        "event_type": "delivery_complete",
        "data": {
            "origin": origin, "destination": dest,
            "carrier": carrier["name"],
            "transit_days": transit,
            "on_time": on_time,
            "region": r1,
        },
    }


def gen_delay(ws: WorldState) -> Dict[str, Any]:
    origin, dest, r1, r2 = _pick_lane(ws)
    carrier = _pick_carrier(ws)
    cause = "weather" if (r1 in ws.active_weather or r2 in ws.active_weather) else ws.rng.choice(DELAY_CAUSES)
    hours = ws.rng.randint(2, 48)
    if cause == "weather" and r1 in ws.active_weather:
        sev = ws.active_weather[r1]["severity"]
        hours = {"moderate": ws.rng.randint(4, 12), "severe": ws.rng.randint(12, 36), "extreme": ws.rng.randint(24, 72)}[sev]
    return {
        "event_type": "delay_reported",
        "data": {
            "origin": origin, "destination": dest,
            "carrier": carrier["name"],
            "delay_hours": hours,
            "cause": cause,
            "region": r1,
        },
    }


def gen_rate_change(ws: WorldState) -> Dict[str, Any]:
    r = ws.rng.choice(REGIONS)
    old = BASE_RATE_PER_MILE[r]
    new = ws.rates[r]
    change = round((new - old) / old * 100, 1)
    return {
        "event_type": "rate_change",
        "data": {
            "lane": f"{ws.rng.choice(CITIES[r])}→{ws.rng.choice(CITIES[ws.rng.choice([x for x in REGIONS if x != r])])}",
            "old_rate": old, "new_rate": new,
            "change_pct": change,
            "demand_level": ws.demand_level[r],
            "region": r,
        },
    }


def gen_capacity(ws: WorldState) -> Dict[str, Any]:
    r = ws.rng.choice(REGIONS)
    total = FLEET_SIZE[r]
    avail = ws.truck_available[r]
    return {
        "event_type": "capacity_update",
        "data": {
            "region": r,
            "trucks_available": avail,
            "trucks_total": total,
            "utilization_pct": round((1 - avail / total) * 100, 1),
        },
    }


def gen_weather(ws: WorldState) -> Dict[str, Any]:
    # Pick region with active weather, or generate a new one
    affected = [r for r in REGIONS if r in ws.active_weather]
    if affected:
        r = ws.rng.choice(affected)
        w = ws.active_weather[r]
    else:
        r = ws.rng.choice(REGIONS)
        w = {"event": ws.rng.choice(WEATHER_EVENTS), "severity": "moderate"}

    n_lanes = ws.rng.randint(2, 8)
    return {
        "event_type": "weather_event",
        "data": {
            "event_type": w["event"],
            "region": r,
            "severity": w.get("severity", "moderate"),
            "affected_lanes": n_lanes,
            "duration_hours": ws.rng.randint(6, 72),
        },
    }


def gen_empty_miles(ws: WorldState) -> Dict[str, Any]:
    origin, dest, r1, r2 = _pick_lane(ws)
    carrier = _pick_carrier(ws)
    return {
        "event_type": "empty_miles",
        "data": {
            "origin": origin, "destination": dest,
            "carrier": carrier["name"],
            "miles": ws.rng.randint(50, 500),
            "load_density": round(ws.rng.uniform(0.1, 0.8), 2),
            "region": r1,
        },
    }


# Event type weights (probability distribution)
EVENT_GENERATORS = [
    (gen_quote_request, 0.20),
    (gen_load_booking,  0.18),
    (gen_delivery,      0.18),
    (gen_delay,         0.12),
    (gen_rate_change,   0.10),
    (gen_capacity,      0.08),
    (gen_weather,       0.06),
    (gen_empty_miles,   0.08),
]


def generate_event(ws: WorldState) -> Dict[str, Any]:
    """Generate one random freight event based on weighted probabilities."""
    r = ws.rng.random()
    cumulative = 0.0
    for gen_fn, weight in EVENT_GENERATORS:
        cumulative += weight
        if r <= cumulative:
            return gen_fn(ws)
    return EVENT_GENERATORS[0][0](ws)  # fallback


# ---------------------------------------------------------------------------
# Scenario injectors — create correlated events for pattern formation
# ---------------------------------------------------------------------------

def inject_pricing_opportunity(ws: WorldState) -> List[Dict]:
    """Inject correlated events: high demand + low trucks + rising rates."""
    r = ws.rng.choice(REGIONS)
    ws.truck_available[r] = max(3, int(FLEET_SIZE[r] * 0.15))
    ws.demand_level[r] = "critical"
    ws.rates[r] = round(BASE_RATE_PER_MILE[r] * 1.8, 2)

    events = [
        {"event_type": "capacity_update", "data": {
            "region": r, "trucks_available": ws.truck_available[r],
            "trucks_total": FLEET_SIZE[r], "utilization_pct": 85.0}},
        {"event_type": "rate_change", "data": {
            "lane": f"{ws.rng.choice(CITIES[r])}→{ws.rng.choice(CITIES[ws.rng.choice([x for x in REGIONS if x != r])])}",
            "old_rate": BASE_RATE_PER_MILE[r], "new_rate": ws.rates[r],
            "change_pct": 80.0, "demand_level": "critical", "region": r}},
        gen_quote_request(ws),
        gen_load_booking(ws),
    ]
    return events


def inject_capacity_risk(ws: WorldState) -> List[Dict]:
    """Inject: weather + regional saturation + delays."""
    r = ws.rng.choice(REGIONS)
    ws.active_weather[r] = {"event": "snowstorm", "severity": "severe", "remaining_days": 3}
    ws.truck_available[r] = max(2, int(FLEET_SIZE[r] * 0.10))

    events = [
        gen_weather(ws),
        {"event_type": "capacity_update", "data": {
            "region": r, "trucks_available": ws.truck_available[r],
            "trucks_total": FLEET_SIZE[r], "utilization_pct": 90.0}},
        gen_delay(ws),
        gen_delay(ws),
    ]
    return events


def inject_route_optimization(ws: WorldState) -> List[Dict]:
    """Inject: empty miles + low density + same region."""
    r = ws.rng.choice(REGIONS)
    carrier = _pick_carrier(ws)
    events = []
    for _ in range(3):
        o = ws.rng.choice(CITIES[r])
        d = ws.rng.choice(CITIES[r])
        events.append({"event_type": "empty_miles", "data": {
            "origin": o, "destination": d, "carrier": carrier["name"],
            "miles": ws.rng.randint(100, 400), "load_density": round(ws.rng.uniform(0.1, 0.3), 2),
            "region": r}})
    return events


SCENARIOS = [
    (inject_pricing_opportunity, "pricing_opportunity"),
    (inject_capacity_risk, "capacity_risk"),
    (inject_route_optimization, "route_optimization"),
]


# ---------------------------------------------------------------------------
# Main simulation loop
# ---------------------------------------------------------------------------

def run_simulation(
    n_events: int = 200,
    n_cycles: int = 1,
    tenant_id: str = "",
    dry_run: bool = False,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run the freight simulation and feed events into Vectrax."""

    ws = WorldState(seed=seed)
    results = {
        "events_generated": 0,
        "events_ingested": 0,
        "scenarios_injected": 0,
        "errors": 0,
        "cycles": n_cycles,
    }

    # Create or use tenant
    if not tenant_id and not dry_run:
        try:
            from core.tenant import create_tenant
            t = create_tenant(name="FreightSim", plan="creator", domain=DOMAIN)
            tenant_id = t["tenant_id"]
            logger.info("Created tenant: %s (key: %s...)", tenant_id, t["api_key"][:12])
            if t.get("domain_priors_seeded"):
                logger.info("  → Seeded with %d domain priors!", t["domain_priors_seeded"])
        except Exception as exc:
            logger.warning("Tenant creation failed: %s — using 'sim_freight'", exc)
            tenant_id = "sim_freight"

    logger.info("")
    logger.info("=" * 60)
    logger.info("  VECTRAX FREIGHT SIMULATOR")
    logger.info("  Events: %d | Cycles: %d | Tenant: %s", n_events, n_cycles, tenant_id or "(dry-run)")
    logger.info("  Seed: %d | Domain: %s", seed, DOMAIN)
    logger.info("=" * 60)
    logger.info("")

    for cycle in range(n_cycles):
        logger.info("── Cycle %d/%d ──", cycle + 1, n_cycles)
        cycle_ingested = 0
        events_this_cycle = n_events

        for i in range(events_this_cycle):
            # Advance simulation day every 10 events
            if i % 10 == 0:
                ws.advance_day()

            # Inject a scenario every 25 events for pattern formation
            if i > 0 and i % 25 == 0:
                scenario_fn, scenario_name = ws.rng.choice(SCENARIOS)
                scenario_events = scenario_fn(ws)
                for se in scenario_events:
                    results["events_generated"] += 1
                    results["scenarios_injected"] += 1
                    if dry_run:
                        logger.info("  [SCENARIO:%s] %s", scenario_name, se["event_type"])
                    else:
                        _ingest(tenant_id, se, results)
                        cycle_ingested += 1

            # Normal event
            event = generate_event(ws)
            results["events_generated"] += 1

            if dry_run:
                if i < 10 or i % 50 == 0:
                    logger.info("  [%d] %s: %s", i, event["event_type"],
                                json.dumps(event["data"], default=str)[:120])
            else:
                _ingest(tenant_id, event, results)
                cycle_ingested += 1

        logger.info("  Cycle %d: %d events ingested", cycle + 1, cycle_ingested)

        # Try to elevate patterns after each cycle
        if not dry_run:
            try:
                from core.domain_knowledge import try_elevate_from_gravity
                elevated = try_elevate_from_gravity(DOMAIN, tenant_id)
                if elevated:
                    logger.info("  → %d patterns elevated to domain library", elevated)
            except Exception:
                pass

    # Final summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("  SIMULATION COMPLETE")
    logger.info("  Generated: %d events (%d scenario injections)",
                results["events_generated"], results["scenarios_injected"])
    logger.info("  Ingested: %d | Errors: %d", results["events_ingested"], results["errors"])

    if not dry_run:
        _print_domain_status()

    logger.info("=" * 60)
    return results


def _ingest(tenant_id: str, event: Dict, results: Dict) -> None:
    """Ingest one event through the Vectrax pipeline."""
    try:
        from core.domain_ingester import ingest_event
        r = ingest_event(
            tenant_id=tenant_id,
            domain=DOMAIN,
            event_type=event["event_type"],
            data=event["data"],
        )
        if r.get("success"):
            results["events_ingested"] += 1
        else:
            results["errors"] += 1
    except Exception as exc:
        results["errors"] += 1
        logger.debug("Ingest error: %s", exc)


def _print_domain_status() -> None:
    """Print the current state of the freight domain in the gravity engine."""
    try:
        from core.learn.gravity_engine import get_gravity_index
        gi = get_gravity_index()
        records = [r for r in gi.all_records() if r.domain == DOMAIN]
        logger.info("")
        logger.info("  Domain '%s' in gravity engine:", DOMAIN)
        logger.info("    Stars: %d", len(records))
        mature = [r for r in records if r.hits >= 15]
        logger.info("    Mature (hits>=15): %d", len(mature))
        for r in sorted(mature, key=lambda x: x.hits, reverse=True)[:8]:
            logger.info("      %s | hits=%d cc=%.3f freq=%.3f",
                        r.intent or r.fingerprint[:30], r.hits, r.cc_score, r.freq)
    except Exception:
        pass

    try:
        from core.domain_knowledge import get_domain_summary
        s = get_domain_summary(DOMAIN)
        if s.get("patterns", 0) > 0:
            logger.info("")
            logger.info("  Domain library:")
            logger.info("    Patterns: %d (strong: %d)", s["patterns"], s.get("strong_patterns", 0))
            logger.info("    Avg WR: %.1f%% | Avg E: %+.3f%%", s["avg_win_rate"], s["avg_expectancy"])
            logger.info("    Total observations: %d", s["total_observations"])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vectrax Freight Logistics Simulator")
    parser.add_argument("--events", type=int, default=200, help="Events per cycle (default: 200)")
    parser.add_argument("--cycles", type=int, default=1, help="Learning cycles (default: 1)")
    parser.add_argument("--tenant", type=str, default="", help="Tenant ID (auto-created if empty)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--dry-run", action="store_true", help="Print events without ingesting")
    args = parser.parse_args()

    run_simulation(
        n_events=args.events,
        n_cycles=args.cycles,
        tenant_id=args.tenant,
        dry_run=args.dry_run,
        seed=args.seed,
    )
