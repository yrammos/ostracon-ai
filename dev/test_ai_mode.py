#!/usr/bin/env python3
"""Self-tests for src/ai_mode.py and type_search: "ai-mode" in
extract_bibtex(): the prompt from the identity, the entry read from page
text, its type deciding the extraction's, and its fields filling gaps only.

Stubbed: Chrome (ai_mode.ask, with constructed page text in the shape seen in
PR #111's run, not a recorded page) and the Anthropic client (scripted replies).
"""
from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ai_mode  # noqa: E402
import biblio_agent  # noqa: E402
import extract_pages  # noqa: E402
import gemini_search  # noqa: E402

ANSWER = """@incollection{degroot2010music,
  author    = {De Groot, Rokus},
  title     = {Music at the Limits},
  booktitle = {How the West Was Won},
  editor    = {Kennaway, James},
  publisher = {Brill},
  address   = {Leiden},
  year      = {2010},
  pages     = {101--120},
  url       = {https://brill.com/x},
}"""
PAGE = "AI Mode\nThe optimal type is @incollection.\n" + ANSWER + "\nSources: brill.com"


def test_first_entry_and_query_parts():
    assert ai_mode.first_entry(PAGE) == ANSWER
    assert ai_mode.first_entry("mentions @incollection only") is None
    assert ai_mode.query_parts({"author": "Juslin, Patrik~N. and Sloboda, John", "title": "T",
                                "subtitle": "S"}) == ("Patrik N. Juslin et al.", "T: S")
    assert ai_mode.query_parts({"author": "Fischer, Kurt", "title": "Grove Music Online",
                                "lista": "Kurth, Ernst"}) == ("Kurt Fischer", "Kurth, Ernst")


def test_decide_type_reports_failures_as_errors():
    r = ai_mode.decide_type({"author": "A, B", "title": "T"}, asker=lambda a, t, log=None: ("ok", ANSWER, PAGE))
    assert r["type"] == "incollection" and r["found"] and r["entry"] == ANSWER, r
    for status in ("blocked", "refused", "error"):
        r = ai_mode.decide_type({"title": "T"}, asker=lambda a, t, log=None, s=status: (s, None, "x"))
        assert r.get("error", "").startswith(f"AI Mode {status}"), r
    r = ai_mode.decide_type({"title": "T"}, asker=lambda a, t, log=None: ("no entry", None, "x"))
    assert r["found"] is False and not r.get("error"), r
    assert ai_mode.decide_type({"author": "A"}).get("error") == "no title"


def test_answers_are_cleaned_and_limited_to_the_type():
    qs, ans = ai_mode.answers(ANSWER, "incollection",
                              {"author", "title", "booktitle", "editor", "publisher", "location", "date", "pages"})
    got = {q["use"].split()[1]: ans[q["id"]] for q in qs}
    assert got["location"] == "Leiden" and got["date"] == "2010" and got["pages"] == "101-120", got
    assert "url" not in got and "address" not in got and "year" not in got, got
    assert all(q["auto"] for q in qs)


DRAFT = ("@incollection{X,\n  Author = {De Groot, Rokus},\n  Title = {Music at the Limits},\n"
         "  Booktitle = {How the West Was Won},\n  Publisher = {Brill Academic},\n}")
IDENT = ('{"title": "Music at the Limits", "subtitle": null, "authors": ["De Groot, Rokus"], '
         '"editors": [], "container": "How the West Was Won", "entry_heading": null, '
         '"supplement": null, "reviewed_work": null, "year": "2010", "kind": "a chapter"}')


def _reply(text):
    return type("M", (), {"content": [type("B", (), {"type": "text", "text": text})()],
                          "stop_reason": "end_turn", "usage": None})()


def _run(answer, draft=DRAFT):
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config.update(enrich_missing_fields=False, validate_entries=False, gemini_type_check=True,
                        gemini_api_key=None, require_langid=False, library_match=False,
                        house_cleanup=False, render_check=False, type_search="ai-mode")
    replies = [IDENT, draft]
    agent.client = type("C", (), {"messages": type("Ms", (), {
        "create": lambda self, **k: _reply(replies.pop(0))})()})()
    biblio_agent.EXTRACTORS[".fake"] = lambda path, **kw: extract_pages.SourceContent(
        text="Rokus de Groot, Music at the Limits", label="PDF")
    ask = mock.Mock(return_value=answer)
    try:
        with tempfile.TemporaryDirectory() as td, redirect_stderr(io.StringIO()), \
                mock.patch.object(ai_mode, "ask", ask), \
                mock.patch.object(gemini_search.requests, "post") as post:
            src = Path(td) / "x.fake"
            src.write_text("x")
            result = agent.extract_bibtex(src)
    finally:
        del biblio_agent.EXTRACTORS[".fake"]
    post.assert_not_called()
    return result, ask, agent


def test_ai_mode_decides_the_type_and_fills_gaps_only():
    result, ask, agent = _run(("ok", ANSWER, PAGE))
    assert ask.call_args.args[:2] == ("Rokus De Groot", "Music at the Limits"), ask.call_args
    assert [u["call"] for u in agent.usage] == ["identity", "extraction"], agent.usage
    g = result.gemini
    assert g["engine"] == "ai-mode" and g["status"] == "decided" and g["type"] == "incollection", g
    e = result.entry
    assert "Location = {Leiden}" in e and "Editor = {Kennaway, James}" in e, e
    assert "Publisher = {Brill Academic}" in e and "Publisher = {Brill}" not in e, e   # the source's value stands
    assert "AI Mode: " in (result.field_sources or ""), result.field_sources
    assert "url" not in e.lower(), e


def test_a_failed_query_leaves_the_type_to_the_extraction():
    result, _, agent = _run(("blocked", None, "unusual traffic"))
    assert result.gemini["status"].startswith("error: AI Mode blocked"), result.gemini
    assert "Location" not in result.entry and [u["call"] for u in agent.usage] == ["identity", "extraction"]


def test_a_type_not_followed_takes_no_fields():
    book = "@book{X,\n  Author = {De Groot, Rokus},\n  Title = {Music at the Limits},\n}"
    result, _, _ = _run(("ok", ANSWER, PAGE), draft=book)
    assert result.gemini["questions_status"] == "skipped: type not followed", result.gemini
    assert "Leiden" not in result.entry, result.entry


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else
          f"All {len(tests)} AI Mode self-tests passed.")
    sys.exit(1 if failed else 0)
