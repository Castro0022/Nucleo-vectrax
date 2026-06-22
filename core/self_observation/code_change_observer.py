"""
core/self_observation/code_change_observer.py — observe source changes.

Records git commits / diffs as abstract ``code_change`` observations (domain
``code``) via the :mod:`core.self_observation.quality_observer` contract, so the
living universe can show what changed, where (which components), and how big the
change was — and correlate it with test failures/recoveries.

ABSTRACTION (governance): only abstract metadata is stored — repo-relative file
paths, per-change insertion/deletion COUNTS, the touched components (scope /
modules), the branch and the short commit SHA. The commit MESSAGE, author,
emails and the actual diff CONTENT are never read into the ledger.

This module NEVER pushes, mutates, or installs git hooks. It only runs read-only
git plumbing (``rev-parse``, ``show --numstat``, ``diff --numstat``). It is meant
to be CALLED from a hook or CI step that you wire up yourself, e.g.:

    # .git/hooks/post-commit  (you create this; we do not)
    #!/bin/sh
    python -m core.self_observation.code_change_observer --rev HEAD

Public API:
    observe_commit(rev="HEAD", repo_dir=None) -> int
    observe_diff(repo_dir=None, staged=False, base=None) -> int
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from typing import List, Optional, Tuple

from core.self_observation import quality_observer as qo

logger = logging.getLogger("vectrax.code_change_observer")


# ---------------------------------------------------------------------------
# git helpers (read-only)
# ---------------------------------------------------------------------------

def _git(args: List[str], cwd: Optional[str] = None) -> str:
    """Run a read-only git command with a timeout. Returns stdout or ''."""
    base = ["git"]
    if cwd:
        base += ["-C", cwd]
    base += ["--no-pager"]
    try:
        r = subprocess.run(
            base + list(args),
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return r.stdout
        logger.debug("git %s rc=%s: %s", args, r.returncode, r.stderr.strip()[:200])
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.debug("git %s failed: %s", args, exc)
    return ""


def _git_root(cwd: Optional[str] = None) -> Optional[str]:
    out = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return out.strip() or None


def _current_branch(cwd: Optional[str] = None) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).strip()


def _short_sha(rev: str, cwd: Optional[str] = None) -> str:
    return _git(["rev-parse", "--short", rev], cwd=cwd).strip()


def _clean_path(raw: str) -> str:
    """Normalize a numstat path, resolving rename syntax to the new path.

    git renders renames as ``old => new`` or ``dir/{old => new}/file``. We only
    keep the resulting (new) path — still just a repo-relative path, no content.
    """
    p = raw.strip()
    if "=>" in p:
        # dir/{a => b}/x.py  ->  dir/b/x.py   ;   a => b  ->  b
        p = p.replace("{", "").replace("}", "")
        left, _, right = p.partition("=>")
        p = (left.rsplit("/", 1)[0] + "/" + right.strip()) if "/" in left and right.strip() and not right.strip().startswith("/") else right.strip() or left.strip()
        p = p.replace("//", "/").strip("/ ")
    return p


def _parse_numstat(out: str) -> Tuple[List[str], int, int]:
    """Parse ``--numstat`` output → (files, insertions, deletions).

    Each line: ``<ins>\\t<del>\\t<path>``. Binary files show ``-`` for counts.
    """
    files: List[str] = []
    insertions = 0
    deletions = 0
    for line in out.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ins_s, del_s, path = parts[0], parts[1], "\t".join(parts[2:])
        path = _clean_path(path)
        if not path:
            continue
        files.append(path)
        if ins_s.isdigit():
            insertions += int(ins_s)
        if del_s.isdigit():
            deletions += int(del_s)
    return files, insertions, deletions


# ---------------------------------------------------------------------------
# Public observers
# ---------------------------------------------------------------------------

def observe_commit(rev: str = "HEAD", repo_dir: Optional[str] = None) -> int:
    """Record one commit as a ``code_change`` event. Returns the ledger row id
    (or -1 if the commit could not be inspected)."""
    root = _git_root(repo_dir) or repo_dir
    if not root:
        logger.debug("observe_commit: not a git repo (%s)", repo_dir)
        return -1
    sha = _short_sha(rev, cwd=root)
    if not sha:
        logger.debug("observe_commit: cannot resolve %s", rev)
        return -1
    branch = _current_branch(cwd=root)
    out = _git(["show", "--numstat", "--format=", rev], cwd=root)
    files, insertions, deletions = _parse_numstat(out)
    if not files:
        # Fall back to name-only (e.g. when numstat is empty) so we still record
        # the affected files even without line counts.
        names = _git(["show", "--name-only", "--format=", rev], cwd=root)
        files = [_clean_path(l) for l in names.splitlines() if l.strip()]
    return qo.record_code_change(
        files=files,
        insertions=insertions,
        deletions=deletions,
        scope=qo._scopes(files),
        branch=branch,
        commit=sha,
        source="commit",
    )


def observe_diff(
    repo_dir: Optional[str] = None,
    *,
    staged: bool = False,
    base: Optional[str] = None,
) -> int:
    """Record the current diff (working tree, staged index, or vs ``base``) as a
    ``code_change`` event. Returns the ledger row id, 0 if there was nothing to
    record, or -1 if the repo could not be inspected."""
    root = _git_root(repo_dir) or repo_dir
    if not root:
        logger.debug("observe_diff: not a git repo (%s)", repo_dir)
        return -1
    if base:
        args = ["diff", "--numstat", base]
        source = "range"
    elif staged:
        args = ["diff", "--numstat", "--cached"]
        source = "staged"
    else:
        args = ["diff", "--numstat"]
        source = "worktree"
    files, insertions, deletions = _parse_numstat(_git(args, cwd=root))
    if not files:
        return 0
    return qo.record_code_change(
        files=files,
        insertions=insertions,
        deletions=deletions,
        scope=qo._scopes(files),
        branch=_current_branch(cwd=root),
        commit=None,
        source=source,
    )


# ---------------------------------------------------------------------------
# CLI (for hooks / CI — installs nothing)
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="code_change_observer",
        description="Record a git commit or diff as an abstract code_change "
                    "observation. Installs no hooks; call from your own hook/CI.",
    )
    parser.add_argument("--repo", default=None, help="repo directory (default: cwd)")
    parser.add_argument("--rev", default="HEAD", help="commit to observe (default: HEAD)")
    parser.add_argument("--diff", action="store_true",
                        help="observe the current diff instead of a commit")
    parser.add_argument("--staged", action="store_true",
                        help="with --diff: observe the staged index")
    parser.add_argument("--base", default=None,
                        help="with --diff: observe the diff against this ref")
    args = parser.parse_args(argv)

    if args.diff or args.base:
        row = observe_diff(args.repo, staged=args.staged, base=args.base)
    else:
        row = observe_commit(args.rev, args.repo)
    # row > 0 recorded, 0 nothing to record, -1 error.
    return 0 if row >= 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
