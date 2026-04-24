#!/usr/bin/env python3
"""
Setup script: registra el webhook en Telegram (idempotente).

Uso:
    # Requiere TELEGRAM_BOT_TOKEN y TELEGRAM_WEBHOOK_SECRET en el env.
    WEBHOOK_URL=https://api.vectrax.app python3 scripts/setup_webhook.py
    # O temporal con cloudflared:
    WEBHOOK_URL=https://xxx.trycloudflare.com python3 scripts/setup_webhook.py

Qué hace:
    1. Lee TELEGRAM_BOT_TOKEN y TELEGRAM_WEBHOOK_SECRET del entorno.
    2. Construye la URL: $WEBHOOK_URL/v1/webhook/telegram/$SECRET
    3. Llama setWebhook con:
         - url = URL construida
         - secret_token = SECRET (Telegram lo envía en header X-Telegram-Bot-Api-Secret-Token)
         - max_connections = 40 (default, razonable)
         - allowed_updates = ["message"]  (mismo scope que el long-poll)
         - drop_pending_updates = False  (no queremos perder mensajes en vuelo)
    4. Verifica con getWebhookInfo que el registro tomó.

Safe para re-ejecutar: Telegram sobrescribe la configuración previa.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure project root importable so .env loader works from anywhere
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

import httpx


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    base_url = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        return 2
    if not secret:
        print("ERROR: TELEGRAM_WEBHOOK_SECRET not set", file=sys.stderr)
        return 2
    if not base_url:
        print("ERROR: WEBHOOK_URL not set (e.g., https://api.vectrax.app)", file=sys.stderr)
        return 2
    if not base_url.startswith("https://"):
        print(f"ERROR: WEBHOOK_URL must be https (got: {base_url})", file=sys.stderr)
        return 2

    webhook_path = f"/v1/webhook/telegram/{secret}"
    webhook_full_url = f"{base_url}{webhook_path}"

    # Display URL with secret REDACTED for logs
    redacted_url = f"{base_url}/v1/webhook/telegram/***REDACTED***"
    print(f"Setting webhook: {redacted_url}")

    api = f"https://api.telegram.org/bot{token}"
    payload = {
        "url": webhook_full_url,
        "secret_token": secret,
        "max_connections": 40,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    }

    with httpx.Client(timeout=30) as client:
        # setWebhook
        r = client.post(f"{api}/setWebhook", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            print(f"ERROR: setWebhook failed: {data}", file=sys.stderr)
            return 1
        print(f"[OK] setWebhook: {data.get('description', 'ok')}")

        # Verify with getWebhookInfo
        r = client.get(f"{api}/getWebhookInfo")
        r.raise_for_status()
        info = r.json().get("result", {})
        print("")
        print("=== WEBHOOK INFO (verification) ===")
        # Redact URL in output
        if info.get("url"):
            info["url"] = info["url"].replace(secret, "***REDACTED***")
        print(json.dumps(info, indent=2, ensure_ascii=False))

        if not info.get("url", "").endswith(webhook_path.replace(secret, "***REDACTED***")):
            # URL matches but with the actual (not redacted) secret — check by suffix
            # We already redacted info["url"] above; recheck against the redacted pattern
            expected_redacted = f"{base_url}/v1/webhook/telegram/***REDACTED***"
            if info.get("url") != expected_redacted:
                print(
                    f"WARN: webhook URL mismatch. Got: {info.get('url')}, expected: {expected_redacted}",
                    file=sys.stderr,
                )
                return 1

        pending = info.get("pending_update_count", 0)
        if pending:
            print(f"  Pending updates in Telegram queue: {pending}")

        last_err = info.get("last_error_message")
        if last_err:
            print(f"WARN: last_error_message: {last_err}", file=sys.stderr)

    print("")
    print("[OK] Webhook registered. Telegram will now push updates to:")
    print(f"  {redacted_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
