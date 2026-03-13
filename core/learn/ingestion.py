"""
Vectrax Ingestion Pipeline
============================
For each Python file:
  1. Parse / extract facts (imports, classes, functions, docstrings)
  2. Scrub (PII / secrets)
  3. Infer intent
  4. Create pattern candidate (QUARANTINED)
  5. Upsert semantic index
  6. Log to episodic ledger

Modes:
  - ``ingest_file(path)``  — single file (for watchdog events)
  - ``full_scan()``        — walk all watched directories
"""

from __future__ import annotations

import ast
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.learn import PROJECT_ROOT, WATCHED_DIRS
from core.learn.scrubber import scrub
from core.learn.intent_engine import infer_intent
from core.learn.episodic import get_ledger, EpisodicEvent, content_hash
from core.learn.ascension_gate import get_gate, PatternCandidate


# ---------------------------------------------------------------------------
# Structure extraction
# ---------------------------------------------------------------------------

@dataclass
class FileStructure:
    """Extracted facts from a Python file."""
    file_path: str
    imports: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    docstrings: List[str] = field(default_factory=list)
    line_count: int = 0

    @property
    def symbols(self) -> List[str]:
        return self.classes + self.functions

    def summary_text(self) -> str:
        parts = []
        if self.classes:
            parts.append(f"classes: {', '.join(self.classes)}")
        if self.functions:
            parts.append(f"functions: {', '.join(self.functions)}")
        if self.imports:
            parts.append(f"imports: {', '.join(self.imports[:10])}")
        parts.append(f"{self.line_count} lines")
        return "; ".join(parts)


