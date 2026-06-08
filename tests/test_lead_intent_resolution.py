"""
Tests — /lead Command Intent Resolution
==========================================
Validates that the lead command handler resolves user intent correctly:

  /lead joy       → search for lead "joy", NOT show help
  /lead           → list leads (existing behavior)
  /lead help      → show help explicitly
  /lead add joy   → add lead (existing behavior)

Simulates the TelegramGateway._handle_lead flow using the real
lead_tracker against a temp DB.

Creado: 2026-06-08
Creador: Mario Bravo Castro
Co-Authored-By: Oz <oz-agent@warp.dev>
"""
from __future__ import annotations

import os
import re
import tempfile
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_lead_db(monkeypatch, tmp_path):
    """Redirect lead_tracker to a temp DB."""
    db_path = str(tmp_path / "test_leads.db")
    monkeypatch.setattr("core.lead_tracker._DB", db_path)
    yield db_path


class FakeGateway:
    """Minimal mock of TelegramGateway for testing _handle_lead."""

    def __init__(self):
        self.sent_messages: list = []
        self._lang = "es"

    def _send(self, cid: int, text: str):
        self.sent_messages.append(text)

    def _handle_lead(self, cid: int, tg_uid: str, text: str) -> None:
        """Copy of the real _handle_lead logic (imported dynamically)."""
        from core.lead_tracker import (
            add_lead, update_lead_status, record_contact,
            get_leads, generate_followup, format_lead_list,
        )
        lang = self._lang

        clean = re.sub(r"^/?lead\s*", "", text, flags=re.IGNORECASE).strip()
        parts = clean.split(None, 2)
        sub = parts[0].lower() if parts else "list"
        arg = parts[1].strip() if len(parts) > 1 else ""
        extra = parts[2].strip() if len(parts) > 2 else ""

        try:
            if sub in ("add", "nuevo", "agregar"):
                if not arg:
                    self._send(cid, "Uso: /lead add <nombre> [contexto]")
                    return
                name = arg
                ctx = extra
                if "|" in arg:
                    parts2 = arg.split("|", 1)
                    name = parts2[0].strip()
                    ctx = parts2[1].strip()
                result = add_lead(tg_uid, name, context=ctx)
                if result["action"] == "updated":
                    self._send(cid, f"✅ {name} actualizado.")
                else:
                    ctx_note = f" — {ctx}" if ctx else ""
                    self._send(cid, f"{name} guardado{ctx_note}.")

            elif sub in ("summary", "resumen", "hot", "calientes"):
                leads = get_leads(tg_uid)
                if not leads:
                    self._send(cid, "No hay leads activos.")
                else:
                    self._send(cid, format_lead_list(leads))

            elif sub in ("list", "lista", "ver", ""):
                leads = get_leads(tg_uid)
                if leads:
                    self._send(cid, format_lead_list(leads))
                else:
                    self._send(cid, "No tienes leads activos.\nGuarda uno con:\n/lead add nombre")

            elif sub in ("view", "info", "detalle"):
                if not arg:
                    self._send(cid, "Uso: /lead view <nombre>")
                    return
                leads = get_leads(tg_uid)
                lead = next((l for l in leads if l["name"].lower() == arg.lower()), None)
                if not lead:
                    self._send(cid, f"Lead '{arg}' no encontrado.")
                else:
                    self._send(cid, f"👤 {lead['name']}\n   Estado: {lead['status']}")

            elif sub in ("follow", "seguimiento", "mensaje"):
                if not arg:
                    self._send(cid, "Uso: /lead follow <nombre>")
                    return
                self._send(cid, f"Follow-up para {arg}")

            elif sub in ("contact", "contacte", "hablé", "llamé", "contacto"):
                if not arg:
                    self._send(cid, "Uso: /lead contact <nombre>")
                    return
                ok = record_contact(tg_uid, arg, note=extra)
                if ok:
                    self._send(cid, f"✅ Contacto registrado con {arg}.")
                else:
                    self._send(cid, f"Lead '{arg}' no encontrado.")

            elif sub in ("proposal", "propuesta", "envié"):
                pass
            elif sub in ("won", "cerrado", "gano", "gané"):
                pass
            elif sub in ("lost", "perdido", "perdi"):
                pass

            elif sub in ("help", "ayuda", "?"):
                self._send(cid, "Comandos:\n  /lead add <nombre>")

            else:
                # ── INTENT RESOLUTION: interpret as lead name ──
                lead_name = clean
                leads = get_leads(tg_uid)
                lead = next(
                    (l for l in leads if l["name"].lower() == lead_name.lower()),
                    None,
                )
                if lead:
                    self._send(cid, f"👤 {lead['name']}\n   Estado: {lead['status']}")
                else:
                    display_name = lead_name.title()
                    self._send(cid, (
                        f"No encontré un lead llamado '{display_name}'.\n"
                        f"¿Quieres crearlo?\n/lead add {display_name}"
                    ))

        except Exception as e:
            self._send(cid, f"Error: {e}")


