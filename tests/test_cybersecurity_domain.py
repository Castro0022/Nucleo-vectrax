"""
tests/test_cybersecurity_domain.py — Suite hermética del dominio cybersecurity.

Sin red: usa el simulador y HTTP mockeado. Aísla el estado en un vault temporal
(VECTRAX_VAULT_DIR / CYBER_SEEN_DB) para no tocar producción.

Creador: Mario Bravo Castro
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from connectors.cybersecurity import dimensions as dim
from connectors.cybersecurity import seen_ledger


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def temp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(vault))
    monkeypatch.setenv("CYBER_SEEN_DB", str(tmp_path / "cyber_seen.db"))
    seen_ledger.clear()
    yield vault
    seen_ledger.clear()


# ── Dimensiones ────────────────────────────────────────────────────────────────

def test_severity_tier():
    assert dim.severity_tier({"base_severity": "critical"}) == "CRITICAL"
    assert dim.severity_tier({"base_severity": "HIGH"}) == "HIGH"
    assert dim.severity_tier({"base_severity": ""}) is None
    assert dim.severity_tier({}) is None
    assert dim.severity_tier({"base_severity": "banana"}) is None


def test_attack_vector_direct_and_from_vector_and_v2():
    assert dim.attack_vector({"attack_vector": "NETWORK"}) == "NETWORK"
    assert dim.attack_vector({"attack_vector": "N"}) == "NETWORK"
    assert dim.attack_vector({"cvss_vector": "CVSS:3.1/AV:L/AC:L/PR:N"}) == "LOCAL"
    assert dim.attack_vector({"access_vector": "ADJACENT_NETWORK"}) == "ADJACENT"
    assert dim.attack_vector({}) is None


def test_vulnerability_class_mapped_other_and_none():
    assert dim.vulnerability_class({"cwes": ["CWE-79"]}) == "xss"
    assert dim.vulnerability_class({"cwes": ["CWE-89"]}) == "sql_injection"
    # CWE real pero fuera de la taxonomía → other_weakness
    assert dim.vulnerability_class({"cwes": ["CWE-1321"]}) == "other_weakness"
    # placeholders → None
    assert dim.vulnerability_class({"cwes": ["NVD-CWE-noinfo"]}) is None
    assert dim.vulnerability_class({"cwes": []}) is None


def test_product_family_map_override_and_fallback():
    # override producto
    assert dim.product_family({"cpe_products": [["microsoft", "windows"]]}) == "Microsoft Windows"
    # vendor family (producto no en override)
    assert dim.product_family({"cpe_products": [["fortinet", "fortiproxy"]]}) == "Fortinet"
    # fallback vendor:product crudo
    assert dim.product_family({"cpe_products": [["acme", "widget"]]}) == "acme:widget"
    # sin cpe → None
    assert dim.product_family({"cpe_products": []}) is None


def test_extract_dims_and_ladder_order_and_skip_none():
    data = {
        "base_severity": "CRITICAL", "attack_vector": "NETWORK",
        "cwes": ["CWE-89"], "cpe_products": [["microsoft", "windows"]],
    }
    dims = dim.extract_dims(data)
    ladder = dim.subject_ladder(dims)
    levels = [lvl for (lvl, _key) in ladder]
    assert levels == ["family", "class", "vector", "tier"]  # específico→general
    assert ladder[0] == ("family", "family:Microsoft Windows")
    assert ladder[3] == ("tier", "tier:CRITICAL")

    # con dims faltantes se omiten, preservando el orden
    partial = dim.extract_dims({"base_severity": "HIGH", "attack_vector": "LOCAL"})
    lad2 = dim.subject_ladder(partial)
    assert [lvl for (lvl, _k) in lad2] == ["vector", "tier"]


def test_gravity_events_alignment_with_template_fields():
    dims = dim.extract_dims({
        "base_severity": "LOW", "attack_vector": "PHYSICAL",
        "cwes": ["CWE-79"], "cpe_products": [["google", "chrome"]],
    })
    evts = dict(dim.gravity_events(dims, cve_id="CVE-2021-1"))
    assert evts["cve_tier"]["severity_tier"] == "LOW"
    assert evts["cve_vector"]["attack_vector"] == "PHYSICAL"
    assert evts["cve_class"]["vulnerability_class"] == "xss"
    assert evts["cve_family"]["product_family"] == "Google Chrome"
    # cada evento lleva el cve_id y su único campo de dim (+ cve_id)
    assert set(evts["cve_tier"].keys()) == {"severity_tier", "cve_id"}


def test_has_any_dim_and_coverage():
    rows = [
        dim.extract_dims({"base_severity": "HIGH"}),
        dim.extract_dims({"cpe_products": [["microsoft", "windows"]]}),
        dim.extract_dims({}),  # neutral
    ]
    assert dim.has_any_dim(rows[0]) is True
    assert dim.has_any_dim(rows[2]) is False
    cov = dim.coverage(rows)
    assert cov["n"] == 3
    assert cov["neutral"] == 1


# ── Seen-ledger (idempotencia + checkpoints) ────────────────────────────────────

def _rec(cve_id="CVE-2021-1", **kw):
    base = {
        "cve_id": cve_id, "published_ts": 1600000000.0, "in_kev": 0,
        "product_family": "Microsoft Windows", "vulnerability_class": "xss",
        "attack_vector": "NETWORK", "severity_tier": "CRITICAL",
        "outcome": "loss", "timing": "NONE",
    }
    base.update(kw)
    return base


def test_seen_ledger_upsert_new_then_idempotent_then_changed(temp_vault):
    is_new, is_changed = seen_ledger.upsert(_rec())
    assert (is_new, is_changed) == (True, True)
    # misma info → no cambia
    is_new, is_changed = seen_ledger.upsert(_rec())
    assert (is_new, is_changed) == (False, False)
    # cambia el outcome (p. ej. LOSS→WIN por entrar a KEV) → is_changed
    is_new, is_changed = seen_ledger.upsert(_rec(in_kev=1, outcome="win", timing="LATE"))
    assert is_new is False and is_changed is True


def test_seen_ledger_mark_materialized_once(temp_vault):
    seen_ledger.upsert(_rec())
    assert seen_ledger.mark_materialized("CVE-2021-1", "tier") is True
    assert seen_ledger.mark_materialized("CVE-2021-1", "tier") is False  # ya materializado
    assert seen_ledger.mark_materialized("CVE-2021-1", "family") is True
    assert seen_ledger.is_materialized("CVE-2021-1", "tier") is True
    assert seen_ledger.is_materialized("CVE-2021-1", "vector") is False


def test_seen_ledger_checkpoint_roundtrip(temp_vault):
    assert seen_ledger.get_checkpoint("cursor") is None
    seen_ledger.set_checkpoint("cursor", "2021-01-01|0")
    assert seen_ledger.get_checkpoint("cursor") == "2021-01-01|0"
    seen_ledger.set_checkpoint("cursor", "2021-05-01|2000")
    assert seen_ledger.get_checkpoint("cursor") == "2021-05-01|2000"


def test_seen_ledger_counts(temp_vault):
    seen_ledger.upsert(_rec("CVE-2021-1", outcome="loss", in_kev=0))
    seen_ledger.upsert(_rec("CVE-2021-2", outcome="win", in_kev=1))
    c = seen_ledger.counts()
    assert c["total"] == 2
    assert c["in_kev"] == 1
    assert c["by_outcome"].get("win") == 1


# ── Provider registry ───────────────────────────────────────────────────────────

def test_get_provider_default_simulator(monkeypatch):
    monkeypatch.delenv("CYBER_FEED_PROVIDER", raising=False)
    from connectors.cybersecurity import get_provider
    p = get_provider(force_new=True)
    assert p.provider_name == "simulator"
    assert p.health_check().get("healthy") is True
    evts = list(p.stream_events(20))
    assert len(evts) == 20
    assert all(e.cve_id.startswith("CVE-") for e in evts)


# ── Etapa 2: fuentes NVD/KEV (sin red) ──────────────────────────────────────────

def test_normalize_cve_v31_with_kev_and_dims():
    from connectors.cybersecurity.nvd_provider import normalize_cve
    item = {"cve": {
        "id": "CVE-2021-44228", "published": "2021-12-10T10:15:09.143",
        "metrics": {"cvssMetricV31": [{"cvssData": {
            "baseSeverity": "CRITICAL", "attackVector": "NETWORK",
            "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N"}}]},
        "weaknesses": [{"description": [{"value": "CWE-502"}]}],
        "configurations": [{"nodes": [{"cpeMatch": [
            {"criteria": "cpe:2.3:a:apache:log4j:2.0:*:*:*:*:*:*:*", "vulnerable": True}]}]}],
        "cisaExploitAdd": "2021-12-10",
    }}
    ev = normalize_cve(item)
    d = ev.data
    assert d["cve_id"] == "CVE-2021-44228"
    assert d["base_severity"] == "CRITICAL"
    assert d["attack_vector"] == "NETWORK"
    assert d["cwes"] == ["CWE-502"]
    assert d["cpe_products"] == [["apache", "log4j"]]
    assert d["in_kev"] is True and d["kev_date"] == "2021-12-10"
    dims = dim.extract_dims(d)
    assert dims["vulnerability_class"] == "deserialization"
    assert dims["product_family"] == "Apache Log4j"


def test_normalize_cve_v2_fallback():
    from connectors.cybersecurity.nvd_provider import normalize_cve
    item = {"cve": {
        "id": "CVE-2015-1", "published": "2015-01-01T00:00:00.000",
        "metrics": {"cvssMetricV2": [{"baseSeverity": "HIGH",
                                        "cvssData": {"accessVector": "NETWORK"}}]},
        "weaknesses": [], "configurations": [],
    }}
    ev = normalize_cve(item)
    assert ev.data["base_severity"] == "HIGH"
    assert ev.data["attack_vector"] == "NETWORK"
    assert ev.data["in_kev"] is False


def test_normalize_cve_kev_map_reconciliation():
    from connectors.cybersecurity.nvd_provider import normalize_cve
    item = {"cve": {"id": "CVE-2020-1472", "published": "2020-08-17T00:00:00.000",
                    "metrics": {}, "weaknesses": [], "configurations": []}}
    kev_map = {"CVE-2020-1472": {"date_added": "2020-09-18", "ransomware": True}}
    ev = normalize_cve(item, kev_map=kev_map)
    assert ev.data["in_kev"] is True
    assert ev.data["kev_date"] == "2020-09-18"
    assert ev.data["ransomware"] is True


def test_nvd_iter_window_paginates_and_sets_pub_dates(monkeypatch):
    from connectors.cybersecurity import nvd_provider as nvd
    client = nvd.NvdClient(page_size=2, delay=0)
    pages = {
        0: {"vulnerabilities": [{"cve": {"id": "CVE-2021-1"}}, {"cve": {"id": "CVE-2021-2"}}],
            "totalResults": 3},
        2: {"vulnerabilities": [{"cve": {"id": "CVE-2021-3"}}], "totalResults": 3},
    }
    calls = []

    def fake_request(params):
        calls.append(dict(params))
        return pages[params["startIndex"]]

    monkeypatch.setattr(client, "_request", fake_request)
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    end = datetime(2021, 2, 1, tzinfo=timezone.utc)
    items = list(client.iter_window(start, end))
    assert [i["cve"]["id"] for i in items] == ["CVE-2021-1", "CVE-2021-2", "CVE-2021-3"]
    assert calls[0]["pubStartDate"].startswith("2021-01-01")
    assert "pubEndDate" in calls[0]
    assert len(calls) == 2


def test_nvd_request_backoff_then_success(monkeypatch):
    from connectors.cybersecurity import nvd_provider as nvd

    class FakeResp:
        def __init__(self, sc, payload=None):
            self.status_code = sc
            self._p = payload or {}
        def json(self):
            return self._p
        def raise_for_status(self):
            raise RuntimeError("http %d" % self.status_code)

    seq = [FakeResp(429), FakeResp(200, {"totalResults": 1, "vulnerabilities": []})]

    class FakeRequests:
        def get(self, *a, **k):
            return seq.pop(0)

    monkeypatch.setattr(nvd, "requests", FakeRequests())
    monkeypatch.setattr(nvd.time, "sleep", lambda *a, **k: None)
    client = nvd.NvdClient(delay=0, backoff=0)
    data = client._request({"resultsPerPage": 1, "startIndex": 0})
    assert data["totalResults"] == 1
    assert seq == []  # consumió ambas respuestas (reintentó tras 429)


def test_date_windows_clamps_2020_and_max_120():
    from connectors.cybersecurity.nvd_provider import date_windows, MIN_PUB
    since = datetime(2018, 1, 1, tzinfo=timezone.utc)
    until = datetime(2020, 9, 1, tzinfo=timezone.utc)
    wins = date_windows(since, until)
    assert wins[0][0] == MIN_PUB              # clamp a 2020-01-01
    assert all((b - a).days <= 120 for (a, b) in wins)
    assert wins[-1][1] == until


def test_kev_build_map_and_snapshot_roundtrip(temp_vault):
    from connectors.cybersecurity import kev_client as kev
    sample = {"vulnerabilities": [
        {"cveID": "CVE-2021-44228", "vendorProject": "Apache", "product": "Log4j",
         "dateAdded": "2021-12-10", "knownRansomwareCampaignUse": "Known",
         "vulnerabilityName": "Log4Shell"},
        {"cveID": "cve-2020-1472", "vendorProject": "Microsoft", "product": "Netlogon",
         "dateAdded": "2020-09-18", "knownRansomwareCampaignUse": "Unknown"},
    ]}
    m = kev.build_kev_map(sample)
    assert m["CVE-2021-44228"]["ransomware"] is True
    assert m["CVE-2020-1472"]["date_added"] == "2020-09-18"  # cve id normalizado a upper
    kev._write_snapshot(sample)
    loaded = kev.load_kev()
    assert set(kev.build_kev_map(loaded).keys()) == set(m.keys())
    assert kev.health()["healthy"] is True


# ── Etapa 3: outcome + verificación ─────────────────────────────────────────

from core.learn.outcome_adapter import OutcomeStatus
from core.learn import verification_ledger as vledger
from connectors.cybersecurity.base import CVEEvent
from connectors.cybersecurity import verification_cycle as vc

_NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)


def _cve(cid, published, in_kev=False, kev_date=None, sev="HIGH", av="NETWORK",
         cwe="CWE-79", vendor=("microsoft", "windows")):
    data = {
        "cve_id": cid, "published": published,
        "base_severity": sev, "attack_vector": av,
        "cwes": [cwe] if cwe else [],
        "cpe_products": [list(vendor)] if vendor else [],
        "in_kev": in_kev,
    }
    if in_kev and kev_date:
        data["kev_date"] = kev_date
    return CVEEvent(event_type="cve", data=data)


def test_outcome_win_timing_buckets():
    early = vc.resolve_cve(_cve("CVE-2021-1", "2021-01-01T00:00:00.000", True, "2021-01-11"), _NOW)
    late = vc.resolve_cve(_cve("CVE-2021-2", "2021-01-01T00:00:00.000", True, "2021-04-11"), _NOW)
    none_ = vc.resolve_cve(_cve("CVE-2021-3", "2021-01-01T00:00:00.000", True, "2021-10-28"), _NOW)
    assert early["outcome"].status is OutcomeStatus.WIN
    assert early["outcome"].evidence["timing"] == "EARLY"
    assert late["outcome"].evidence["timing"] == "LATE"
    assert none_["outcome"].evidence["timing"] == "NONE"


def test_outcome_kev_never_loss_even_if_old():
    # CVE de 2021 en KEV, muy vieja respecto a 2026 → sigue WIN (nunca LOSS)
    r = vc.resolve_cve(_cve("CVE-2021-9", "2021-01-01T00:00:00.000", True, "2025-01-01"), _NOW)
    assert r["outcome"].status is OutcomeStatus.WIN
    assert r["outcome"].evidence["timing"] == "NONE"  # days_to_kev > 180


def test_outcome_loss_matured():
    r = vc.resolve_cve(_cve("CVE-2021-10", "2021-01-01T00:00:00.000", False), _NOW)
    assert r["outcome"].status is OutcomeStatus.LOSS


def test_outcome_pending_recent():
    r = vc.resolve_cve(_cve("CVE-2026-1", "2026-07-15T00:00:00.000", False), _NOW)
    assert r["outcome"].status is OutcomeStatus.PENDING


def test_outcome_neutral_no_dims():
    r = vc.resolve_cve(_cve("CVE-2021-11", "2021-01-01T00:00:00.000", False,
                            sev="", av="", cwe=None, vendor=None), _NOW)
    assert r["outcome"].status is OutcomeStatus.NEUTRAL


def test_verify_records_per_level_and_idempotent(temp_vault):
    vledger.clear_domain("cybersecurity")
    evs = [_cve("CVE-2021-100", "2021-01-01T00:00:00.000", True, "2021-01-15")]  # WIN, 4 dims
    vc.verify_events(evs, record=True, now=_NOW)
    outs = vledger.load_outcomes("cybersecurity")
    assert len(outs) == 4
    assert sorted(o.prediction_id.split("|")[1] for o in outs) == ["class", "family", "tier", "vector"]
    # re-ejecutar: 0 nuevas (idempotente)
    vc.verify_events(evs, record=True, now=_NOW)
    assert len(vledger.load_outcomes("cybersecurity")) == 4


def test_verify_flip_loss_to_win_dedupe_at_read(temp_vault):
    vledger.clear_domain("cybersecurity")
    vc.verify_events([_cve("CVE-2021-200", "2021-01-01T00:00:00.000", False)], now=_NOW)
    s1 = vc.verified_score()
    assert s1.losses >= 1 and s1.wins == 0
    # entra a KEV → WIN superseding; el dedupe-at-read se queda con WIN
    vc.verify_events([_cve("CVE-2021-200", "2021-01-01T00:00:00.000", True, "2021-02-01")], now=_NOW)
    s2 = vc.verified_score()
    assert s2.wins >= 1 and s2.losses == 0


def test_verify_exclusion_pre2020(temp_vault):
    vledger.clear_domain("cybersecurity")
    sc = vc.verify_events([_cve("CVE-2019-1", "2019-06-01T00:00:00.000", True, "2019-06-10")], now=_NOW)
    assert sc.n_total == 0
    assert len(vledger.load_outcomes("cybersecurity")) == 0


# ── Etapa 4: backfill orquestado ────────────────────────────────────────

def _nvd_item(cid, sev, av, cwe, vendor, product, pub, kev=None):
    cve = {"id": cid, "published": pub,
           "metrics": ({"cvssMetricV31": [{"cvssData": {"baseSeverity": sev, "attackVector": av}}]} if sev else {}),
           "weaknesses": ([{"description": [{"value": cwe}]}] if cwe else []),
           "configurations": ([{"nodes": [{"cpeMatch": [
               {"criteria": f"cpe:2.3:a:{vendor}:{product}:1.0:*:*:*:*:*:*:*", "vulnerable": True}]}]}] if vendor else [])}
    if kev:
        cve["cisaExploitAdd"] = kev
    return {"cve": cve}


def _nvd_items():
    return [
        _nvd_item("CVE-2021-0001", "CRITICAL", "NETWORK", "CWE-89", "microsoft", "windows", "2021-01-05T00:00:00.000", kev="2021-01-15"),
        _nvd_item("CVE-2021-0002", "HIGH", "LOCAL", "CWE-79", "adobe", "acrobat_reader", "2021-01-06T00:00:00.000"),
        _nvd_item("CVE-2021-0003", "MEDIUM", "NETWORK", "CWE-22", "apache", "http_server", "2021-01-07T00:00:00.000"),
        _nvd_item("CVE-2021-0004", "", "", "", None, None, "2021-01-08T00:00:00.000"),  # neutral (sin dims)
    ]


class _FakeClient:
    def __init__(self, items):
        self._items = items

    def iter_window(self, start, end, use_lastmod=False):
        return iter(self._items)


def _cyber_stars(gi):
    return {fp: r for fp, r in gi.load_raw().items() if fp.startswith("cybersecurity:")}


def test_backfill_writes_bounded_mass_and_idempotent(tmp_path, temp_vault):
    from connectors.cybersecurity import backfill as bf
    from core.learn.gravity_engine import GravityIndex
    vledger.clear_domain("cybersecurity")
    gi = GravityIndex(path=str(tmp_path / "grav.json"))
    items = _nvd_items()
    kev_map = {"CVE-2021-0001": {"date_added": "2021-01-15", "ransomware": False}}
    since = datetime(2021, 1, 1, tzinfo=timezone.utc)
    until = datetime(2021, 2, 1, tzinfo=timezone.utc)
    s1 = bf.run_backfill(since=since, until=until, now=_NOW, client=_FakeClient(items),
                         kev_map=kev_map, gravity_index=gi, fetch_kev_if_missing=False)
    lines1 = len(vledger.load_outcomes("cybersecurity"))
    stars = _cyber_stars(gi)
    hits1 = sum(r.hits for r in stars.values())
    assert lines1 == 12                     # 3 decisivos x 4 niveles
    assert 0 < len(stars) <= 12             # cardinalidad acotada (anti-fragmentación)
    assert s1["seen_ledger"]["total"] == 4
    assert s1["verified"]["wins"] == 4 and s1["verified"]["losses"] == 8
    # idempotente: reset del checkpoint y re-correr → 0 nuevas y masa intacta
    seen_ledger.set_checkpoint("backfill_window_idx", "0")
    bf.run_backfill(since=since, until=until, now=_NOW, client=_FakeClient(items),
                    kev_map=kev_map, gravity_index=gi, fetch_kev_if_missing=False)
    assert len(vledger.load_outcomes("cybersecurity")) == lines1
    hits2 = sum(r.hits for r in _cyber_stars(gi).values())
    assert hits2 == hits1


def test_backfill_dry_run_no_writes(tmp_path, temp_vault):
    from connectors.cybersecurity import backfill as bf
    from core.learn.gravity_engine import GravityIndex
    vledger.clear_domain("cybersecurity")
    gi = GravityIndex(path=str(tmp_path / "grav.json"))
    items = _nvd_items()
    kev_map = {"CVE-2021-0001": {"date_added": "2021-01-15"}}
    s = bf.run_backfill(dry_run=True, since=datetime(2021, 1, 1, tzinfo=timezone.utc),
                        until=datetime(2021, 2, 1, tzinfo=timezone.utc), now=_NOW,
                        client=_FakeClient(items), kev_map=kev_map,
                        gravity_index=gi, fetch_kev_if_missing=False)
    assert s["dry_run"] is True
    assert s["cve_seen"] == 4
    assert s["outcomes"].get("win") == 1
    assert "coverage" in s
    # NADA escrito
    assert len(vledger.load_outcomes("cybersecurity")) == 0
    assert seen_ledger.counts()["total"] == 0
    assert len(_cyber_stars(gi)) == 0


# ── Etapa 5: criterio + fallback jerárquico ────────────────────────────────

def _seed_ledger(subject, wins, losses):
    from core.learn.outcome_adapter import Outcome, OutcomeStatus
    i = 0
    for _ in range(wins):
        vledger.record_outcome(Outcome(prediction_id=f"CVE-2021-{subject}-{i}|x", domain="cybersecurity",
                                       subject=subject, status=OutcomeStatus.WIN, score=1.0,
                                       evidence={"timing": "EARLY"}))
        i += 1
    for _ in range(losses):
        vledger.record_outcome(Outcome(prediction_id=f"CVE-2021-{subject}-{i}|x", domain="cybersecurity",
                                       subject=subject, status=OutcomeStatus.LOSS, score=-1.0,
                                       evidence={"timing": "NONE"}))
        i += 1


def test_detect_domain_cyber(tmp_path, temp_vault, monkeypatch):
    from core.learn import gravity_engine as ge
    from core.learn.gravity_engine import GravityIndex
    from core.learn import criterion
    gi = GravityIndex(path=str(tmp_path / "grav.json"))
    gi.record_event(fingerprint="cybersecurity:cve_tier:severity_tier=CRITICAL",
                    cc_score=0.5, domain="cybersecurity", intent="CRITICAL")
    monkeypatch.setattr(ge, "_index", gi)
    assert criterion.detect_domain("¿qué opinas de las vulnerabilidades críticas?") == "cybersecurity"
    assert criterion.detect_domain("opinión sobre exploits y cve") == "cybersecurity"


def test_criterion_grounded_cyber(temp_vault):
    from core.learn import criterion
    vledger.clear_domain("cybersecurity")
    _seed_ledger("tier:CRITICAL", wins=14, losses=26)
    # sin LLM (llm devuelve vacío) → conclusión determinista grounded
    res = criterion.build_criterion_result("cybersecurity", "¿qué opinas de las vulnerabilidades?", llm=lambda p: "")
    assert res.origin in ("deterministic", "llm_rendered")
    assert "cybersecurity" in res.text
    assert "CRITICAL" in res.text  # cita el subject aprendido


def test_resolve_best_subject_prefers_specific_with_wins(temp_vault):
    vledger.clear_domain("cybersecurity")
    _seed_ledger("tier:CRITICAL", wins=14, losses=26)   # 40 decisivos, con wins
    _seed_ledger("family:Foo", wins=0, losses=5)        # poca masa
    dims = {"product_family": "Foo", "vulnerability_class": None,
            "attack_vector": None, "severity_tier": "CRITICAL"}
    best = vc.resolve_best_subject(dims, min_decisive=30)
    assert best is not None
    assert best[0] == "tier:CRITICAL"  # family sin masa suficiente → cae a tier


# ── Etapa 6: ciclo incremental + freno del worker ───────────────────────────

def test_learning_cycle_disabled_by_default(temp_vault, monkeypatch):
    monkeypatch.delenv("CYBER_LEARN_ENABLED", raising=False)
    from connectors.cybersecurity import learning_cycle as lc
    r = lc.run_learning_cycle()
    assert r["success"] is False
    assert "CYBER_LEARN_ENABLED" in r["detail"]


def test_learning_cycle_enabled_with_simulator(tmp_path, temp_vault, monkeypatch):
    from core.learn import gravity_engine as ge
    from core.learn.gravity_engine import GravityIndex
    from connectors.cybersecurity import learning_cycle as lc
    from connectors.cybersecurity.simulator_adapter import SimulatorAdapter
    monkeypatch.setenv("CYBER_LEARN_ENABLED", "1")
    gi = GravityIndex(path=str(tmp_path / "grav.json"))
    monkeypatch.setattr(ge, "_index", gi)
    vledger.clear_domain("cybersecurity")
    prov = SimulatorAdapter(seed=7, kev_ratio=0.3, neutral_ratio=0.05)
    r = lc.run_learning_cycle(n_events=60, provider=prov, now=_NOW)
    assert r["success"] is True
    assert r["provider"] == "simulator"
    assert r["events"] == 60
    assert len([fp for fp in gi.load_raw() if fp.startswith("cybersecurity:")]) > 0
    assert len(vledger.load_outcomes("cybersecurity")) > 0
