#!/usr/bin/env python3
"""Self-tests for dev/ai_mode_probe.py's parsing: the entry found in page
text, and the prompt's author and title. Constructed inputs; Chrome is not
touched."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))
sys.path.insert(0, str(ROOT / "src"))

import ai_mode_probe as p  # noqa: E402
import bib_audit  # noqa: E402

PAGE = """AI Mode
The optimal type is @incollection, because ...
@incollection{juslin2001,
  author = {Juslin, Patrik N.},
  title = {Communicating Emotion in {Music} Performance},
  booktitle = {Music and Emotion},
}
Sources: ..."""


def test_first_entry_is_brace_balanced():
    e = p.first_entry(PAGE)
    assert e.startswith("@incollection{juslin2001,") and e.endswith("}") and "{Music}" in e, e
    assert p.first_entry("no entry here; mentions @incollection only") is None


def test_query_parts_from_an_expected_entry():
    entry = bib_audit.scan("@incollection{K,\n  author = {Juslin, Patrik N. and Sloboda, John},\n"
                           "  title = {Communicating Emotion},\n  subtitle = {A Review},\n}\n")[0][0]
    assert p.query_parts(entry) == ("Patrik N. Juslin et al.", "Communicating Emotion: A Review")
    ref = bib_audit.scan("@inreference{G,\n  author = {Fischer, Kurt},\n  title = {Grove Music Online},\n"
                         "  lista = {Kurth, Ernst},\n}\n")[0][0]
    assert p.query_parts(ref) == ("Kurt Fischer", "Kurth, Ernst")


def test_the_url_carries_the_prompt():
    import urllib.parse
    url = p.URL.format(q=urllib.parse.quote_plus(p.PROMPT.format(author="A B", title="T")))
    assert "udm=50" in url and "biblatex-chicago" in urllib.parse.unquote_plus(url)


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
