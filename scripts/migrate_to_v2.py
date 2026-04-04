#!/usr/bin/env python3
"""
Migrate legacy stars → user_stars + patterns (gravitational v2).

What it does:
  1. Read all rows from the `stars` table
  2. Group by `owner`
  3. For each owner: create a UserStar with centroid = mean_embedding
  4. Insert each star's content as a Pattern
  5. Mario → role=creator, mass=0.50
  6. Recalculate mass, distance, layer for all stars
  7. Refresh the nucleus centroid

Safe to run multiple times (idempotent — uses INSERT OR IGNORE for patterns).

Usage:
    PYTHONPATH=. python3 scripts/migrate_to_v2.py
"""

from __future__ import annotations

import json
import sys
import os
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from vectrax import db
from vectrax.embeddings import decode_embedding, encode_embedding, mean_embedding
from vectrax.gravity import assign_layer, compute_distance_from_mass, compute_user_star_mass
from vectrax.models import (
    CREATOR_INITIAL_MASS, Pattern, ROLE_CREATOR, ROLE_USER, UserStar,
)

CREATOR_OWNER = "mario"


def migrate():
    db.init_db()

    # 1. Read all legacy stars
    all_stars = db.get_all_stars()
    print(f"Legacy stars: {len(all_stars)}")

    if not all_stars:
        print("No legacy stars to migrate.")
        return

    # 2. Group by owner
    by_owner = defaultdict(list)
    for s in all_stars:
        owner = s.owner or "unknown"
        by_owner[owner].append(s)

    print(f"Distinct owners: {len(by_owner)}")

    migrated_stars = 0
    migrated_patterns = 0

    for owner, stars in sorted(by_owner.items(), key=lambda x: -len(x[1])):
        is_creator = (owner == CREATOR_OWNER)
        role = ROLE_CREATOR if is_creator else ROLE_USER

        # 3. Collect embeddings for centroid
        vecs = []
        topics = defaultdict(int)

        for s in stars:
            # 4. Insert as Pattern (idempotent)
            topic = "general"
            try:
                from core.smart_router import SmartRouter
                _r = SmartRouter()
                ctx = _r.detect_context(s.content, "user", owner)
                topic = ctx.get("topic", "general")
            except Exception:
                pass

            pattern = Pattern(
                id=s.id,  # reuse star id for traceability
                user_id=owner,
                content=s.content,
                embedding=s.embedding,
                topic=topic,
                timestamp=s.timestamp,
            )
            try:
                db.insert_pattern(pattern)
                migrated_patterns += 1
            except Exception:
                pass  # already exists (idempotent)

            topics[topic] += 1
            if s.embedding:
                vecs.append(decode_embedding(s.embedding))

        # 5. Compute centroid
        centroid_bytes = None
        if vecs:
            centroid = mean_embedding(vecs)
            centroid_bytes = encode_embedding(centroid)

        # 6. Create UserStar
        mass = compute_user_star_mass(
            pattern_count=len(stars),
            topic_diversity=len(topics),
            is_creator=is_creator,
        )
        distance = compute_distance_from_mass(mass)
        layer = assign_layer(mass)

        user_star = UserStar(
            user_id=owner,
            role=role,
            embedding=centroid_bytes,
            mass=mass,
            distance_to_core=distance,
            layer=layer,
            pattern_count=len(stars),
            topic_fingerprint=json.dumps(dict(topics)),
            activation_count=sum(s.activation_count for s in stars),
            last_active=max(s.timestamp for s in stars),
            created_at=min(s.timestamp for s in stars),
        )
        db.upsert_user_star(user_star)
        migrated_stars += 1

        marker = "★" if is_creator else "☆"
        print(
            f"  {marker} {owner[:20]:20s} → {len(stars):3d} patterns | "
            f"mass={mass:.4f} | dist={distance:.4f} | layer={layer} | "
            f"topics={len(topics)}"
        )

    # 7. Refresh nucleus
    try:
        from vectrax.core_nucleus import refresh_centroid_v2
        refresh_centroid_v2()
        print("\nNucleus centroid refreshed.")
    except Exception as e:
        print(f"\nNucleus refresh failed: {e}")

    print(f"\nMigration complete: {migrated_stars} stars, {migrated_patterns} patterns")

    # Summary
    universe = db.get_universe_status()
    print(f"Universe: {universe['stars']} stars, {universe['patterns']} patterns, "
          f"total_mass={universe['total_mass']}")
    print(f"Layers: {universe['layers']}")


if __name__ == "__main__":
    migrate()
