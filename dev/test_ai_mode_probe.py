#!/usr/bin/env python3
"""Self-tests for dev/ai_mode_probe.py: the prompt's author and title read
from an expected entry, and the cleanup applied before scoring. Constructed
entries; Chrome is not touched (src/ai_mode.py's own tests cover the page)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))
sys.path.insert(0, str(ROOT / "src"))

import ai_mode_probe as p  # noqa: E402
import bib_audit  # noqa: E402


def test_query_parts_from_an_expected_entry():
    entry = bib_audit.scan("@incollection{K,\n  author = {Juslin, Patrik~N. and Sloboda, John},\n"
                           "  title = {Communicating Emotion},\n  subtitle = {A Review},\n}\n")[0][0]
    assert p.query_parts(entry) == ("Patrik N. Juslin et al.", "Communicating Emotion: A Review")
    ref = bib_audit.scan("@inreference{G,\n  author = {Fischer, Kurt},\n  title = {Grove Music Online},\n"
                         "  lista = {Kurth, Ernst},\n}\n")[0][0]
    assert p.query_parts(ref) == ("Kurt Fischer", "Kurth, Ernst")


def test_cleanup_before_scoring():
    raw = ("@book{x,\n  author = {A, B},\n  title = {T: S},\n  address = {Oxford},\n"
           "  year = {2001},\n  url = {https://x},\n}")
    out = p.clean(raw)
    assert "location" in out.lower() and "date" in out.lower(), out
    assert "address" not in out.lower() and "url" not in out.lower(), out


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} AI Mode probe self-tests passed.")
    sys.exit(1 if failed else 0)
