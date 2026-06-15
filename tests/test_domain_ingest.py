"""
Tests for Multi-Domain Connector: tenant, ingester, templates
================================================================
Creador: Mario Bravo Castro
"""
import json
import os
import sqlite3
import time
import pytest

# Override tenant DB path
_TEST_DIR = "/tmp/vectrax_test_domain"
os.makedirs(_TEST_DIR, exist_ok=True)
_TEST_TENANT_DB = os.path.join(_TEST_DIR, "tenants.db")

import core.tenant as tenant_mod
tenant_mod._DB_PATH = _TEST_TENANT_DB


def _reset_tenants():
    if os.path.exists(_TEST_TENANT_DB):
        os.remove(_TEST_TENANT_DB)


# ═══════════════════════════════════════════════════════════════════
# TENANT TESTS
# ═══════════════════════════════════════════════════════════════════

class TestTenantCreation:
    def setup_method(self):
        _reset_tenants()

    def test_create_tenant(self):
        t = tenant_mod.create_tenant("Test Store", plan="free", domain="test_store")
        assert t["tenant_id"].startswith("t_")
        assert t["api_key"].startswith("vx_")
        assert t["name"] == "Test Store"
        assert t["domain"] == "test_store"
        assert t["daily_limit"] == 100

    def test_create_tenant_auto_domain(self):
        t = tenant_mod.create_tenant("My Phone Shop")
        assert t["domain"] == "my_phone_shop"

    def test_create_tenant_pro(self):
        t = tenant_mod.create_tenant("Pro Biz", plan="pro")
        assert t["daily_limit"] == 10000

    def test_unique_keys(self):
        t1 = tenant_mod.create_tenant("Store A")
        t2 = tenant_mod.create_tenant("Store B")
        assert t1["api_key"] != t2["api_key"]
        assert t1["tenant_id"] != t2["tenant_id"]


class TestTenantValidation:
    def setup_method(self):
        _reset_tenants()

    def test_valid_key(self):
        t = tenant_mod.create_tenant("Valid Store", domain="valid")
        tenant = tenant_mod.validate_key(t["api_key"])
        assert tenant is not None
        assert tenant.name == "Valid Store"
        assert tenant.domain == "valid"

    def test_invalid_key(self):
        assert tenant_mod.validate_key("invalid_key_123") is None

    def test_empty_key(self):
        assert tenant_mod.validate_key("") is None
        assert tenant_mod.validate_key(None) is None


class TestTenantRateLimits:
    def setup_method(self):
        _reset_tenants()

    def test_can_ingest_fresh(self):
        t = tenant_mod.create_tenant("Rate Test", plan="free")
        tenant = tenant_mod.validate_key(t["api_key"])
        assert tenant.can_ingest is True

    def test_rate_limit_enforced(self):
        t = tenant_mod.create_tenant("Limit Test", plan="free")
        # Simulate 100 events today
        conn = tenant_mod._conn()
        today = time.strftime("%Y-%m-%d")
        conn.execute(
            "UPDATE tenants SET events_today=100, events_date=? WHERE tenant_id=?",
            (today, t["tenant_id"]),
        )
        conn.commit()
        conn.close()
        tenant = tenant_mod.validate_key(t["api_key"])
        assert tenant.can_ingest is False

    def test_counter_resets_new_day(self):
        t = tenant_mod.create_tenant("Reset Test", plan="free")
        conn = tenant_mod._conn()
        conn.execute(
            "UPDATE tenants SET events_today=100, events_date='2026-01-01' WHERE tenant_id=?",
            (t["tenant_id"],),
        )
        conn.commit()
        conn.close()
        tenant = tenant_mod.validate_key(t["api_key"])
        assert tenant.can_ingest is True  # old date = new day

    def test_record_event(self):
        t = tenant_mod.create_tenant("Counter Test")
        tenant_mod.record_event(t["tenant_id"])
        tenant_mod.record_event(t["tenant_id"])
        tenant = tenant_mod.get_tenant(t["tenant_id"])
        assert tenant.events_today == 2


class TestTenantList:
    def setup_method(self):
        _reset_tenants()

    def test_list_empty(self):
        assert tenant_mod.list_tenants() == []

    def test_list_multiple(self):
        tenant_mod.create_tenant("A")
        tenant_mod.create_tenant("B")
        tenant_mod.create_tenant("C")
        tenants = tenant_mod.list_tenants()
        assert len(tenants) == 3


