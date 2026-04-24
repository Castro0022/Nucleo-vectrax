#!/usr/bin/env python3
"""
Rollback script: elimina el webhook en Telegram.

Uso:
    python3 scripts/remove_webhook.py

Después de esto:
    - Telegram deja de enviar POSTs al webhook.
    - Los updates vuelven a estar disponibles via getUpdates (long-poll).
    - Volver a long-poll: set USE_WEBHOOK=0 en .env + redeploy + start
      telegram_gateway service.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        return 2

    api = f"https://api.telegram.org/bot{token}"

    with httpx.Client(timeout=30) as client:
        # Show current webhook before removing
        r = client.get(f"{api}/getWebhookInfo")
        r.raise_for_status()
        info = r.json().get("result", {})
        current_url = info.get("url", "")
        if current_url:
            # Redact any secret-looking suffix
            parts = current_url.split("/")
            if len(parts) > 2:
                parts[-1] = "***REDACTED***"
                display_url = "/".join(parts)
            else:
                display_url = current_url
            print(f"Current webhook: {display_url}")
        else:
            print("No webhook currently set (already cleared).")
            return 0

        # deleteWebhook — drop_pending_updates=False preserves the queue
        # so if you switch back to long-poll, you get the pending messages.
        r = client.post(
            f"{api}/deleteWebhook",
            json={"drop_pending_updates": False},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            print(f"ERROR: deleteWebhook failed: {data}", file=sys.stderr)
            return 1
        print(f"[OK] deleteWebhook: {data.get('description', 'ok')}")

        # Verify
        r = client.get(f"{api}/getWebhookInfo")
        r.raise_for_status()
        info = r.json().get("result", {})
        if info.get("url"):
            print(f"WARN: webhook still set: {info['url']}", file=sys.stderr)
            return 1

        pending = info.get("pending_update_count", 0)
        print(f"[OK] webhook cleared. Pending updates in Telegram queue: {pending}")
        if pending:
            print("  These will be delivered via next long-poll getUpdates call.")

    print("")
    print("Next steps (to return to long-poll mode):")
    print("  1. Set USE_WEBHOOK=0 in .env on the server")
    print("  2. Redeploy (rsync + docker compose up -d)")
    print("  3. Verify telegram_gateway service is running in supervisor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
