# ==========================
# VECTRAX - Comandos (limpio)
# ==========================

# Puertos (ajusta si cambian)
export VECTRAX_CORE="${VECTRAX_CORE:-http://127.0.0.1:9005}"
export VECTRAX_VOICE="${VECTRAX_VOICE:-http://127.0.0.1:9010}"

# Hablar (TTS)
vh () {
  curl -s -X POST "$VECTRAX_VOICE/voz/hablar" \
    -H "Content-Type: application/json" \
    -d "{\"texto\":\"$*\"}" >/dev/null
}

# Hablar (alias humano)
habla () { vh "$*"; }

# Preguntar al Núcleo (devuelve texto)
vp () {
  local q="$*"
  local resp
  resp=$(curl -sG "$VECTRAX_CORE/preguntar" --data-urlencode "q=$q")
  python3 - <<PY
import json
raw = """$resp"""
try:
    d=json.loads(raw)
    t=d.get("respuesta") or d.get("mensaje") or raw
except Exception:
    t=raw
print(t)
PY
}

# Preguntar y hablar
vpa () {
  local txt
  txt=$(vp "$*")
  vh "$txt"
  echo "$txt"
}

# Guardar nota en ARGOS (usa el trigger guardar:)
vg () {
  curl -sG "$VECTRAX_CORE/preguntar" --data-urlencode "q=guardar: $*" ; echo
}

# Ver última nota (requiere /argos/ultima)
vu () {
  local id
  id=$(curl -s "$VECTRAX_CORE/argos/ultima" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])' 2>/dev/null)
  if [ -z "$id" ]; then
    echo "No pude obtener /argos/ultima en $VECTRAX_CORE"
    return 1
  fi
  curl -s "$VECTRAX_CORE/argos/ver?id=$id" ; echo
}

# Arrancar Vectrax — delega al único camino soberano
despierta () {
  vx activate && echo "" && vx watch
}
alias vboot='despierta'

# Ping rápido
vping () {
  echo "CORE=$VECTRAX_CORE"
  curl -s "$VECTRAX_CORE/argos/mapa?limit=1" ; echo
  echo "VOICE=$VECTRAX_VOICE"
  curl -s "$VECTRAX_VOICE/voz/ping" ; echo
}

# vu2: última nota vía /argos/mapa (fallback si no existe /argos/ultima)
vu2 () {
  local id
  id=$(curl -s "$VECTRAX_CORE/argos/mapa?limit=1" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["notas"][0]["id"] if d.get("notas") else "")' 2>/dev/null)
  if [ -z "$id" ]; then
    echo "No pude obtener /argos/mapa en $VECTRAX_CORE"
    return 1
  fi
  curl -s "$VECTRAX_CORE/argos/ver?id=$id" ; echo
}