def extract_structure(file_path: str) -> Optional[FileStructure]:
    """Parse a Python file and extract structural facts."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError:
        return None

    fs = FileStructure(file_path=file_path, line_count=source.count("\n") + 1)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Still extract what we can from raw text
        return fs

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                fs.imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                fs.imports.append(node.module)
        elif isinstance(node, ast.ClassDef):
            fs.classes.append(node.name)
            ds = ast.get_docstring(node)
            if ds:
                fs.docstrings.append(ds[:200])
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            fs.functions.append(node.name)
            ds = ast.get_docstring(node)
            if ds:
                fs.docstrings.append(ds[:200])

    # Module-level docstring
    ds = ast.get_docstring(tree)
    if ds:
        fs.docstrings.insert(0, ds[:300])

    return fs


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@dataclass
class IngestionResult:
    file_path: str
    content_hash: str
    intent_primary: str
    candidate_id: str
    sensitivity: str
    cc_score: float = 0.0
    constitution_mode: str = "OBSERVE"
    domain: str = "unknown"
    is_dead_end: bool = False
    skipped: bool = False
    reason: str = ""


def ingest_file(file_path: str, use_semantic: bool = True) -> IngestionResult:
    """
    Run the full ingestion pipeline on a single Python file.

    Args:
        file_path: Absolute path to the file.
        use_semantic: If True, upsert into semantic index (requires
                      sentence-transformers + faiss).
    """
    ledger = get_ledger()
    gate = get_gate()
    rel_path = os.path.relpath(file_path, PROJECT_ROOT)

    # --- 1. Read & hash ---
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as e:
        return IngestionResult(
            file_path=rel_path, content_hash="", intent_primary="",
            candidate_id="", sensitivity="", skipped=True,
            reason=str(e),
        )

    chash = content_hash(source)

    # Log FILE_OBSERVED
    ledger.append(EpisodicEvent(
        event_type="FILE_OBSERVED",
        summary=f"Observed {rel_path}",
        content_hash=chash,
        metadata={"file": rel_path, "size": len(source)},
    ))

    # --- 2. Extract structure ---
    fs = extract_structure(file_path)
    if fs:
        ledger.append(EpisodicEvent(
            event_type="STRUCTURE_EXTRACTED",
            summary=fs.summary_text(),
            content_hash=chash,
            metadata={
                "file": rel_path,
                "classes": fs.classes,
                "functions": fs.functions,
                "imports": fs.imports[:15],
                "line_count": fs.line_count,
            },
        ))

    # --- 3. Scrub ---
    sr = scrub(source)
    clean_text = sr.text

    # --- 4. Infer intent ---
    intent = infer_intent(clean_text, file_path=rel_path)

    ledger.append(EpisodicEvent(
        event_type="INTENT_INFERRED",
        summary=f"{rel_path}: {intent.intent_primary} (conf={intent.confidence:.2f}, impact={intent.impact})",
        content_hash=chash,
        intent_fingerprint=intent.fingerprint,
        metadata=intent.to_dict(),
    ))

    # --- 4b. Constitution enforcement ---
    verdict = {"allowed": True, "mode": intent.mode, "cc_score": 0.0,
               "is_dead_end": False, "domain": intent.domain}
    try:
        from core.learn.constitution import get_enforcer
        enforcer = get_enforcer()
        verdict = enforcer.enforce(
            fingerprint=intent.fingerprint,
            sensitivity=intent.sensitivity,
            impact=intent.impact,
            intent=intent.intent_primary,
            domain=intent.domain,
            observation_score=intent.confidence,
        )
        ledger.append(EpisodicEvent(
            event_type="CONSTITUTION_ENFORCED",
            summary=f"{rel_path}: mode={verdict['mode']} cc={verdict['cc_score']:.2f}",
            content_hash=chash,
            intent_fingerprint=intent.fingerprint,
            metadata=verdict,
        ))
        if verdict.get("is_dead_end"):
            ledger.append(EpisodicEvent(
                event_type="DEAD_END_DETECTED",
                summary=f"Dead-end pattern: {intent.fingerprint} in {rel_path}",
                content_hash=chash,
                intent_fingerprint=intent.fingerprint,
                metadata={"domain": intent.domain},
            ))
    except ImportError:
        pass

    # --- 4c. Gravity registration ---
    try:
        from core.learn.gravity_engine import get_gravity_index
        from core.learn.resonance import compute_resonance, should_speak
        grav_idx = get_gravity_index()
        g_rec, g_promotion = grav_idx.record_event(
            fingerprint=intent.fingerprint,
            cc_score=verdict.get("cc_score", 0.0),
            impact=intent.impact,
            domain=verdict.get("domain", "unknown"),
            intent=intent.intent_primary,
            outcome="observed",
            summary=f"{rel_path}: {intent.intent_primary}",
        )
        ledger.append(EpisodicEvent(
            event_type="GRAVITY_RECORDED",
            summary=f"{rel_path}: tier={g_rec.tier} hits={g_rec.hits}",
            content_hash=chash,
            intent_fingerprint=intent.fingerprint,
            metadata={"tier": g_rec.tier, "hits": g_rec.hits,
                      "resonance": compute_resonance(g_rec),
                      "should_speak": should_speak(g_rec)},
        ))
        if g_promotion:
            ledger.append(EpisodicEvent(
                event_type="GRAVITY_PROMOTED",
                summary=f"Déjà Vu: {intent.fingerprint} → {g_promotion}",
                content_hash=chash,
                intent_fingerprint=intent.fingerprint,
                metadata={"new_tier": g_promotion, "hits": g_rec.hits},
            ))
    except ImportError:
        pass

    # --- 5. Create pattern candidate ---
    summary_for_candidate = (
        f"{rel_path}: {intent.intent_primary}"
        + (f" / {intent.intent_secondary}" if intent.intent_secondary else "")
        + f" | {fs.summary_text()}" if fs else ""
    )
    candidate = PatternCandidate(
        summary=summary_for_candidate[:300],
        intent=intent.intent_primary,
        file_path=rel_path,
        symbols=fs.symbols if fs else [],
        confidence=intent.confidence,
        impact=intent.impact,
        content_hash=chash,
        cc_score=verdict.get("cc_score", 0.0),
        domain=verdict.get("domain", "unknown"),
        constitution_mode=verdict.get("mode", "OBSERVE"),
        metadata={"sensitivity": intent.sensitivity, "mode": verdict.get("mode", intent.mode)},
    )
    cid = gate.add(candidate)

    ledger.append(EpisodicEvent(
        event_type="PATTERN_CANDIDATE_CREATED",
        summary=f"Candidate {cid} for {rel_path} [cc={verdict.get('cc_score', 0.0):.2f}]",
        content_hash=chash,
        intent_fingerprint=intent.fingerprint,
        metadata={"candidate_id": cid, "intent": intent.intent_primary,
                  "cc_score": verdict.get("cc_score", 0.0),
                  "constitution_mode": verdict.get("mode", "OBSERVE")},
    ))

    # --- 6. Upsert semantic index (optional) ---
    if use_semantic:
        try:
            from core.learn.semantic import get_semantic_index, SemanticRecord
            idx = get_semantic_index()
            # Build a text representation for embedding
            embed_text = f"{rel_path} {fs.summary_text()}" if fs else rel_path
            if fs and fs.docstrings:
                embed_text += " " + " ".join(fs.docstrings[:3])

            rec = SemanticRecord(
                id=chash,
                summary=summary_for_candidate[:200],
                file_path=rel_path,
                symbols=fs.symbols if fs else [],
                intent=intent.intent_primary,
                content_hash=chash,
            )
            idx.upsert(rec, embed_text)

            ledger.append(EpisodicEvent(
                event_type="SEMANTIC_INDEX_UPSERTED",
                summary=f"Indexed {rel_path}",
                content_hash=chash,
                metadata={"file": rel_path},
            ))
        except ImportError:
            pass  # sentence-transformers / faiss not installed

    return IngestionResult(
        file_path=rel_path,
        content_hash=chash,
        intent_primary=intent.intent_primary,
        candidate_id=cid,
        sensitivity=intent.sensitivity,
        cc_score=verdict.get("cc_score", 0.0),
        constitution_mode=verdict.get("mode", "OBSERVE"),
        domain=verdict.get("domain", "unknown"),
        is_dead_end=verdict.get("is_dead_end", False),
    )


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

def full_scan(use_semantic: bool = True) -> Dict[str, Any]:
    """
    Walk all watched directories and ingest every ``.py`` file.

    Returns a summary dict.
    """
    ledger = get_ledger()
    results: List[IngestionResult] = []
    t0 = time.time()

    dirs_to_watch = []
    for d in WATCHED_DIRS:
        abs_d = os.path.join(PROJECT_ROOT, d)
        if os.path.isdir(abs_d):
            dirs_to_watch.append(abs_d)
    # Also watch /code if it exists
    code_dir = os.path.join(PROJECT_ROOT, "code")
    if os.path.isdir(code_dir):
        dirs_to_watch.append(code_dir)

    for watch_dir in dirs_to_watch:
        for root, _dirs, files in os.walk(watch_dir):
            # Skip __pycache__
            _dirs[:] = [d for d in _dirs if d != "__pycache__"]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                result = ingest_file(fpath, use_semantic=use_semantic)
                results.append(result)

    elapsed = time.time() - t0

    summary = {
        "files_scanned": len(results),
        "files_skipped": sum(1 for r in results if r.skipped),
        "intents": {},
        "elapsed_seconds": round(elapsed, 2),
    }
    for r in results:
        if not r.skipped:
            summary["intents"][r.intent_primary] = summary["intents"].get(r.intent_primary, 0) + 1

    # Log summary
    ledger.append(EpisodicEvent(
        event_type="INGESTION_SUMMARY",
        summary=f"Full scan: {summary['files_scanned']} files in {elapsed:.1f}s",
        metadata=summary,
    ))

    return summary
