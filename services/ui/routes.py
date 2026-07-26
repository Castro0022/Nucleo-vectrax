"""
Vectrax UI — Route handler
============================
Serves the single-page application, universe portals, and personalized user portals.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["ui"])

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_DB_PATH = Path.home() / ".vectrax" / "vectrax.db"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    """Serve the main UI page."""
    index_path = _TEMPLATE_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Vectrax UI not found</h1>", status_code=404)


@router.get("/universe", response_class=HTMLResponse, include_in_schema=False)
async def universe():
    """Serve the interactive cognitive universe visualizer (unified view)."""
    unified_path = Path(__file__).resolve().parent / "static" / "universe.html"
    if unified_path.exists():
        return HTMLResponse(
            content=unified_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )
    return HTMLResponse(content="<h1>Universe not found</h1>", status_code=404)


@router.get("/presence", response_class=HTMLResponse, include_in_schema=False)
async def presence():
    """Serve the Creator Channel viewer (La Presencia — campo vivo)."""
    presence_path = Path(__file__).resolve().parent / "static" / "presence.html"
    if presence_path.exists():
        return HTMLResponse(
            content=presence_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )
    return HTMLResponse(content="<h1>Presence not found</h1>", status_code=404)


@router.get("/universe/legacy", response_class=HTMLResponse, include_in_schema=False)
async def universe_legacy():
    """Legacy gravitational universe (knowledge stars, constellations)."""
    legacy_path = _TEMPLATE_DIR / "universe_legacy.html"
    if legacy_path.exists():
        return HTMLResponse(content=legacy_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Legacy universe not found</h1>", status_code=404)


@router.get("/portals", response_class=HTMLResponse, include_in_schema=False)
async def portals():
    """Serve the Universe Portals — dimensional entry map."""
    portals_path = _TEMPLATE_DIR / "portals.html"
    if portals_path.exists():
        return HTMLResponse(content=portals_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Portals not found</h1>", status_code=404)


@router.get("/portal/{username}", response_class=HTMLResponse, include_in_schema=False)
async def user_portal(username: str):
    """
    Personalized portal entry per user domain.

    Looks up the user's assigned domain and renders a domain-specific
    portal page that routes them directly into their cognitive space.
    """
    domain = _get_user_domain(username)
    domain_info = _get_domain_info(username)

    # Domain color mapping
    domain_colors = {
        "personal":     ("#fbbf24", "◈"),
        "financiero":   ("#34d399", "◆"),
        "tecnol\u00f3gico":  ("#4f8ff7", "⬡"),
        "empresarial":  ("#06b6d4", "◎"),
        "cient\u00edfico":   ("#a78bfa", "★"),
        "otro":         ("#6b7280", "●"),
    }
    color, icon = domain_colors.get(domain, ("#6b7280", "●"))

    context = domain_info.get("context", "") if domain_info else ""
    rules = domain_info.get("rules", []) if domain_info else []
    routes = domain_info.get("routes", []) if domain_info else []

    rules_html = "".join(f'<li>{r}</li>' for r in rules)
    routes_html = "".join(
        f'<a href="/?view=chat" class="route-chip">{r}</a>' for r in routes
    )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Vectrax — Portal {username}</title>
  <link rel="stylesheet" href="/static/portals.css">
  <style>
    .user-portal {{ max-width: 640px; margin: 60px auto; padding: 40px 32px; text-align: center; position: relative; z-index: 10; }}
    .up-icon {{ font-size: 56px; color: {color}; margin-bottom: 16px; text-shadow: 0 0 30px {color}40; }}
    .up-name {{ font-size: 28px; font-weight: 700; color: #fff; margin-bottom: 4px; }}
    .up-domain {{ font-size: 14px; color: {color}; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 20px; }}
    .up-context {{ font-size: 14px; color: #8899aa; line-height: 1.6; margin-bottom: 24px; }}
    .up-rules {{ text-align: left; margin: 0 auto 24px; max-width: 400px; }}
    .up-rules li {{ font-size: 13px; color: #6b7280; margin-bottom: 6px; list-style: none; }}
    .up-rules li::before {{ content: "▸ "; color: {color}; }}
    .route-chips {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-bottom: 28px; }}
    .route-chip {{ padding: 6px 16px; border-radius: 20px; font-size: 12px; font-weight: 600;
                   background: {color}15; color: {color}; border: 1px solid {color}30;
                   text-decoration: none; transition: background 0.2s; }}
    .route-chip:hover {{ background: {color}30; }}
    .up-enter {{ display: inline-block; padding: 12px 32px; border-radius: 8px;
                 background: {color}; color: #000; font-size: 15px; font-weight: 700;
                 text-decoration: none; transition: opacity 0.2s; }}
    .up-enter:hover {{ opacity: 0.85; }}
    .up-back {{ display: block; margin-top: 16px; font-size: 12px; color: #5a6374; text-decoration: none; }}
    .up-back:hover {{ color: #d1d5db; }}
  </style>
</head>
<body>
  <canvas id="starfield"></canvas>
  <div class="user-portal">
    <div class="up-icon">{icon}</div>
    <div class="up-name">{username}</div>
    <div class="up-domain">{domain}</div>
    <p class="up-context">{context}</p>
    <ul class="up-rules">{rules_html}</ul>
    <div class="route-chips">{routes_html}</div>
    <a href="/?view=chat" class="up-enter">Entrar a mi espacio</a>
    <a href="/portals" class="up-back">← Universo Vectrax</a>
  </div>
  <script src="/static/portals.js"></script>
</body>
</html>"""
    return HTMLResponse(content=html)


def _get_user_domain(username: str) -> str:
    """Look up user's domain from the users table."""
    if not _DB_PATH.exists():
        return "otro"
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT domain FROM users WHERE username = ?", (username,)
            ).fetchone()
        return row["domain"] if row else "otro"
    except Exception:
        return "otro"


def _get_domain_info(username: str) -> dict | None:
    """Look up user's domain info from user_domains table."""
    if not _DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM user_domains WHERE username = ?", (username,)
            ).fetchone()
        if not row:
            return None
        return {
            "domain": row["domain"],
            "context": row["context"],
            "rules": json.loads(row["rules"]) if row["rules"] else [],
            "routes": json.loads(row["routes"]) if row["routes"] else [],
        }
    except Exception:
        return None
