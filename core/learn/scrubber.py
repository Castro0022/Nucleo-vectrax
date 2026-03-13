"""
Vectrax Scrubber — PII & Secrets Detection / Sanitization
===========================================================
Runs BEFORE any indexing or storage.  Returns sanitized text and a
sensitivity classification: CLEAN / PII / SECRETS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple


class Sensitivity(str, Enum):
    CLEAN = "CLEAN"
    PII = "PII"
    SECRETS = "SECRETS"


@dataclass
class ScrubResult:
    """Result of scrubbing a text block."""
    text: str
    sensitivity: Sensitivity
    findings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns  (order matters — SECRETS checked first)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret", re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*\S+")),
    ("generic_api_key", re.compile(r"(?i)(api[_\-]?key|apikey|api_token)\s*[=:]\s*['\"]?\S{8,}['\"]?")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
    ("private_key", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    ("password_assign", re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"]?\S{4,}['\"]?")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("slack_token", re.compile(r"xox[bpras]-[A-Za-z0-9\-]+")),
]

_PII_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("phone", re.compile(r"\b\+?[1-9]\d{1,2}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
]

# Replacement tokens
_REDACT_SECRET = "[REDACTED_SECRET]"
_REDACT_PII = "[REDACTED_PII]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrub(text: str) -> ScrubResult:
    """
    Scrub *text* removing secrets and PII.

    Returns a ScrubResult with the sanitized text, sensitivity level,
    and a list of finding labels (e.g. ``["aws_key", "email"]``).
    """
    findings: List[str] = []
    sensitivity = Sensitivity.CLEAN
    cleaned = text

    # --- Pass 1: Secrets ---
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(cleaned):
            findings.append(label)
            sensitivity = Sensitivity.SECRETS
            cleaned = pattern.sub(_REDACT_SECRET, cleaned)

    # --- Pass 2: PII ---
    for label, pattern in _PII_PATTERNS:
        if pattern.search(cleaned):
            findings.append(label)
            if sensitivity == Sensitivity.CLEAN:
                sensitivity = Sensitivity.PII
            cleaned = pattern.sub(_REDACT_PII, cleaned)

    return ScrubResult(text=cleaned, sensitivity=sensitivity, findings=findings)


def is_sensitive(text: str) -> bool:
    """Quick boolean check: does *text* contain secrets or PII?"""
    return scrub(text).sensitivity != Sensitivity.CLEAN
