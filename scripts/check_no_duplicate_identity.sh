#!/usr/bin/env bash
# Class G static analyzer wrapper — delegates to the Python AST scanner.
#
# Blocks commits that introduce get_product_identity() calls or hardcoded
# Vectrax-branded blurbs in user-facing response functions, outside the
# canonical vectrax/identity_handler.py + vectrax/core_identity.py layer.
#
# This script is intentionally a thin wrapper: all detection logic lives
# in core/recovery/static/hardcoded_handler_scan.py, which is unit-tested
# and reused by both pre-commit hooks and CI.

# Resolve repo root from this script's location (works regardless of CWD).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT" || {
  echo "check_no_duplicate_identity: cannot cd to repo root: $REPO_ROOT" >&2
  exit 2
}

PY="${PYTHON:-python3}"

"$PY" -m core.recovery.static.hardcoded_handler_scan --repo-root "$REPO_ROOT"
rc=$?

if [ $rc -ne 0 ]; then
  echo ""
  echo "Commit blocked by Class G static analyzer."
  echo "Route identity responses through vectrax.identity_handler.respond_if_identity."
fi

exit $rc
