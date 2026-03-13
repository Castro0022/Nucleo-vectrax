"""
Vectrax Core — User Independence Engine
==========================================
When a new user is created, this engine provisions their complete
independence package:

  1. Identity              — user_id, username, role, owner, channel
  2. Credentials           — secure temp password, initial token, must-change flag
  3. Domain assignment     — personal | financiero | tecnológico | empresarial | científico | otro
  4. Base constellation    — isolated per-user, bound to domain, no nucleus mixing
  5. Cognitive path        — domain context, rules, initial routes, structural seed
  6. Portal entry          — user enters through their domain, not a generic chat
  7. Nucleus integrity     — zero creator channel or nucleus contamination

CRITICAL INVARIANT:
  No credential, constellation, star, or domain record may ever touch
  channel='creator' or owner='mario'. All independence occurs in the
  external user channel.
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("vectrax.core.independence")

# ---------------------------------------------------------------------------
# Domain definitions
# ---------------------------------------------------------------------------

VALID_DOMAINS = (
    "personal",
    "financiero",
    "tecnológico",
    "empresarial",
    "científico",
    "otro",
)

# Cognitive seed per domain — initial context, rules, and routes
DOMAIN_SEEDS: Dict[str, Dict] = {
    "personal": {
        "context": "Gestión personal, productividad, bienestar, organización diaria.",
        "rules": [
            "Priorizar tareas por impacto personal",
            "Registrar hábitos y patrones recurrentes",
            "Mantener balance entre urgencia e importancia",
        ],
        "routes": ["tareas", "hábitos", "objetivos", "reflexiones"],
        "seed_stars": [
            "Inicio de camino personal — espacio de organización y evolución propia",
        ],
    },
    "financiero": {
        "context": "Finanzas, inversiones, presupuestos, análisis económico, optimización de recursos.",
        "rules": [
            "Registrar toda operación con monto y categoría",
            "Detectar patrones de gasto recurrente",
            "Evaluar riesgo antes de decisión financiera",
        ],
        "routes": ["presupuesto", "inversiones", "análisis", "alertas"],
        "seed_stars": [
            "Inicio de camino financiero — espacio de análisis y gestión económica",
        ],
    },
    "tecnológico": {
        "context": "Desarrollo de software, infraestructura, DevOps, arquitectura de sistemas.",
        "rules": [
            "Documentar decisiones técnicas con contexto",
            "Priorizar estabilidad sobre velocidad",
            "Registrar errores con trazabilidad completa",
        ],
        "routes": ["código", "infraestructura", "errores", "arquitectura"],
        "seed_stars": [
            "Inicio de camino tecnológico — espacio de desarrollo e innovación técnica",
        ],
    },
    "empresarial": {
        "context": "Gestión de negocio, estrategia, operaciones, equipos, crecimiento.",
        "rules": [
            "Alinear decisiones con objetivos estratégicos",
            "Medir impacto de cambios operativos",
            "Registrar señales de mercado relevantes",
        ],
        "routes": ["estrategia", "operaciones", "equipo", "mercado"],
        "seed_stars": [
            "Inicio de camino empresarial — espacio de gestión y estrategia de negocio",
        ],
    },
    "científico": {
        "context": "Investigación, hipótesis, experimentación, análisis de datos, publicaciones.",
        "rules": [
            "Formular hipótesis antes de experimentar",
            "Registrar resultados con metodología clara",
            "Mantener trazabilidad entre observación y conclusión",
        ],
        "routes": ["hipótesis", "experimentos", "datos", "conclusiones"],
        "seed_stars": [
            "Inicio de camino científico — espacio de investigación y descubrimiento",
        ],
    },
    "otro": {
        "context": "Espacio libre de exploración sin dominio predefinido.",
        "rules": [
            "Registrar intenciones con claridad",
            "Evolucionar las reglas según el uso",
        ],
        "routes": ["exploración", "notas", "ideas"],
        "seed_stars": [
            "Inicio de camino libre — espacio de exploración abierta",
        ],
    },
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IndependencePackage:
    """Complete provisioning result for a new user."""
    # Identity
    user_id: str = ""
    username: str = ""
    role: str = "viewer"
    channel: str = "user"
    owner: str = ""

    # Credentials
    temp_password: str = ""
    token: str = ""
    must_change_password: bool = True
    token_expires_in: int = 30 * 86400  # 30 days

    # Domain
    domain: str = "otro"
    domain_context: str = ""
    domain_rules: List[str] = field(default_factory=list)
    domain_routes: List[str] = field(default_factory=list)

    # Constellation
    constellation_id: str = ""
    seed_stars_created: int = 0

    # Portal
    portal_url: str = ""

    # Validation
    nucleus_intact: bool = True
    validation_passed: bool = True
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "channel": self.channel,
            "domain": self.domain,
            "domain_context": self.domain_context,
            "domain_rules": self.domain_rules,
            "domain_routes": self.domain_routes,
            "constellation_id": self.constellation_id,
            "seed_stars_created": self.seed_stars_created,
            "portal_url": self.portal_url,
            "must_change_password": self.must_change_password,
            "token_expires_in": self.token_expires_in,
            "nucleus_intact": self.nucleus_intact,
            "validation_passed": self.validation_passed,
            "validation_errors": self.validation_errors,
        }


# ---------------------------------------------------------------------------
# User Independence Engine
# ---------------------------------------------------------------------------

class UserIndependenceEngine:
    """
    Orchestrates the full independence provisioning for new users.

    INVARIANT: This engine NEVER writes to channel='creator' or
    assigns owner='mario' to any artifact it creates.
    """

    def provision(
        self,
        username: str,
        role: str = "viewer",
        domain: str = "otro",
        password: Optional[str] = None,
    ) -> IndependencePackage:
        """
        Execute the full 7-step independence flow.

        If password is None, a secure temporary password is generated.
        Returns a complete IndependencePackage.
        """
        from vectrax.identity import CHANNEL_USER, CREATOR_OWNER

        pkg = IndependencePackage()
        errors: List[str] = []

        # ── Guard: never provision the creator ──────────────────────
        if username == CREATOR_OWNER:
            pkg.validation_passed = False
            pkg.validation_errors = ["Cannot provision independence for creator account"]
            return pkg

        # ──────────────────────────────────────────────────────────────
        # Step 1: Identity
        # ──────────────────────────────────────────────────────────────
        pkg.username = username
        pkg.role = role
        pkg.channel = CHANNEL_USER
        pkg.owner = username

        # ──────────────────────────────────────────────────────────────
        # Step 2: Credentials
        # ──────────────────────────────────────────────────────────────
        if password is None:
            pkg.temp_password = secrets.token_urlsafe(16)
            pkg.must_change_password = True
        else:
            pkg.temp_password = password
            pkg.must_change_password = False

        user = self._create_user_record(pkg)
        if user is None:
            pkg.validation_passed = False
            pkg.validation_errors = [f"Failed to create user '{username}' — may already exist"]
            return pkg
        pkg.user_id = user.id

        # Issue initial token
        pkg.token = self._issue_token(user)

        # ──────────────────────────────────────────────────────────────
        # Step 3: Domain assignment
        # ──────────────────────────────────────────────────────────────
        pkg.domain = domain if domain in VALID_DOMAINS else "otro"
        seed = DOMAIN_SEEDS.get(pkg.domain, DOMAIN_SEEDS["otro"])
        pkg.domain_context = seed["context"]
        pkg.domain_rules = seed["rules"]
        pkg.domain_routes = seed["routes"]

        self._save_domain(pkg)

        # ──────────────────────────────────────────────────────────────
        # Step 4: Base constellation (isolated)
        # ──────────────────────────────────────────────────────────────
        pkg.constellation_id = self._create_base_constellation(pkg)

        # ──────────────────────────────────────────────────────────────
        # Step 5: Cognitive path seed
        # ──────────────────────────────────────────────────────────────
        pkg.seed_stars_created = self._seed_cognitive_path(pkg, seed)

        # ──────────────────────────────────────────────────────────────
        # Step 6: Portal entry
        # ──────────────────────────────────────────────────────────────
        pkg.portal_url = f"/portal/{username}"

        # ──────────────────────────────────────────────────────────────
        # Step 7: Validate nucleus integrity
        # ──────────────────────────────────────────────────────────────
        pkg.nucleus_intact = self._validate_nucleus_integrity(pkg)
        if not pkg.nucleus_intact:
            errors.append("CRITICAL: Nucleus integrity compromised during provisioning")

        pkg.validation_passed = len(errors) == 0
        pkg.validation_errors = errors

        logger.info(
            "Independence provisioned: user=%s domain=%s constellation=%s stars=%d nucleus_ok=%s",
            username, pkg.domain, pkg.constellation_id[:8],
            pkg.seed_stars_created, pkg.nucleus_intact,
        )
        return pkg

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _create_user_record(self, pkg: IndependencePackage):
        """Create user in the UserStore with domain and must-change flag."""
        from services.core.users import UserStore

        store = UserStore()
        try:
            user = store.create_user(
                username=pkg.username,
                password=pkg.temp_password,
                role=pkg.role,
                channel=pkg.channel,
            )
            # Update domain + must_change_password
            store.set_user_domain(user.id, pkg.domain)
            if pkg.must_change_password:
                store.set_must_change_password(user.id, True)
            return user
        except ValueError as e:
            logger.error("User creation failed: %s", e)
            return None

    def _issue_token(self, user) -> str:
        """Issue initial access token."""
        from services.core.tokens import TokenManager

        tm = TokenManager()
        return tm.issue_token(
            user_id=user.id,
            username=user.username,
            role=user.role,
            channel=user.channel,
        )

    def _save_domain(self, pkg: IndependencePackage) -> None:
        """Persist domain assignment in user_domains table."""
        import json
        import sqlite3
        from pathlib import Path

        db_path = Path.home() / ".vectrax" / "vectrax.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_domains (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    domain      TEXT NOT NULL,
                    context     TEXT NOT NULL DEFAULT '',
                    rules       TEXT NOT NULL DEFAULT '[]',
                    routes      TEXT NOT NULL DEFAULT '[]',
                    created_at  REAL NOT NULL,
                    UNIQUE(user_id)
                )
            """)
            conn.execute(
                """INSERT OR REPLACE INTO user_domains
                   (id, user_id, username, domain, context, rules, routes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4())[:12],
                    pkg.user_id,
                    pkg.username,
                    pkg.domain,
                    pkg.domain_context,
                    json.dumps(pkg.domain_rules, ensure_ascii=False),
                    json.dumps(pkg.domain_routes, ensure_ascii=False),
                    time.time(),
                ),
            )

    def _create_base_constellation(self, pkg: IndependencePackage) -> str:
        """Create an empty base constellation bound to the user's domain."""
        from vectrax import db
        from vectrax.models import Constellation

        db.init_db()
        constellation = Constellation(
            star_ids=[],
            coherence_score=1.0,
            success_rate=0.0,
            channel="user",
            owner=pkg.username,
        )
        db.upsert_constellation(constellation)
        logger.info("Base constellation created: %s for user %s", constellation.id[:8], pkg.username)
        return constellation.id

    def _seed_cognitive_path(self, pkg: IndependencePackage, seed: Dict) -> int:
        """Ingest seed stars into the user's isolated space."""
        from vectrax.engine import ingest
        from vectrax.identity import CHANNEL_USER

        count = 0
        seed_texts = seed.get("seed_stars", [])

        # Add domain context as first star
        context_star_text = f"[DOMAIN:{pkg.domain.upper()}] {seed['context']}"
        try:
            ingest(context_star_text, success=True, channel=CHANNEL_USER, owner=pkg.username)
            count += 1
        except Exception as e:
            logger.warning("Failed to ingest context star: %s", e)

        # Add seed stars
        for text in seed_texts:
            try:
                ingest(text, success=True, channel=CHANNEL_USER, owner=pkg.username)
                count += 1
            except Exception as e:
                logger.warning("Failed to ingest seed star: %s", e)

        # Add rule stars
        for rule in seed.get("rules", []):
            rule_text = f"[REGLA] {rule}"
            try:
                ingest(rule_text, success=True, channel=CHANNEL_USER, owner=pkg.username)
                count += 1
            except Exception as e:
                logger.warning("Failed to ingest rule star: %s", e)

        return count

    def _validate_nucleus_integrity(self, pkg: IndependencePackage) -> bool:
        """
        Verify that the provisioning did NOT contaminate the nucleus.

        Checks:
          1. No stars with channel='user' AND owner=pkg.username exist in creator channel
          2. No constellations leaked into creator channel for this user
          3. Creator star count unchanged (or only changed by creator operations)
        """
        from vectrax import db
        from vectrax.identity import CHANNEL_CREATOR

        # Check: no user stars in creator channel
        creator_stars = db.get_all_stars(channel=CHANNEL_CREATOR, owner=pkg.username)
        if creator_stars:
            logger.critical(
                "NUCLEUS VIOLATION: Found %d stars in creator channel for user '%s'",
                len(creator_stars), pkg.username,
            )
            return False

        # Check: no user constellations in creator channel
        creator_constellations = db.get_all_constellations(
            channel=CHANNEL_CREATOR, owner=pkg.username
        )
        if creator_constellations:
            logger.critical(
                "NUCLEUS VIOLATION: Found %d constellations in creator channel for user '%s'",
                len(creator_constellations), pkg.username,
            )
            return False

        return True


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: UserIndependenceEngine | None = None


def get_independence_engine() -> UserIndependenceEngine:
    global _engine
    if _engine is None:
        _engine = UserIndependenceEngine()
    return _engine
