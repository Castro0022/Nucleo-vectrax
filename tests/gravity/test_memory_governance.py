"""
tests/gravity/test_memory_governance.py — 7 verificaciones obligatorias
del Memory Governance Engine.

  1. Saludos simples NO suben a DEEP.
  2. 3 eventos (Persona=Naomy, Lugar=Casa) crean ContextIdentity.
  3. Records DEEP NO reciben decay.
  4. Persona reconocida NO recibe decay.
  5. EssentialSummary genera principios correctos.
  6. Retrieval prioriza identity_mass sobre fecha.
  7. Aislamiento estricto user_id (no memory bleed entre A y B).
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TMPDIR = tempfile.mkdtemp(prefix="vx_governance_test_")
os.environ["VECTRAX_GRAVITY_DB"] = os.path.join(_TMPDIR, "gov.db")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.gravity import (  # noqa: E402
    SQLiteVectorStore,
    DeepMemoryRecord,
    MemoryGovernor,
    ConceptFusion,
    MemoryDecay,
    EssentialSummaryEngine,
    retrieve,
    rank_results,
    derive_identity_key,
    PROTECTED_MEMORY_TYPES,
)


# ===========================================================================
# Helpers
# ===========================================================================

def fake_embed(text: str, dim: int = 8) -> list:
    anchors = ["naomy", "foto", "playa", "casa", "mario",
               "parque", "cafe", "libro"]
    v = [0.0] * dim
    if not text:
        return v
    low = text.lower()
    for i, a in enumerate(anchors[:dim]):
        if a in low:
            v[i] = 1.0
    if all(x == 0.0 for x in v):
        v[-1] = 0.001
    return v


def make_record(user_id, raw_text, tags=None, ts=None):
    return DeepMemoryRecord(
        user_id=user_id,
        raw_text=raw_text,
        embedding=fake_embed(raw_text + " " + " ".join(tags or [])),
        tags=tags or [],
        ts=ts if ts is not None else time.time(),
    )


def fresh_store(name: str) -> SQLiteVectorStore:
    s = SQLiteVectorStore(db_path=os.path.join(_TMPDIR, f"{name}.db"))
    s.reset_for_tests()
    return s


# ===========================================================================
# Test 1 — Saludos no suben a DEEP
# ===========================================================================

class TestNoiseStaysShallow(unittest.TestCase):
    def test_hola_does_not_promote(self):
        store = fresh_store("noise")
        try:
            uid = "tg:user_a"
            for txt in ["Hola", "Hey", "Gracias", "Ok"]:
                store.upsert(make_record(uid, txt))
            governor = MemoryGovernor(store)
            promoted = governor.promote_warm_to_deep(uid)
            self.assertEqual(
                promoted, 0,
                f"Esperaba 0 promociones para saludos; se promovieron {promoted}",
            )
            # Ningún record debe tener memory_type DEEP
            for r in store.list_active_for_user(uid):
                self.assertNotEqual(r["memory_type"], "DEEP")
                self.assertNotIn(r["memory_type"], PROTECTED_MEMORY_TYPES)
        finally:
            store.close()


# ===========================================================================
# Test 2 — 3 eventos Persona+Lugar → ContextIdentity
# ===========================================================================

class TestConceptFusion(unittest.TestCase):
    def test_three_events_create_context_identity(self):
        store = fresh_store("fusion")
        try:
            uid = "tg:user_a"
            tags = ["person:Naomy", "location:Casa"]
            ev1 = make_record(uid, "Naomy en casa hicimos café", tags=tags)
            ev2 = make_record(uid, "Tarde con Naomy en casa", tags=tags)
            ev3 = make_record(uid, "Naomy llegó a casa de noche",
                              tags=tags + ["emotion:joy"])
            for r in (ev1, ev2, ev3):
                store.upsert(r)

            fusion = ConceptFusion(store, threshold=3)
            self.assertEqual(
                derive_identity_key(
                    {"tags": ["person:Naomy", "location:Casa"]},
                ),
                "Naomy|Casa",
            )

            ident = fusion.fuse_if_threshold_met(uid, ev1.__dict__ | {
                "id": ev1.id, "tags": tags,
            })
            self.assertIsNotNone(ident)
            self.assertEqual(ident["identity_key"], "Naomy|Casa")
            self.assertEqual(ident["evidence_count"], 3)
            self.assertGreater(ident["identity_mass"], 0)
            self.assertIn("Naomy", ident["people"])
            self.assertEqual(ident["location"], "Casa")

            # Las evidencias originales se preservan, marcadas FUSED
            ev = store.get_record(ev1.id)
            self.assertIsNotNone(ev)
            self.assertEqual(ev["fused_into"], ident["id"])
            self.assertEqual(ev["memory_status"], "FUSED")
        finally:
            store.close()


# ===========================================================================
# Test 3 — DEEP no recibe decay
# ===========================================================================

class TestDeepNotDecayed(unittest.TestCase):
    def test_deep_records_skipped_by_decay(self):
        store = fresh_store("deep")
        try:
            uid = "tg:user_a"
            old_ts = time.time() - 90 * 86400  # 90 días atrás
            r = make_record(uid, "Decision importante", tags=["decision"],
                            ts=old_ts)
            store.upsert(r)
            store.set_memory_type(r.id, "DEEP")
            store.set_gravity_score(r.id, 8.0)

            decay = MemoryDecay(store)
            now = datetime.datetime.utcnow()
            affected = decay.decay_cold_memories(uid, now=now)
            self.assertEqual(affected, 0)
            after = store.get_record(r.id)
            self.assertEqual(after["gravity_score"], 8.0)
            self.assertEqual(after["memory_type"], "DEEP")
        finally:
            store.close()


# ===========================================================================
# Test 4 — Persona reconocida no recibe decay
# ===========================================================================

class TestPersonNotDecayed(unittest.TestCase):
    def test_person_recognized_skipped_by_decay(self):
        store = fresh_store("person")
        try:
            uid = "tg:user_a"
            old_ts = time.time() - 200 * 86400  # 200 días atrás
            r = make_record(uid, "Mario y Naomy",
                            tags=["person:Naomy"], ts=old_ts)
            store.upsert(r)
            store.set_gravity_score(r.id, 4.0)

            decay = MemoryDecay(store)
            now = datetime.datetime.utcnow()
            affected = decay.decay_cold_memories(uid, now=now)
            self.assertEqual(affected, 0)
            after = store.get_record(r.id)
            self.assertEqual(after["gravity_score"], 4.0)
        finally:
            store.close()


# ===========================================================================
# Test 5 — EssentialSummary genera principios
# ===========================================================================

class TestEssentialSummary(unittest.TestCase):
    def test_extracts_person_preference_principle(self):
        store = fresh_store("essential")
        try:
            uid = "tg:user_a"
            # Persona recurrente (Naomy x3)
            for i in range(3):
                store.upsert(make_record(
                    uid, f"Día con Naomy #{i}", tags=["person:Naomy"],
                ))
            # Preferencia recurrente: café fuerte x2
            for i in range(2):
                store.upsert(make_record(
                    uid, "Me gusta el café fuerte",
                    tags=["preference", "pref:café fuerte"],
                ))
            # Principio declarado
            principle_rec = make_record(
                uid, "Prefiero respuestas directas",
                tags=["principle"],
            )
            store.upsert(principle_rec)

            engine = EssentialSummaryEngine(store, user_name="Mario")
            principles = engine.extract_user_principles(
                store.list_all_for_user(uid),
            )
            summaries = [p["summary"] for p in principles]
            joined = " | ".join(summaries)

            self.assertTrue(
                any("Naomy" in s and "vínculo recurrente" in s
                    for s in summaries),
                f"falta vínculo recurrente: {joined}",
            )
            self.assertTrue(
                any("le gusta" in s.lower() and "café" in s.lower()
                    for s in summaries),
                f"falta preferencia café: {joined}",
            )
            self.assertTrue(
                any("respuestas directas" in s.lower() for s in summaries),
                f"falta principio respuestas directas: {joined}",
            )

            # Persistencia
            ids = engine.persist_essential_summary(uid, principles)
            self.assertEqual(len(ids), len(principles))
            stored = store.list_essential_memories(uid)
            self.assertEqual(len(stored), len(principles))
        finally:
            store.close()


# ===========================================================================
# Test 6 — Retrieval prioriza identity_mass sobre fecha
# ===========================================================================

class TestRetrievalPriorityIdentityOverDate(unittest.TestCase):
    def test_high_mass_old_beats_low_mass_new(self):
        store = fresh_store("rank")
        try:
            uid = "tg:user_a"
            now = time.time()

            # Record viejo, alto identity_mass (DEEP, gravity_score alto)
            old = DeepMemoryRecord(
                user_id=uid,
                raw_text="Naomy en casa, momento clave",
                embedding=fake_embed("naomy casa foto"),
                tags=["person:Naomy", "location:Casa"],
                mass=20.0,
                ts=now - 365 * 86400,  # 1 año atrás
            )
            store.upsert(old)
            store.set_memory_type(old.id, "DEEP")
            store.set_gravity_score(old.id, 10.0)

            # Record nuevo, baja masa
            new = DeepMemoryRecord(
                user_id=uid,
                raw_text="Foto random en casa",
                embedding=fake_embed("naomy casa foto"),
                tags=["location:Casa"],
                mass=0.5,
                ts=now,
            )
            store.upsert(new)
            store.set_gravity_score(new.id, 0.5)

            # Query semánticamente similar a ambos
            q = fake_embed("naomy casa foto")
            results = retrieve(store, uid, q, limit=5)
            self.assertGreater(len(results), 0)
            top = results[0]
            self.assertEqual(top["id"], old.id,
                             f"Esperaba el record DEEP viejo en top, got {top['id']}")
            # Y el final_score del DEEP debe superar al nuevo
            scores_by_id = {r["id"]: r["final_score"] for r in results}
            self.assertGreater(
                scores_by_id[old.id], scores_by_id.get(new.id, 0),
            )

            # rank_results pure: mismo orden (sin side-effect)
            active = store.list_active_for_user(uid)
            ranked = rank_results(active, q)
            self.assertEqual(ranked[0]["id"], old.id)
        finally:
            store.close()


# ===========================================================================
# Test 7 — Aislamiento estricto: User A jamás accede memoria User B
# ===========================================================================

class TestStrictIsolation(unittest.TestCase):
    def test_user_a_never_sees_user_b(self):
        store = fresh_store("isolation")
        try:
            uid_a, uid_b = "tg:user_a", "tg:user_b"
            store.upsert(make_record(
                uid_a, "Naomy en casa",
                tags=["person:Naomy", "location:Casa"],
            ))
            store.upsert(make_record(
                uid_b, "Naomy en casa",
                tags=["person:Naomy", "location:Casa"],
            ))
            store.upsert(make_record(uid_b, "Café fuerte",
                                     tags=["preference"]))

            # Retrieve para user_a NO debe traer data de user_b
            q = fake_embed("naomy casa")
            res_a = retrieve(store, uid_a, q, limit=10)
            for r in res_a:
                self.assertEqual(r["user_id"], uid_a)

            res_b = retrieve(store, uid_b, q, limit=10)
            for r in res_b:
                self.assertEqual(r["user_id"], uid_b)

            # ConceptFusion: la identity de user_b no se ve para user_a
            fusion = ConceptFusion(store, threshold=2)
            seed = {"tags": ["person:Naomy", "location:Casa"]}
            similars_a = fusion.find_similar_concepts(uid_a, seed)
            for s in similars_a:
                self.assertEqual(s["user_id"], uid_a)

            # Essential summary: principios de A solo de records de A
            engine = EssentialSummaryEngine(store, user_name="A")
            ps_a = engine.extract_user_principles(
                store.list_all_for_user(uid_a),
            )
            for p in ps_a:
                # ev_ids tienen que pertenecer a uid_a (los principios
                # SOVEREIGN-* son sintéticos, no estan en la DB — se
                # excluyen del check de aislamiento porque no son
                # cross-user.)
                for eid in p["evidence_ids"]:
                    if isinstance(eid, str) and eid.startswith("SOVEREIGN-"):
                        continue
                    rec = store.get_record(eid)
                    self.assertIsNotNone(rec, f"missing rec for {eid}")
                    self.assertEqual(rec["user_id"], uid_a)
        finally:
            store.close()


# ===========================================================================
# Bonus — DEEP gana al ranking incluso sin retrieval_count
# ===========================================================================

class TestRankingBreakdown(unittest.TestCase):
    def test_breakdown_includes_components(self):
        store = fresh_store("breakdown")
        try:
            uid = "tg:user_a"
            r = make_record(uid, "Naomy", tags=["person:Naomy"])
            store.upsert(r)
            store.set_memory_type(r.id, "DEEP")
            store.set_gravity_score(r.id, 5.0)
            results = retrieve(store, uid, fake_embed("naomy"), limit=3)
            self.assertGreater(len(results), 0)
            top = results[0]
            for k in ("score_semantic", "score_mass",
                       "score_recency", "score_retrieval", "final_score"):
                self.assertIn(k, top)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
