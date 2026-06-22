#!/usr/bin/env bash
# ============================================================================
# Vectrax — Host Health Watcher
# ============================================================================
# Runs on the HOST (outside the container) so a hung container can't disable
# the watcher. Probes core_api /health; on SUSTAINED failure it alerts the
# creator via Telegram (and sends a recovery notice when it comes back).
#
# This complements the in-supervisor self-heal (_check_core_api_http, which
# restarts a hung core_api): the supervisor RECOVERS, this watcher NOTIFIES
# and leaves a record — and still fires even if the supervisor itself is stuck.
#
# Install (host cron, every 5 min):
#   ( crontab -l 2>/dev/null | grep -v 'health_watch.sh'; \
#     echo '*/5 * * * * /opt/vectrax/scripts/health_watch.sh >> /root/.vectrax/health_watch.log 2>&1' ) | crontab -
#
# Reads TELEGRAM_BOT_TOKEN + TELEGRAM_CREATOR_CHAT_ID from $VX_ENV_FILE (.env).
# No secrets are printed; the token is only used in the Telegram API call.
# ============================================================================

ENV_FILE="${VX_ENV_FILE:-/opt/vectrax/.env}"
HEALTH_URL="${VX_HEALTH_URL:-http://localhost:8900/health}"
STATE_DIR="${VX_WATCH_STATE_DIR:-/root/.vectrax}"
DOWN_FLAG="$STATE_DIR/health_watch.down"
COOLDOWN_S="${VX_WATCH_COOLDOWN_S:-1800}"   # max 1 'still down' alert / 30 min
RETRIES="${VX_WATCH_RETRIES:-3}"            # consecutive fails before alerting
RETRY_GAP_S="${VX_WATCH_RETRY_GAP_S:-5}"

mkdir -p "$STATE_DIR" 2>/dev/null

_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

_send_telegram() {
  # $1 = message text. Best-effort; never fails the script.
  [ -f "$ENV_FILE" ] || return 0
  local tok chat
  tok="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
  chat="$(grep -E '^TELEGRAM_CREATOR_CHAT_ID=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
  [ -n "$tok" ] && [ -n "$chat" ] || return 0
  curl -s -o /dev/null --max-time 10 \
    "https://api.telegram.org/bot${tok}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=${1}" >/dev/null 2>&1 || true
}

# Probe /health with retries (tolerates a brief blip / deploy restart).
code="000"
ok=0
i=1
while [ "$i" -le "$RETRIES" ]; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$HEALTH_URL" 2>/dev/null || echo 000)"
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep "$RETRY_GAP_S"
  i=$((i + 1))
done

if [ "$ok" = "1" ]; then
  echo "$(_ts) OK 200"
  if [ -f "$DOWN_FLAG" ]; then
    _send_telegram "✅ Vectrax core_api RECUPERADO ($(_ts)) — /health responde 200."
    rm -f "$DOWN_FLAG"
  fi
  exit 0
fi

# Sustained failure.
echo "$(_ts) DOWN (last_code=$code, retries=$RETRIES)"
now="$(date +%s)"
last=0
[ -f "$DOWN_FLAG" ] && last="$(cat "$DOWN_FLAG" 2>/dev/null || echo 0)"
if [ ! -f "$DOWN_FLAG" ] || [ "$((now - last))" -ge "$COOLDOWN_S" ]; then
  _send_telegram "🔴 Vectrax core_api NO responde ($(_ts)) — /health != 200 tras ${RETRIES} intentos (último code=${code}). El supervisor debería reiniciarlo automáticamente; revisa si persiste."
  echo "$now" > "$DOWN_FLAG"
fi
exit 0