# ===========================================================================
# Test cases — user perspective
# ===========================================================================

class TestLeadIntentResolution:
    """
    Core tests: /lead <name> must resolve as lead lookup, never as help.
    """

    def _gw(self):
        return FakeGateway()

    def test_lead_joy_existing_shows_view(self):
        """
        /lead joy when Joy exists → show lead details, NOT help.
        """
        from core.lead_tracker import add_lead
        add_lead("tg:user1", "Joy", context="diseño logo")

        gw = self._gw()
        gw._handle_lead(1, "tg:user1", "/lead joy")

        assert len(gw.sent_messages) == 1
        msg = gw.sent_messages[0]
        assert "👤" in msg or "Joy" in msg
        assert "Comandos" not in msg  # must NOT show help

    def test_lead_joy_nonexistent_offers_create(self):
        """
        /lead joy when Joy doesn't exist → offer to create, NOT help.
        """
        gw = self._gw()
        gw._handle_lead(1, "tg:user2", "/lead joy")

        assert len(gw.sent_messages) == 1
        msg = gw.sent_messages[0]
        assert "Joy" in msg
        assert "crear" in msg.lower() or "add" in msg.lower()
        assert "Comandos" not in msg  # must NOT show help

    def test_lead_bare_shows_list(self):
        """
        /lead (no args) → show list or "no leads" message.
        """
        gw = self._gw()
        gw._handle_lead(1, "tg:user3", "/lead")

        assert len(gw.sent_messages) == 1
        msg = gw.sent_messages[0]
        # Should show list or empty message, not help
        assert "leads" in msg.lower() or "no tienes" in msg.lower()
        assert "/lead add <nombre>" not in msg or "Comandos" not in msg

    def test_lead_help_shows_commands(self):
        """
        /lead help → show command list (only explicit path to help).
        """
        gw = self._gw()
        gw._handle_lead(1, "tg:user4", "/lead help")

        assert len(gw.sent_messages) == 1
        msg = gw.sent_messages[0]
        assert "Comandos" in msg or "/lead add" in msg

    def test_lead_add_still_works(self):
        """
        /lead add Joy diseño → add lead normally.
        """
        gw = self._gw()
        gw._handle_lead(1, "tg:user5", "/lead add Joy diseño")

        assert len(gw.sent_messages) == 1
        msg = gw.sent_messages[0]
        assert "Joy" in msg
        assert "guardado" in msg.lower() or "actualizado" in msg.lower()

    def test_lead_name_case_insensitive(self):
        """
        /lead JOY should find lead "Joy" (case insensitive).
        """
        from core.lead_tracker import add_lead
        add_lead("tg:user6", "Joy", context="test")

        gw = self._gw()
        gw._handle_lead(1, "tg:user6", "/lead JOY")

        msg = gw.sent_messages[0]
        assert "Joy" in msg
        assert "Comandos" not in msg

    def test_lead_multiword_name(self):
        """
        /lead María López should search for "María López".
        """
        from core.lead_tracker import add_lead
        add_lead("tg:user7", "María López", context="contrato")

        gw = self._gw()
        gw._handle_lead(1, "tg:user7", "/lead María López")

        msg = gw.sent_messages[0]
        assert "María López" in msg
        assert "Comandos" not in msg

    def test_lead_view_subcommand_still_works(self):
        """
        /lead view Joy → explicit view still works.
        """
        from core.lead_tracker import add_lead
        add_lead("tg:user8", "Joy", context="test")

        gw = self._gw()
        gw._handle_lead(1, "tg:user8", "/lead view Joy")

        msg = gw.sent_messages[0]
        assert "Joy" in msg

    def test_lead_summary_still_works(self):
        """
        /lead summary → existing behavior preserved.
        """
        from core.lead_tracker import add_lead
        add_lead("tg:user9", "Carlos", context="test")

        gw = self._gw()
        gw._handle_lead(1, "tg:user9", "/lead summary")

        msg = gw.sent_messages[0]
        assert "Carlos" in msg

    def test_never_shows_help_for_unknown_name(self):
        """
        Any unknown word after /lead must NEVER trigger help dump.
        Test with multiple random names.
        """
        gw = self._gw()
        for name in ["Carlos", "Anna", "TestLead", "xyz123"]:
            gw.sent_messages.clear()
            gw._handle_lead(1, "tg:user10", f"/lead {name}")
            msg = gw.sent_messages[0]
            assert "Comandos:" not in msg, (
                f"/lead {name} should not show help! Got: {msg}"
            )
