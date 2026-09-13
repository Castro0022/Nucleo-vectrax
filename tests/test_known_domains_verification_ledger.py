"""
tests/test_known_domains_verification_ledger.py — Fix 2026-09-13.

Contrato: `core.learn.criterion.known_domains()` debe incluir dominios que
solo tienen evidencia en `core.learn.verification_ledger`
(`<vault>/domain_verification/{domain}.jsonl`), no solo los que aparecen
en `gravity_engine.domain_stats()` o `domain_knowledge.list_domains()`.

Sin esto, `detect_domain()` nunca podía resolver dominios como
"cybersecurity" para el Domain Criterion Gate, aunque
`build_criterion_result()` ya sabía recuperar su evidencia real cuando se
le pasaba el dominio de forma explícita — el Gate simplemente nunca
llegaba a intentarlo.

Aislado con un vault temporal (`VECTRAX_VAULT_DIR`) para no depender ni
contaminar los datos reales de producción.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTRAX_VAULT_DIR", str(tmp_path))
    # Limpia el cache de lectura de verification_ledger entre tests —
    # está keyed por path absoluto, así que un tmp_path nuevo por test ya
    # es suficiente, pero se limpia explícitamente por claridad.
    from core.learn import verification_ledger as vled
    vled._load_cache.clear()
    yield tmp_path


def _write_outcome(vault_dir, domain: str, subject: str = "test_subject"):
    d = vault_dir / "domain_verification"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{domain}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "prediction_id": f"{domain}-1",
            "domain": domain,
            "subject": subject,
            "status": "win",
            "score": 1.0,
            "resolved_ts": 0.0,
            "evidence": {},
        }) + "\n")


class TestVerificationLedgerListDomains:
    def test_empty_dir_returns_empty_list(self, isolated_vault):
        from core.learn import verification_ledger as vled
        assert vled.list_domains() == []

    def test_lists_domains_with_jsonl_files(self, isolated_vault):
        from core.learn import verification_ledger as vled
        _write_outcome(isolated_vault, "cybersecurity")
        _write_outcome(isolated_vault, "freight_logistics")
        assert vled.list_domains() == ["cybersecurity", "freight_logistics"]

    def test_excludes_backup_files(self, isolated_vault):
        from core.learn import verification_ledger as vled
        _write_outcome(isolated_vault, "cybersecurity")
        d = isolated_vault / "domain_verification"
        (d / "cybersecurity.jsonl.bak.20260101000000").write_text("{}\n")
        assert vled.list_domains() == ["cybersecurity"]

    def test_excludes_non_jsonl_files(self, isolated_vault):
        from core.learn import verification_ledger as vled
        _write_outcome(isolated_vault, "market")
        d = isolated_vault / "domain_verification"
        (d / "market_verified.json").write_text("{}")
        assert vled.list_domains() == ["market"]


class TestKnownDomainsIncludesVerificationLedger:
    def test_domain_only_in_verification_ledger_is_known(self, isolated_vault):
        """El caso exacto de la regresión: un dominio SOLO presente en
        verification_ledger (sin registros en gravity, sin archivo en
        domain_knowledge) debe seguir siendo un dominio conocido."""
        from core.learn.criterion import known_domains
        _write_outcome(isolated_vault, "cybersecurity")
        assert "cybersecurity" in known_domains()

    def test_detect_domain_resolves_cybersecurity_via_ledger_only(self, isolated_vault):
        from core.learn.criterion import detect_domain
        _write_outcome(isolated_vault, "cybersecurity")
        assert detect_domain("¿Qué has observado en el dominio de ciberseguridad?") == "cybersecurity"

    def test_excluded_domains_never_leak_in_via_ledger(self, isolated_vault):
        from core.learn.criterion import known_domains
        _write_outcome(isolated_vault, "unknown")
        _write_outcome(isolated_vault, "tests")
        doms = known_domains()
        assert "unknown" not in doms
        assert "tests" not in doms

    def test_verification_ledger_failure_is_non_fatal(self, monkeypatch):
        """Si verification_ledger falla, known_domains() sigue funcionando
        con las otras dos fuentes (fail-safe, no rompe el contrato previo)."""
        import core.learn.criterion as crit_mod
        monkeypatch.setattr(
            "core.learn.verification_ledger.list_domains",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # No debe lanzar.
        result = crit_mod.known_domains()
        assert isinstance(result, list)
