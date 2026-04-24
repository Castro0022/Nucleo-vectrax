"""
Clase G — Static analyzer: detecta handlers hardcoded en tiempo de commit.

Tesis
-----
El detector runtime atrapa el antipatrón cuando ya está en producción. El
detector estático lo atrapa ANTES, cuando alguien intenta commitear un
nuevo `return get_product_identity(...)` o un blurb literal. Un bug
prevenido en pre-commit vale infinitamente más que uno detectado en runtime.

Qué detecta
-----------
1. **Llamadas canónicas** fuera de la capa unificada:
   * `return get_product_identity(...)` importado directamente
     en cualquier archivo que NO esté en la WHITELIST de módulos autorizados
     (actualmente sólo `vectrax/identity_handler.py` y `vectrax/core_identity.py`).

2. **Blurb literal hardcoded** en handlers:
   * `return "<literal larga con vocabulario producto>"` desde una función
     cuyo nombre o parámetros sugieren que responde a un usuario
     (p.ej. `_fast`, `_try_fast_response`, `respond`, `handle_message`,
     o parámetros `text/content/input/message/user`).
   * Las cadenas "vectrax es tu memoria" / "vectrax is your" / "vectrax est"
     (u otras marcadas como `VECTRAX_PRODUCT_SIGNATURE`) son la signatura.

Qué NO detecta (por diseño)
---------------------------
* Docstrings, comentarios, tests — se saltan vía whitelist de rutas.
* Strings que son system prompts o templates en `VECTRAX_*` (nombres de
  módulo de configuración). Ésos viven en `core_identity.py`.
* Imports pasivos de `get_product_identity` (solo dispara si se llama
  y se retorna el resultado).

Uso
---
Como CLI (pre-commit / CI):
    python3 -m core.recovery.static.hardcoded_handler_scan [--paths PATH...]

Exit codes:
    0  → clean
    1  → offenders found; stdout lists them with file:line and suggestion
    2  → scanner error (bad input, etc.)

Como módulo (tests):
    from core.recovery.static.hardcoded_handler_scan import scan_paths, Offender
    offenders = scan_paths([\"vectrax\", \"core\"])
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Whitelist of files allowed to reference get_product_identity / contain the
# canonical blurb. These are the SOLE sources of truth.
# Paths are relative to the repo root and matched as suffixes.
# ---------------------------------------------------------------------------
WHITELIST_SUFFIXES: Tuple[str, ...] = (
    "vectrax/identity_handler.py",
    "vectrax/core_identity.py",
)

# Signature phrases that mark a string literal as the canonical blurb.
# Case-insensitive substring match. Any of these → candidate offender.
VECTRAX_PRODUCT_SIGNATURES: Tuple[str, ...] = (
    "vectrax es tu memoria",
    "vectrax is your intelligent",
    "vectrax est ta m",
    "vectrax \u00e8 la tua memoria",
    "vectrax ist dein intelligentes",
    "vectrax \u00e9 a sua mem",
)

# Names of functions that commonly emit direct user-facing responses and
# therefore must NOT return hardcoded literals longer than MAX_LITERAL_LEN.
# Checked case-insensitively by substring.
RESPONSE_FUNCTION_HINTS: Tuple[str, ...] = (
    "fast", "respond", "reply", "handle_message", "handle_text",
    "answer", "send_message",
)

# Parameter names whose presence indicates a function that reads user input.
USER_INPUT_PARAM_HINTS: Tuple[str, ...] = (
    "text", "content", "input", "message", "user_input", "msg",
)

MAX_LITERAL_LEN = 80  # returns longer than this from response-functions are suspect

# Directories to skip entirely
SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", "dist", "build", "tests", "test",
}

# File suffix patterns that indicate backups / archives — skipped
SKIP_FILE_SUFFIXES = (".bak", ".bak.py")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Offender:
    """A single violation found in a file."""

    filepath: str
    lineno: int
    rule: str  # "GET_PRODUCT_IDENTITY_OUTSIDE_LAYER" | "HARDCODED_BLURB_RETURN"
    message: str
    suggestion: str = ""

    def format(self, repo_root: str = "") -> str:
        rel = os.path.relpath(self.filepath, repo_root) if repo_root else self.filepath
        lines = [f"  {rel}:{self.lineno}  [{self.rule}]", f"    {self.message}"]
        if self.suggestion:
            lines.append(f"    → {self.suggestion}")
        return "\n".join(lines)


@dataclass
class ScanReport:
    offenders: List[Offender] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped_parse_error: int = 0

    def is_clean(self) -> bool:
        return not self.offenders

    def format(self, repo_root: str = "") -> str:
        if self.is_clean():
            return (
                f"OK  scanned={self.files_scanned}  "
                f"skipped(parse-error)={self.files_skipped_parse_error}  "
                f"offenders=0"
            )
        lines = [
            f"FAIL  scanned={self.files_scanned}  "
            f"skipped(parse-error)={self.files_skipped_parse_error}  "
            f"offenders={len(self.offenders)}",
            "",
            "Class G — hardcoded identity handler antipattern detected:",
        ]
        for o in self.offenders:
            lines.append(o.format(repo_root))
        lines.append("")
        lines.append(
            "Fix: route identity responses through "
            "`vectrax.identity_handler.respond_if_identity(text, lang)`. "
            "Do NOT call `get_product_identity` directly or return hardcoded "
            "blurbs from response functions."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------
class _Visitor(ast.NodeVisitor):
    """Walks a single file's AST collecting Offenders."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.offenders: List[Offender] = []
        self._function_stack: List[ast.FunctionDef] = []

    # Track function context so we can check params / names.
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._function_stack.append(node)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node) -> None:  # noqa: N802
        self._function_stack.append(node)  # treat same as sync
        self.generic_visit(node)
        self._function_stack.pop()

    # A direct return that contains either a call to get_product_identity
    # or a long literal with the product signature is suspect.
    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        if node.value is None:
            return
        # Rule 1: return get_product_identity(...)  (possibly wrapped)
        if _call_matches(node.value, "get_product_identity"):
            self.offenders.append(
                Offender(
                    filepath=self.filepath,
                    lineno=node.lineno,
                    rule="GET_PRODUCT_IDENTITY_OUTSIDE_LAYER",
                    message=(
                        "`return get_product_identity(...)` used outside the "
                        "canonical identity_handler layer."
                    ),
                    suggestion=(
                        "Import and call "
                        "`vectrax.identity_handler.respond_if_identity(text, lang)` "
                        "instead."
                    ),
                )
            )
            # Don't generic_visit — we found it.
            return

        # Rule 2: return "<long literal with product signature>"
        literal = _as_literal_string(node.value)
        if literal is not None and _is_response_context(self._function_stack):
            if len(literal) > MAX_LITERAL_LEN and _contains_product_signature(literal):
                self.offenders.append(
                    Offender(
                        filepath=self.filepath,
                        lineno=node.lineno,
                        rule="HARDCODED_BLURB_RETURN",
                        message=(
                            f"Hardcoded Vectrax-branded literal "
                            f"({len(literal)} chars) returned from a user-facing "
                            f"response function."
                        ),
                        suggestion=(
                            "Delegate the response to "
                            "`vectrax.identity_handler.respond_if_identity()`. "
                            "The canonical blurb lives in `core_identity.py`."
                        ),
                    )
                )
                return

        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _call_matches(node: ast.AST, func_name: str) -> bool:
    """True if `node` is a Call (or Await Call) of `func_name` or `module.func_name`.

    Handles patterns:
      func_name(...)
      module.func_name(...)
      await func_name(...)
    """
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name) and f.id == func_name:
        return True
    if isinstance(f, ast.Attribute) and f.attr == func_name:
        return True
    return False