# ═══════════════════════════════════════════════════════════════════
# DOMAIN INGESTER TESTS
# ═══════════════════════════════════════════════════════════════════

class TestEventToText:
    def test_generic_format(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("custom", {"a": 1, "b": "hello"})
        assert "custom" in text
        assert "a=1" in text
        assert "b=hello" in text

    def test_nested_dict(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("event", {"meta": {"key": "val"}})
        assert "meta.key=val" in text

    def test_list_field(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("event", {"items": [1, 2, 3]})
        assert "3 items" in text


# ═══════════════════════════════════════════════════════════════════
# DOMAIN TEMPLATE TESTS
# ═══════════════════════════════════════════════════════════════════

class TestDomainTemplates:
    def test_retail_sale(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("sale", {"product": "Galaxy S24", "quantity": 1, "amount": 899}, domain="retail")
        assert "Venta:" in text
        assert "Galaxy S24" in text
        assert "899" in text

    def test_retail_return(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("return", {"product": "AirPods", "quantity": 1, "amount": 199, "reason": "defectuoso"}, domain="retail")
        assert "Devolución:" in text
        assert "defectuoso" in text

    def test_restaurant_order(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("order", {"table": 3, "items": "Burger x2", "total": 28.5, "server": "Luis", "guests": 2}, domain="restaurant")
        assert "Pedido mesa 3" in text
        assert "28.5" in text

    def test_finance_transaction(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("transaction", {"type": "expense", "amount": 500, "category": "rent", "counterparty": "Landlord"}, domain="finance")
        assert "expense" in text
        assert "500" in text
        assert "Landlord" in text

    def test_logistics_delivery(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("delivery", {"tracking_id": "X-100", "recipient": "Ana", "on_time": True, "condition": "good"}, domain="logistics")
        assert "Entrega X-100" in text
        assert "Ana" in text

    def test_healthcare_no_show(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("no_show", {"patient_id": "P-1", "provider": "Dr. Smith", "date": "2026-06-15", "type": "follow_up", "consecutive_no_shows": 2}, domain="healthcare")
        assert "No-show" in text
        assert "Dr. Smith" in text

    def test_unknown_domain_fallback(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("custom", {"x": 1}, domain="nonexistent_domain")
        assert "custom" in text
        assert "x=1" in text

    def test_unknown_event_type_fallback(self):
        from core.domain_ingester import event_to_text
        text = event_to_text("unknown_event", {"data": "test"}, domain="retail")
        assert "unknown_event" in text
        assert "data=test" in text


# ═══════════════════════════════════════════════════════════════════
# TEMPLATE FILE VALIDATION
# ═══════════════════════════════════════════════════════════════════

class TestTemplateFiles:
    TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "domain_templates")

    @pytest.mark.parametrize("domain", ["retail", "restaurant", "finance", "logistics", "healthcare"])
    def test_template_exists(self, domain):
        path = os.path.join(self.TEMPLATES_DIR, f"{domain}.json")
        assert os.path.exists(path), f"Template {domain}.json missing"

    @pytest.mark.parametrize("domain", ["retail", "restaurant", "finance", "logistics", "healthcare"])
    def test_template_valid_json(self, domain):
        path = os.path.join(self.TEMPLATES_DIR, f"{domain}.json")
        data = json.load(open(path))
        assert "domain" in data
        assert "event_types" in data
        assert "convergence_rules" in data
        assert "proposal_types" in data
        assert len(data["event_types"]) >= 3

    @pytest.mark.parametrize("domain", ["retail", "restaurant", "finance", "logistics", "healthcare"])
    def test_template_has_text_templates(self, domain):
        path = os.path.join(self.TEMPLATES_DIR, f"{domain}.json")
        data = json.load(open(path))
        for evt_name, evt_config in data["event_types"].items():
            assert "text_template" in evt_config, f"{domain}/{evt_name} missing text_template"
            assert len(evt_config["text_template"]) > 5, f"{domain}/{evt_name} text_template too short"

    @pytest.mark.parametrize("domain", ["retail", "restaurant", "finance", "logistics", "healthcare"])
    def test_template_has_weights(self, domain):
        path = os.path.join(self.TEMPLATES_DIR, f"{domain}.json")
        data = json.load(open(path))
        for evt_name, evt_config in data["event_types"].items():
            assert "weights" in evt_config, f"{domain}/{evt_name} missing weights"
            assert len(evt_config["weights"]) >= 1
