"""
Tests for the semantic intent classifier (vectrax.resolver.classify).

Validates that the classifier routes based on meaning, not just punctuation.
"""
import sys
from pathlib import Path

# Ensure project root is importable
_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

from vectrax.resolver import classify

# ---- Test cases: (input, expected_mode) ----

CASES = [
    # === ONLINE — questions with punctuation ===
    ("¿Cómo funciona la gravedad?",        "online"),
    ("What is NAD+?",                       "online"),
    ("Who invented the telephone?",         "online"),

    # === ONLINE — questions WITHOUT punctuation (semantic intent) ===
    ("como funciona la gravedad",           "online"),
    ("explica cómo funciona la gravedad",   "online"),
    ("dime qué es NAD+",                    "online"),
    ("analiza las causas del cambio climático", "online"),
    ("compara Python y JavaScript",         "online"),
    ("resume la teoría de la relatividad",  "online"),
    ("define entropía",                     "online"),
    ("describe el sistema solar",           "online"),

    # === ONLINE — mid-sentence intent markers ===
    ("por favor explica la fotosíntesis",   "online"),
    ("necesito que me expliques qué es TCP","online"),

    # === ONLINE — unaccented Spanish interrogatives at start ===
    ("cuando empieza el verano",            "online"),
    ("donde queda Machu Picchu",            "online"),
    ("cual es la capital de Francia",       "online"),
    ("por que llueve",                      "online"),
    ("cuanto pesa el sol",                  "online"),

    # === ONLINE — unaccented 'quien' ===
    ("quien inventó Tesla",                 "online"),

    # === ONLINE — English commands without ? ===
    ("explain how gravity works",           "online"),
    ("tell me about black holes",           "online"),
    ("describe the water cycle",            "online"),
    ("define entropy",                      "online"),

    # === MEMORY — personal notes/statements ===
    ("hoy fui al patio con Joy",            "memory"),
    ("me siento bien hoy",                  "memory"),
    ("compré leche en el supermercado",     "memory"),
    ("I went for a walk this morning",      "memory"),
    ("my dog is named Max",                 "memory"),
    ("hoy fue un buen día",                 "memory"),

    # === LOCAL — self-memory references ===
    ("recuerdas lo que dije sobre Joy",     "local"),
    ("qué dije ayer",                       "local"),
    ("what did I say about the meeting",    "local"),
    ("mi historial de mensajes",            "local"),
    ("do you remember my notes",            "local"),
]


def test_classifier():
    passed = 0
    failed = 0
    results = []

    for text, expected in CASES:
        actual = classify(text)
        ok = actual == expected
        status = "✓" if ok else "✗"
        results.append((status, text, expected, actual))
        if ok:
            passed += 1
        else:
            failed += 1

    # Print results
    print(f"\n{'='*70}")
    print(f"  CLASSIFIER INTENT VALIDATION — {passed} passed, {failed} failed")
    print(f"{'='*70}\n")

    for status, text, expected, actual in results:
        line = f"  {status}  [{expected:>7}]  {text}"
        if status == "✗":
            line += f"  (GOT: {actual})"
        print(line)

    print(f"\n{'='*70}")
    if failed == 0:
        print("  ALL TESTS PASSED")
    else:
        print(f"  {failed} TEST(S) FAILED")
    print(f"{'='*70}\n")

    return failed == 0


if __name__ == "__main__":
    success = test_classifier()
    sys.exit(0 if success else 1)
