#!/usr/bin/env python3
"""
Registro del webhook de Telegram (modo durable, sin long-poll).

Apunta Telegram a:
    {WEBHOOK_BASE_URL}/v1/webhook/telegram/{TELEGRAM_WEBHOOK_SECRET}

y fija el header oficial X-Telegram-Bot-Api-Secret-Token = TELEGRAM_WEBHOOK_SECRET
(doble verificación: secreto en la URL + en el header).

Uso:
    # 1) Generar un secreto fuerte (NO se imprime ningún secreto existente):
    python3 scripts/set_webhook.py --gen-secret
    #    → copia el valor a TELEGRAM_WEBHOOK_SECRET en .env

    # 2) Registrar el webhook (lee TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET,
    #    WEBHOOK_BASE_URL desde el entorno/.env):
    python3 scripts/set_webhook.py

    # Opcional: descartar updates pendientes en la cola de Telegram
    python3 scripts/set_webhook.py --drop-pending

Después:
    - Activar USE_WEBHOOK=1 en .env.
    - Asegurar que WEBHOOK_BASE_URL (p.ej. https://api.vectrax.app) enruta por
      nginx al core_api en :8900.
    - Reiniciar el supervisor: el servicio long-poll telegram_gateway NO se
      levanta; el ingreso entra por el endpoint del core_api.

Rollback en segundos:
    python3 scripts/remove_webhook.py   +   USE_WEBHOOK=0 en .env
"""

from __future__ import annotations

import argparse
import os
import secrets
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

DEFAULT_BASE_URL = "https://api.vectrax.app"
WEBHOOK_PATH = "/v1/webhook/telegram"


def _gen_secret() -> str:
    """Generate a strong URL-safe secret (Telegram allows 1-256 chars: A-Z a-z 0-9 _ -)."""
    return secrets.token_urlsafe(32)


def _redact(url: str, secret: str) -> str:
    """Hide the secret when printing the webhook URL."""
    if secret and secret in url:
        return url.replace(secret, "***SECRET***")
    return url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Register Telegram webhook for Vectrax.")
    parser.add_argument(
        "--gen-secret", action="store_true",
        help="Print a fresh strong secret and exit (does NOT touch Telegram).",
    )
    parser.add_argument(
        "--drop-pending", action="store_true",
        help="Drop updates queued in Telegram (default: keep them).",
    )
    parser.add_argument(
        "--base-url", default=os.environ.get("WEBHOOK_BASE_URL", DEFAULT_BASE_URL),
        help="Public HTTPS base URL that nginx routes to core_api :8900.",
    )
    args = parser.parse_args(argv)

    if args.gen_secret:
        # Print only a freshly generated value — never an existing secret.
        print(_gen_secret())
        print(
            "↑ Copia este valor a TELEGRAM_WEBHOOK_SECRET en .env "
            "(no lo compartas).",
            file=sys.stderr,
        )
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        return 2

    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not secret:
        print(
            "ERROR: TELEGRAM_WEBHOOK_SECRET not set.\n"
            "       Genera uno con: python3 scripts/set_webhook.py --gen-secret",
            file=sys.stderr,
        )
        return 2

    base = args.base_url.strip().rstrip("/")
    if not base.startswith("https://"):
        print(f"ERROR: WEBHOOK_BASE_URL must be https:// (got: {base})", file=sys.stderr)
        return 2

    webhook_url = f"{base}{WEBHOOK_PATH}/{secret}"
    api = f"https://api.telegram.org/bot{token}"

    with httpx.Client(timeout=30) as client:
        payload = {
            "url": webhook_url,
            "secret_token": secret,                 # X-Telegram-Bot-Api-Secret-Token
            "allowed_updates": ["message"],
            "max_connections": 40,
            "drop_pending_updates": bool(args.drop_pending),
        }
        r = client.post(f"{api}/setWebhook", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            print(f"ERROR: setWebhook failed: {data}", file=sys.stderr)
            return 1
        print(f"[OK] setWebhook → {_redact(webhook_url, secret)}")
        print(f"     {data.get('description', 'ok')}")

        # Verify
        r = client.get(f"{api}/getWebhookInfo")
        r.raise_for_status()
        info = r.json().get("result", {})
        set_url = info.get("url", "")
        if not set_url:
            print("WARN: getWebhookInfo reports no URL set", file=sys.stderr)
            return 1
        print(f"[OK] getWebhookInfo url: {_redact(set_url, secret)}")
        print(f"     pending_update_count: {info.get('pending_update_count', 0)}")
        if info.get("last_error_message"):
            print(
                f"WARN: last_error_message: {info['last_error_message']}",
                file=sys.stderr,
            )

    print("")
    print("Next steps (cutover):")
    print("  1. Set USE_WEBHOOK=1 in .env")
    print(f"  2. Ensure nginx routes {base} → core_api :8900")
    print("  3. Restart supervisor (long-poll telegram_gateway will NOT start)")
    print("  4. Send a test message; check /v1/webhook/telegram/status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
