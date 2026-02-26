"""
Vectrax Smoke Test Runner
=========================
Validates project health: syntax, imports, structure.
Exit 0 = all good. Exit 1 = failure (details in output).
"""

import ast
import os
import sys
import json
import importlib
import traceback

BASE_DIR = os.path.expanduser("~/Vectrax")

# Files that MUST exist
REQUIRED_FILES = [
    "app.py",
    "nucleo.py",
    "vectrax_engine.py",
    "vectrax_unified.py",
    "core/__init__.py",
    "core/rules.py",
    "core/ingest.py",
    "core/memory_sqlite.py",
    "config/autonomy.json",
]

# Core modules to attempt import
IMPORT_CHECKS = [
    "core.rules",
    "core.ingest",
    "core.memory_sqlite",
]


def log(msg, level="INFO"):
    print(f"[smoke][{level}] {msg}")


def check_file_exists(relpath):
    """Check that a required file exists."""
    full = os.path.join(BASE_DIR, relpath)
    if not os.path.isfile(full):
        return f"Missing required file: {relpath}"
    return None


def check_python_syntax(filepath):
    """Parse a .py file to verify syntax."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        ast.parse(source, filename=filepath)
        return None
    except SyntaxError as e:
        return f"SyntaxError in {filepath}: {e}"


def check_json_valid(filepath):
    """Validate JSON files."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            json.load(f)
        return None
    except (json.JSONDecodeError, FileNotFoundError) as e:
        return f"Invalid JSON {filepath}: {e}"


def check_config_schema():
    """Validate autonomy.json has required keys."""
    cfg_path = os.path.join(BASE_DIR, "config", "autonomy.json")
    try:
        with open(cfg_path, "r") as f:
            cfg = json.load(f)
        ap = cfg.get("autopatch", {})
        required_keys = ["enabled", "max_retries", "scope_allowlist", "scope_denylist", "protected_paths"]
        missing = [k for k in required_keys if k not in ap]
        if missing:
            return f"autonomy.json missing keys: {missing}"
        return None
    except Exception as e:
        return f"autonomy.json error: {e}"


def collect_python_files():
    """Collect all .py files under BASE_DIR, skipping .venv and __pycache__."""
    py_files = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", ".git", "keys", "secrets")]
        for fname in files:
            if fname.endswith(".py"):
                py_files.append(os.path.join(root, fname))
    return py_files


def run_smoke():
    """Run all smoke checks. Returns (passed: bool, errors: list[str])."""
    errors = []

    # 1. Required files
    log("Checking required files...")
    for rf in REQUIRED_FILES:
        err = check_file_exists(rf)
        if err:
            errors.append(err)

    # 2. Syntax check all .py files
    log("Checking Python syntax...")
    for pyf in collect_python_files():
        err = check_python_syntax(pyf)
        if err:
            errors.append(err)

    # 3. JSON validation
    log("Checking JSON configs...")
    for root, dirs, files in os.walk(os.path.join(BASE_DIR, "config")):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", ".git")]
        for fname in files:
            if fname.endswith(".json"):
                err = check_json_valid(os.path.join(root, fname))
                if err:
                    errors.append(err)

    # 4. Config schema
    log("Validating autonomy.json schema...")
    err = check_config_schema()
    if err:
        errors.append(err)

    # 5. Import checks (best-effort, skip if deps missing)
    log("Checking core imports...")
    original_dir = os.getcwd()
    os.chdir(BASE_DIR)
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    for mod_name in IMPORT_CHECKS:
        try:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
            else:
                importlib.import_module(mod_name)
        except ImportError as e:
            # Only fail on missing local modules, not third-party
            if "sentence_transformers" in str(e) or "numpy" in str(e) or "torch" in str(e):
                log(f"Skipping optional dep: {mod_name} ({e})", "WARN")
            else:
                errors.append(f"ImportError for {mod_name}: {e}")
        except Exception as e:
            errors.append(f"Error importing {mod_name}: {e}")
    os.chdir(original_dir)

    passed = len(errors) == 0
    return passed, errors


def main():
    passed, errors = run_smoke()

    if passed:
        log("All smoke checks PASSED ✓", "OK")
        sys.exit(0)
    else:
        log(f"Smoke FAILED — {len(errors)} error(s):", "FAIL")
        for i, e in enumerate(errors, 1):
            log(f"  [{i}] {e}", "FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
