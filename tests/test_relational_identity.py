"""
Tests for vectrax.relational_identity
=======================================
Validates relationship classification and directive generation.

Creador: Mario Bravo Castro
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from vectrax.relational_identity import (
    classify,
    build_relational_directive,
    Relationship,
    RelationalIdentity,
    _is_creator,
)


# ═══════════════════════════════════════════════════════════════════
# CREATOR DETECTION
# ═══════════════════════════════════════════════════════════════════

class TestCreatorDetection:
    def test_creator_uid_direct(self):
        rel = classify("2030762343")
        assert rel.relationship == Relationship.CREATOR
        assert rel.is_creator is True
        assert rel.user_name == "Mario"
        assert rel.has_memory is True

    def test_creator_uid_with_prefix(self):
        rel = classify("tg:2030762343")
        assert rel.relationship == Relationship.CREATOR

    def test_non_creator(self):
        rel = classify("999999999")
        assert rel.relationship != Relationship.CREATOR
        assert rel.is_creator is False

    def test_empty_user_id(self):
        rel = classify("")
        assert rel.relationship == Relationship.NEW_USER

    def test_creator_with_anchor(self):
        anchor = MagicMock()
        anchor.name = "Mario Bravo Castro"
        anchor.has_name = True
        rel = classify("2030762343", anchor=anchor)
        assert rel.relationship == Relationship.CREATOR
        assert rel.user_name == "Mario Bravo Castro"


# ═══════════════════════════════════════════════════════════════════
# KNOWN USER vs NEW USER
# ═══════════════════════════════════════════════════════════════════

class TestUserClassification:
    def test_new_user_no_anchor(self):
        rel = classify("12345")
        assert rel.relationship == Relationship.NEW_USER
        assert rel.is_familiar is False

    def test_known_user_with_anchor_and_interactions(self):
        anchor = MagicMock()
        anchor.name = "Carlos"
        anchor.has_name = True

        # Mock interaction count
        rel = classify("12345", anchor=anchor)
        # Without DB, has_memory=True but interaction_count=0
        # So it stays NEW_USER unless DB has interactions
        assert rel.user_name == "Carlos"
        assert rel.has_memory is True

    def test_is_familiar_property(self):
        rel = RelationalIdentity(relationship=Relationship.KNOWN_USER)
        assert rel.is_familiar is True

        rel2 = RelationalIdentity(relationship=Relationship.NEW_USER)
        assert rel2.is_familiar is False

        rel3 = RelationalIdentity(relationship=Relationship.TEAM_MEMBER)
        assert rel3.is_familiar is True


# ═══════════════════════════════════════════════════════════════════
# SELF-REFERENCE FLAG
# ═══════════════════════════════════════════════════════════════════

class TestSelfReference:
    def test_self_reference_with_creator(self):
        sr = MagicMock()
        sr.self_reference = True
        rel = classify("2030762343", self_ref_result=sr)
        assert rel.is_self_reference is True
        assert rel.relationship == Relationship.CREATOR  # still creator

    def test_self_reference_with_regular_user(self):
        sr = MagicMock()
        sr.self_reference = True
        rel = classify("12345", self_ref_result=sr)
        assert rel.is_self_reference is True

    def test_no_self_reference(self):
        rel = classify("12345")
        assert rel.is_self_reference is False


# ═══════════════════════════════════════════════════════════════════
# DIRECTIVE GENERATION
# ═══════════════════════════════════════════════════════════════════

class TestDirectives:
    def test_creator_directive_es(self):
        rel = RelationalIdentity(
            relationship=Relationship.CREATOR,
            user_name="Mario",
        )
        d = build_relational_directive(rel, lang="es")
        assert "CREADOR" in d
        assert "Mario Bravo Castro" in d
        assert "servilismo" in d
        assert "Nombre: Mario" in d

    def test_creator_directive_en(self):
        rel = RelationalIdentity(
            relationship=Relationship.CREATOR,
            user_name="Mario",
        )
        d = build_relational_directive(rel, lang="en")
        assert "CREATOR" in d
        assert "servility" in d

    def test_known_user_directive(self):
        rel = RelationalIdentity(
            relationship=Relationship.KNOWN_USER,
            user_name="Ana",
        )
        d = build_relational_directive(rel, lang="es")
        assert "CONOCIDO" in d
        assert "Nombre: Ana" in d
        assert "recuerdo que" in d.lower()

    def test_new_user_directive(self):
        rel = RelationalIdentity(
            relationship=Relationship.NEW_USER,
        )
        d = build_relational_directive(rel, lang="es")
        assert "NUEVO" in d
        assert "No asumas" in d
        # Should NOT include a name line
        assert "Nombre:" not in d

    def test_team_member_directive(self):
        rel = RelationalIdentity(
            relationship=Relationship.TEAM_MEMBER,
            user_name="Pedro",
            team_name="Ventas",
        )
        d = build_relational_directive(rel, lang="es")
        assert "EQUIPO" in d
        assert "Nombre: Pedro" in d
        assert "Equipo: Ventas" in d

    def test_self_reference_addendum_es(self):
        rel = RelationalIdentity(
            relationship=Relationship.CREATOR,
            user_name="Mario",
            is_self_reference=True,
        )
        d = build_relational_directive(rel, lang="es")
        assert "primera persona" in d.lower()
        assert "CREADOR" in d

    def test_self_reference_addendum_en(self):
        rel = RelationalIdentity(
            relationship=Relationship.KNOWN_USER,
            user_name="John",
            is_self_reference=True,
        )
        d = build_relational_directive(rel, lang="en")
        assert "first person" in d.lower()

    def test_no_directive_for_empty(self):
        """Build directive still returns content for all types."""
        for r in Relationship:
            if r == Relationship.SELF:
                continue  # SELF has no directive, handled by addendum
            rel = RelationalIdentity(relationship=r)
            d = build_relational_directive(rel, lang="es")
            assert len(d) > 0, f"Empty directive for {r.value}"

    def test_unknown_lang_defaults_to_es(self):
        rel = RelationalIdentity(relationship=Relationship.CREATOR, user_name="Mario")
        d = build_relational_directive(rel, lang="fr")
        assert "CREADOR" in d  # Falls back to Spanish


# ═══════════════════════════════════════════════════════════════════
# IDENTITY CONTEXT INTEGRATION
# ═══════════════════════════════════════════════════════════════════

class TestIdentityContextIntegration:
    def test_prompt_block_with_relational_directive(self):
        from core.identity_context import IdentityContext
        ctx = IdentityContext(
            user_id="2030762343",
            user_name="Mario",
            is_creator=True,
            relationship="creator",
            relational_directive="[RELACIÓN: CREADOR]\nDirectiva test",
            tone="técnico",
            mode="operacional",
        )
        block = ctx.to_prompt_block()
        assert "CREADOR" in block
        assert "Directiva test" in block
        assert "Modo: operacional" in block

    def test_prompt_block_fallback_without_directive(self):
        from core.identity_context import IdentityContext
        ctx = IdentityContext(
            user_id="12345",
            user_name="Ana",
            mode="conversacional",
        )
        block = ctx.to_prompt_block()
        assert "IDENTIDAD ACTIVA" in block
        assert "Ana" in block

    def test_new_fields_default_empty(self):
        from core.identity_context import IdentityContext
        ctx = IdentityContext(user_id="x")
        assert ctx.relationship == ""
        assert ctx.relational_directive == ""
