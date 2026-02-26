"""
Vectrax Autopatch Engine
========================
Closed-loop autonomous patching:
  1. Run smoke → detect failures
  2. Git checkpoint (stash or commit)
  3. Apply fix (syntax-level auto-repair)
  4. Re-run smoke
  5. If pass → commit. If fail after N retries → rollback.

Reads config from config/autonomy.json.
Logs to logs/autopatch.log.
Report to reports/autopatch_last.json.
"""

import ast
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.expanduser("~/Vectrax")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "autonomy.json")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f).get("autopatch", {})


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class PatchLogger:
    def __init__(self, log_path):
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.log_path = log_path

    def log(self, msg, level="INFO"):
        line = f"[{now_ts()}][{level}] {msg}"
        print(line)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------

def is_in_scope(filepath, cfg):
    """Check if a file is within allowlist and not in denylist or protected."""
    rel = os.path.relpath(filepath, BASE_DIR)

    # Check denylist
    for pattern in cfg.get("scope_denylist", []):
        if pattern.endswith("/"):
            if rel.startswith(pattern) or rel.startswith(pattern.rstrip("/")):
                return False
        elif pattern.startswith("*."):
            if rel.endswith(pattern[1:]):
                return False
        elif rel == pattern:
            return False

    # Check protected
    for p in cfg.get("protected_paths", []):
        if p.endswith("/"):
            if rel.startswith(p) or rel.startswith(p.rstrip("/")):
                return False
        elif rel == p:
            return False

    # Check allowlist
    allowlist = cfg.get("scope_allowlist", [])
    if not allowlist:
        return True
    for pattern in allowlist:
        if pattern.endswith("/"):
            if rel.startswith(pattern) or rel.startswith(pattern.rstrip("/")):
                return True
        elif rel == pattern:
            return True
    return False


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(*args):
    """Run a git command in BASE_DIR, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git_checkpoint(logger):
    """Create a checkpoint commit for rollback."""
    git("add", "-A")
    rc, out, err = git("diff", "--cached", "--quiet")
    if rc != 0:
        # There are staged changes
        git("commit", "-m", "[autopatch] checkpoint before patch attempt")
        logger.log("Git checkpoint created.")
    else:
        logger.log("Working tree clean, no checkpoint needed.")
    # Return current HEAD for rollback
    rc, head, _ = git("rev-parse", "HEAD")
    return head.strip() if rc == 0 else None


def git_rollback(target_hash, logger):
    """Hard reset to a specific commit."""
    if not target_hash:
        logger.log("No rollback target — skipping.", "WARN")
        return False
    rc, _, err = git("reset", "--hard", target_hash)
    if rc == 0:
        logger.log(f"Rolled back to {target_hash[:8]}.")
        return True
    else:
        logger.log(f"Rollback failed: {err}", "ERROR")
        return False


def git_commit_patch(message, logger):
    """Stage all and commit."""
    git("add", "-A")
    rc, out, err = git("diff", "--cached", "--quiet")
    if rc == 0:
        logger.log("Nothing to commit after patch.")
        return False
    rc, out, err = git("commit", "-m", message)
    if rc == 0:
        logger.log(f"Committed: {message}")
        return True
    else:
        logger.log(f"Commit failed: {err}", "ERROR")
        return False


# ---------------------------------------------------------------------------
# Smoke runner (subprocess to get exit code)
# ---------------------------------------------------------------------------

def run_smoke(logger):
    """Run smoke via subprocess. Returns (passed, output)."""
    smoke_script = os.path.join(BASE_DIR, "core", "smoke.py")
    try:
        result = subprocess.run(
            [sys.executable, smoke_script],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        passed = result.returncode == 0
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Smoke test timed out (60s)"
    except Exception as e:
        return False, f"Smoke execution error: {e}"


# ---------------------------------------------------------------------------
# Auto-fix: syntax repair agent
# ---------------------------------------------------------------------------

def detect_syntax_errors(logger, cfg):
    """Find .py files with syntax errors that are in scope."""
    errors = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", ".git", "keys", "secrets")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            if not is_in_scope(fpath, cfg):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
                ast.parse(source, filename=fpath)
            except SyntaxError as e:
                errors.append({
                    "file": fpath,
                    "line": e.lineno,
                    "offset": e.offset,
                    "msg": str(e.msg),
                    "text": e.text.rstrip() if e.text else "",
                })
    return errors


def attempt_fix(error, logger):
    """
    Try to auto-fix a syntax error. Returns True if fixed.
    Strategies:
      - Missing colon at end of def/class/if/for/while/with/try/except/elif/else
      - Unmatched quotes (truncated string)
      - Unmatched parentheses/brackets
      - Invalid character or encoding fix
      - Remove the offending line as last resort
    """
    fpath = error["file"]
    line_no = error["line"]
    msg = error["msg"]

    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return False

    if not lines or line_no is None or line_no < 1 or line_no > len(lines):
        return False

    idx = line_no - 1
    original_line = lines[idx]
    fixed = False

    # Strategy 1: Missing colon
    if "expected ':'" in msg or ("invalid syntax" in msg):
        stripped = original_line.rstrip()
        block_starters = ("def ", "class ", "if ", "elif ", "else", "for ", "while ", "with ", "try", "except", "finally")
        if any(stripped.lstrip().startswith(kw) for kw in block_starters):
            if not stripped.endswith(":"):
                lines[idx] = stripped + ":\n"
                fixed = True
                logger.log(f"Fix: added missing ':' at {fpath}:{line_no}")

    # Strategy 2: Unmatched quotes
    if not fixed and ("EOL while scanning string" in msg or "unterminated string" in msg):
        line = original_line.rstrip()
        for q in ['"""', "'''", '"', "'"]:
            count = line.count(q)
            if count % 2 != 0:
                lines[idx] = line + q + "\n"
                fixed = True
                logger.log(f"Fix: closed unmatched quote at {fpath}:{line_no}")
                break

    # Strategy 3: Unmatched parens/brackets — remove trailing opener if obvious
    if not fixed and ("unexpected EOF" in msg or "was never closed" in msg):
        # Try to find unmatched opener in previous lines
        for check_idx in range(max(0, idx - 3), idx + 1):
            line = lines[check_idx].rstrip()
            opens = line.count("(") - line.count(")")
            if opens > 0:
                lines[check_idx] = line + ")" * opens + "\n"
                fixed = True
                logger.log(f"Fix: closed {opens} unmatched paren(s) at {fpath}:{check_idx + 1}")
                break
            opens = line.count("[") - line.count("]")
            if opens > 0:
                lines[check_idx] = line + "]" * opens + "\n"
                fixed = True
                logger.log(f"Fix: closed {opens} unmatched bracket(s) at {fpath}:{check_idx + 1}")
                break

    # Strategy 4: Comment out the broken line as last resort
    if not fixed:
        indent = len(original_line) - len(original_line.lstrip())
        lines[idx] = " " * indent + "# [autopatch] removed broken line: " + original_line.lstrip()
        fixed = True
        logger.log(f"Fix: commented out broken line at {fpath}:{line_no}")

    if fixed:
        with open(fpath, "w", encoding="utf-8") as f:
            f.writelines(lines)
        # Verify fix actually resolved the syntax error
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                ast.parse(f.read(), filename=fpath)
            return True
        except SyntaxError:
            # Revert
            lines[idx] = original_line
            with open(fpath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            logger.log(f"Fix reverted — still broken at {fpath}:{line_no}", "WARN")
            return False

    return False


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(report, cfg):
    report_path = os.path.join(BASE_DIR, cfg.get("report_file", "reports/autopatch_last.json"))
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main autopatch cycle
# ---------------------------------------------------------------------------

def run_autopatch():
    cfg = load_config()

    if not cfg.get("enabled", False):
        print("[autopatch] Disabled in config. Set autopatch.enabled=true.")
        sys.exit(0)

    log_path = os.path.join(BASE_DIR, cfg.get("log_file", "logs/autopatch.log"))
    logger = PatchLogger(log_path)
    max_retries = cfg.get("max_retries", 3)

    logger.log("=" * 60)
    logger.log("AUTOPATCH CYCLE START")
    logger.log(f"Max retries: {max_retries}")

    report = {
        "started_at": now_iso(),
        "attempts": [],
        "result": None,
        "rollback": False,
        "finished_at": None,
    }

    # Step 1: Initial smoke
    logger.log("Running initial smoke test...")
    passed, smoke_out = run_smoke(logger)

    if passed:
        logger.log("Smoke passed on first run. Nothing to patch.")
        report["result"] = "clean"
        report["finished_at"] = now_iso()
        write_report(report, cfg)
        return True

    logger.log("Smoke FAILED. Entering patch loop...")

    # Step 2: Git checkpoint
    checkpoint = git_checkpoint(logger)
    logger.log(f"Checkpoint: {checkpoint[:8] if checkpoint else 'N/A'}")

    # Step 3: Patch loop
    for attempt in range(1, max_retries + 1):
        logger.log(f"--- Attempt {attempt}/{max_retries} ---")
        attempt_record = {
            "attempt": attempt,
            "started_at": now_iso(),
            "errors_found": [],
            "fixes_applied": [],
            "smoke_passed": False,
        }

        # Detect errors
        syntax_errors = detect_syntax_errors(logger, cfg)
        attempt_record["errors_found"] = [
            {"file": os.path.relpath(e["file"], BASE_DIR), "line": e["line"], "msg": e["msg"]}
            for e in syntax_errors
        ]

        if not syntax_errors:
            logger.log("No syntax errors found, but smoke still fails (non-syntax issue).")
            # Smoke failure might be non-syntax — can't auto-fix
            attempt_record["fixes_applied"] = []
            attempt_record["smoke_passed"] = False
            report["attempts"].append(attempt_record)
            break

        # Apply fixes
        for err in syntax_errors:
            rel = os.path.relpath(err["file"], BASE_DIR)
            if attempt_fix(err, logger):
                attempt_record["fixes_applied"].append(rel)
            else:
                logger.log(f"Could not fix {rel}:{err['line']}", "WARN")

        # Re-run smoke
        logger.log("Re-running smoke after fixes...")
        passed, smoke_out = run_smoke(logger)
        attempt_record["smoke_passed"] = passed
        attempt_record["finished_at"] = now_iso()
        report["attempts"].append(attempt_record)

        if passed:
            logger.log(f"Smoke PASSED on attempt {attempt}!")

            # Auto-commit
            if cfg.get("auto_commit", True):
                prefix = cfg.get("commit_prefix", "[autopatch]")
                fixes = ", ".join(attempt_record["fixes_applied"][:5])
                msg = f"{prefix} auto-fix: {fixes}"
                git_commit_patch(msg, logger)

            report["result"] = "fixed"
            report["finished_at"] = now_iso()
            write_report(report, cfg)
            logger.log("AUTOPATCH CYCLE COMPLETE — FIXED ✓")
            return True

        logger.log(f"Attempt {attempt} failed.", "WARN")

    # Step 4: All retries exhausted → rollback
    logger.log(f"All {max_retries} attempts exhausted.", "ERROR")

    if cfg.get("auto_rollback", True) and checkpoint:
        logger.log("Rolling back to checkpoint...")
        rolled = git_rollback(checkpoint, logger)
        report["rollback"] = rolled
        report["result"] = "rollback"
    else:
        report["result"] = "failed"
        logger.log("Auto-rollback disabled or no checkpoint. Manual intervention needed.", "ERROR")

    report["finished_at"] = now_iso()
    write_report(report, cfg)
    logger.log("AUTOPATCH CYCLE COMPLETE — " + report["result"].upper())
    return False


def main():
    success = run_autopatch()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
