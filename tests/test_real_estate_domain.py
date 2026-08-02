"""
Tests — Dominio florida_real_estate (connectors/real_estate).

Herméticos: sin red y sin escritura a producción.
- RealEstateOutcomeAdapter: WIN (venta) / LOSS (expiración) / PENDING (actividad).
- SimulatorAdapter: eventos statewide con variedad de zonas (sin prioridad geo).
- AttomProvider: gated por ATTOM_API_KEY (sin key → inerte) + _normalize_sale.
- get_provider(): selección por env.
- verify_events(record=False): DomainScore desde el núcleo invariante.

Run:  python -m pytest tests/test_real_estate_domain.py -v
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.learn.outcome_adapter import OutcomeStatus, Prediction
from connectors.real_estate.base import RealEstateEvent
from connectors.real_estate.real_estate_outcome_adapter import RealEstateOutcomeAdapter
from connectors.real_estate.simulator_adapter import SimulatorAdapter
from connectors.real_estate import attom_provider as attom
from connectors.real_estate import rentcast_provider as rentcast
from connectors.real_estate.verification_cycle import verify_events


def _pred(subject="33139|condo|luxury"):
    return Prediction(domain="florida_real_estate", subject=subject, predicted="sells")


# ── 1. OutcomeAdapter (verdad objetiva) ─────────────────────────────────

def test_outcome_adapter_win_loss_pending():
    a = RealEstateOutcomeAdapter()
    assert a.domain == "florida_real_estate"

    win = a.resolve(_pred(), {"event_type": "sale_closed",
                              "list_price": 500000, "sold_price": 520000, "dom": 40})
    loss = a.resolve(_pred(), {"event_type": "expired", "dom": 210})
    pend = a.resolve(_pred(), {"event_type": "new_listing", "list_price": 500000})

    assert win.status is OutcomeStatus.WIN
    assert abs(win.score - 0.04) < 1e-6            # premium sold/list - 1
    assert loss.status is OutcomeStatus.LOSS and loss.score == -1.0
    assert pend.status is OutcomeStatus.PENDING


def test_outcome_adapter_sale_below_list_is_win_with_negative_score():
    a = RealEstateOutcomeAdapter()
    out = a.resolve(_pred(), {"event_type": "sale_closed",
                              "list_price": 500000, "sold_price": 470000})
    assert out.status is OutcomeStatus.WIN           # se vendió (absorción)
    assert out.score < 0                              # pero por debajo de lista


# ── 2. Simulator statewide (sin prioridad geográfica) ───────────────────

def test_simulator_statewide_variety():
    sim = SimulatorAdapter(seed=42)
    assert sim.health_check()["healthy"] is True
    events = list(sim.stream_events(200))
    assert len(events) == 200

    valid = {"new_listing", "price_change", "status_change", "open_house",
             "sale_closed", "expired", "withdrawn"}
    assert all(e.event_type in valid for e in events)
    assert all(e.zone for e in events)               # zona siempre presente

    zones = [e.zone for e in events]
    distinct = set(zones)
    assert len(distinct) >= 8                          # repartido por el estado
    top_share = max(zones.count(z) for z in distinct) / len(zones)
    assert top_share < 0.5                             # ninguna zona domina a priori


# ── 3. ATTOM gating + normalización ─────────────────────────────────────

def test_attom_inert_without_key(monkeypatch):
    monkeypatch.delenv("ATTOM_API_KEY", raising=False)
    p = attom.AttomProvider()
    assert p.provider_name == "attom"
    hc = p.health_check()
    assert hc["healthy"] is False                      # sin key → inerte
    assert list(p.stream_events(5)) == []              # y no llama a la red


def test_attom_normalize_sale():
    rec = {
        "address": {"postal1": "33139"},
        "sale": {"amount": {"saleamt": 545000, "salerecdate": "2026-01-05"}},
        "summary": {"proptype": "CONDOMINIUM"},
        "building": {"size": {"livingsize": 1200}},
    }
    ev = attom._normalize_sale(rec)
    assert ev is not None
    assert ev.event_type == "sale_closed"
    assert ev.zone == "33139"
    assert ev.data["sold_price"] == 545000
    assert "condominium" in ev.data["type"]
    # sin monto de venta → None
    assert attom._normalize_sale({"address": {"postal1": "33139"}, "sale": {}}) is None


# ── 3b. RentCast gating + normalización ──────────────────────────

def test_rentcast_inert_without_key(monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    p = rentcast.RentCastProvider()
    assert p.provider_name == "rentcast"
    assert p.health_check()["healthy"] is False        # sin key → inerte
    assert list(p.stream_events(5)) == []              # y no llama a la red


def test_rentcast_normalize_listing():
    active = {"zipCode": "33139", "propertyType": "Condo", "price": 800000,
             "status": "Active", "daysOnMarket": 21, "bedrooms": 2}
    ev = rentcast._normalize_listing(active)
    assert ev.event_type == "new_listing"
    assert ev.zone == "33139" and ev.data["tier"] == "luxury"
    assert ev.data["list_price"] == 800000 and ev.data["status"] == "active"
    # Inactive / removido → status_change (off_market), no terminal
    inactive = {"zipCode": "32801", "propertyType": "SFR", "price": 300000,
               "status": "Inactive", "removedDate": "2026-02-01"}
    ev2 = rentcast._normalize_listing(inactive)
    assert ev2.event_type == "status_change" and ev2.data["status"] == "off_market"


def test_rentcast_normalize_property_sale():
    rec = {"zipCode": "33602", "propertyType": "Single Family",
           "lastSalePrice": 615000, "lastSaleDate": "2026-01-20", "squareFootage": 1800}
    ev = rentcast._normalize_property_sale(rec)
    assert ev is not None and ev.event_type == "sale_closed"
    assert ev.zone == "33602" and ev.data["sold_price"] == 615000
    assert ev.data["tier"] == "mid"
    # sin última venta → None
    assert rentcast._normalize_property_sale({"zipCode": "33602"}) is None


# ── 4. get_provider() por env ───────────────────────────────────

def test_get_provider_selection(monkeypatch):
    from connectors.real_estate import get_provider
    monkeypatch.setenv("REAL_ESTATE_FEED_PROVIDER", "attom")
    assert get_provider(force_new=True).provider_name == "attom"
    monkeypatch.setenv("REAL_ESTATE_FEED_PROVIDER", "rentcast")
    assert get_provider(force_new=True).provider_name == "rentcast"
    monkeypatch.setenv("REAL_ESTATE_FEED_PROVIDER", "simulator")
    assert get_provider(force_new=True).provider_name == "simulator"


# ── 5. verify_events → DomainScore (sin escribir en prod) ───────────────

def test_verify_events_domain_score():
    events = [
        RealEstateEvent("sale_closed", {"zone": "33139", "type": "condo", "tier": "luxury",
                                        "list_price": 500000, "sold_price": 515000}),
        RealEstateEvent("sale_closed", {"zone": "32801", "type": "sfr", "tier": "mid",
                                        "list_price": 400000, "sold_price": 380000}),
        RealEstateEvent("expired", {"zone": "33602", "type": "condo", "tier": "entry"}),
        RealEstateEvent("new_listing", {"zone": "34102", "type": "sfr", "tier": "luxury"}),
    ]
    score = verify_events(events, record=False)        # record=False → hermético
    assert score.domain == "florida_real_estate"
    assert score.n_decisive == 3                        # 2 ventas + 1 expiración
    assert (score.wins, score.losses) == (2, 1)
