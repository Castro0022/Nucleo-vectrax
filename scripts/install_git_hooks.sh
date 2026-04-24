#!/usr/bin/env bash
# Install git pre-commit hook that runs the Class G static analyzer.
#
# Safe to re-run; it overwrites .git/hooks/pre-commit with the current
# wrapper content and re-applies executable bits. Does NOT touch the
# repo's working tree.
#
# Usage:
#   bash scripts/install_git_hooks.sh

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
HOOK_PATH="$HOOKS_DIR/pre-commit"

if [ ! -d "$HOOKS_DIR" ]; then
  echo "install_git_hooks: .git/hooks not found at $HOOKS_DIR" >&2
  exit 2
fi

# Pre-commit content delegates to the unit-tested shell wrapper, which in
# turn delegates to the Python AST scanner. Keeps one source of truth.
cat > "$HOOK_PATH" <<'HOOK_EOF'
#!/usr/bin/env bash
# Auto-installed by scripts/install_git_hooks.sh
# Class G — block commits that introduce hardcoded identity handlers.
REPO_ROOT="$(git rev-parse --show-toplevel)"
exec bash "$REPO_ROOT/scripts/check_no_duplicate_identity.sh"
HOOK_EOF

chmod +x "$HOOK_PATH"
chmod +x "$REPO_ROOT/scripts/check_no_duplicate_identity.sh"

echo "Installed pre-commit hook: $HOOK_PATH"
echo "Runs: scripts/check_no_duplicate_identity.sh → Class G static analyzer"