def _as_literal_string(node: ast.AST) -> Optional[str]:
    """If the node is a pure string literal (possibly wrapped in ast.Constant
    or concatenated via BinOp Add / JoinedStr with only Str parts), return
    the combined string. Otherwise None.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f-string: only treat as literal if no FormattedValue parts
        parts: List[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _as_literal_string(node.left)
        right = _as_literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _is_response_context(stack: Sequence[ast.FunctionDef]) -> bool:
    """True if any enclosing function looks like it produces user-facing text."""
    for fn in stack:
        name = fn.name.lower()
        if any(hint in name for hint in RESPONSE_FUNCTION_HINTS):
            return True
        for arg in fn.args.args:
            if arg.arg.lower() in USER_INPUT_PARAM_HINTS:
                return True
    return False


def _contains_product_signature(text: str) -> bool:
    lower = text.lower()
    return any(sig in lower for sig in VECTRAX_PRODUCT_SIGNATURES)


def _is_whitelisted(filepath: str) -> bool:
    norm = filepath.replace(os.sep, "/")
    return any(norm.endswith(s) for s in WHITELIST_SUFFIXES)


def _iter_py_files(root: str) -> Iterable[str]:
    """Yield all .py files under root, skipping well-known noisy dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if any(fn.endswith(suf) for suf in SKIP_FILE_SUFFIXES):
                continue
            yield os.path.join(dirpath, fn)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def scan_file(filepath: str) -> List[Offender]:
    """Scan a single .py file and return its offenders.

    Whitelisted files always return []. Files that fail to parse are treated
    as non-offending (reported via ScanReport.files_skipped_parse_error by
    the caller when scanning many).
    """
    if _is_whitelisted(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    v = _Visitor(filepath)
    v.visit(tree)
    return v.offenders


def scan_paths(paths: Sequence[str]) -> ScanReport:
    """Scan multiple paths (files or dirs) and return a ScanReport."""
    report = ScanReport()
    seen: Set[str] = set()
    for p in paths:
        if os.path.isfile(p) and p.endswith(".py"):
            files: Iterable[str] = [p]
        elif os.path.isdir(p):
            files = _iter_py_files(p)
        else:
            continue
        for fp in files:
            ap = os.path.abspath(fp)
            if ap in seen:
                continue
            seen.add(ap)
            # Count scanned regardless of whitelist (for visibility)
            report.files_scanned += 1
            try:
                offs = scan_file(fp)
            except Exception:
                report.files_skipped_parse_error += 1
                continue
            report.offenders.extend(offs)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _default_paths(repo_root: str) -> List[str]:
    """Default scan targets: all application Python modules."""
    candidates = ["vectrax", "core", "services", "intents"]
    return [os.path.join(repo_root, c) for c in candidates if os.path.isdir(os.path.join(repo_root, c))]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hardcoded_handler_scan",
        description="Class G static analyzer — blocks hardcoded identity handlers.",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Files or directories to scan (default: vectrax/ core/ services/ intents/).",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repo root for relative path display (default: auto-detect from CWD).",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root or _autodetect_repo_root()
    paths = args.paths if args.paths else _default_paths(repo_root)
    if not paths:
        print(f"hardcoded_handler_scan: no scannable paths found under {repo_root}", file=sys.stderr)
        return 2

    report = scan_paths(paths)
    print(report.format(repo_root=repo_root))
    return 0 if report.is_clean() else 1


def _autodetect_repo_root() -> str:
    """Walk up from CWD looking for a .git directory. Fallback to CWD."""
    here = os.path.abspath(os.getcwd())
    cur = here
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return here
        cur = parent


if __name__ == "__main__":
    sys.exit(main())
