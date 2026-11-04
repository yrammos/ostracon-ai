#!/usr/bin/env python3
"""Self-tests for steps 2-3a in extract_bibtex(): the identity call, Gemini's
type decision, the single extraction told that type, and the question bank,
with the network stubbed.

Stubbed: the Anthropic client (scripted replies, constructed, not recorded),
gemini_search.decide_type / ask_questions (constructed results), and a `.fake`
source extractor. Not stubbed: identity parsing, query building, the
type-list check, the decided-type instruction, question selection, answer
application and the result.
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

import biblio_agent  # noqa: E402
import extract_pages  # noqa: E402
import gemini_search  # noqa: E402

DRAFT = "@article{X,\n  Author = {De Groot, Rokus},\n  Title = {Music at the Limits},\n  Journaltitle = {How the West Was Won},\n}"
REDONE = ("@incollection{X,\n  Author = {De Groot, Rokus},\n  Title = {Music at the Limits},\n"
          "  Booktitle = {How the West Was Won},\n}")


def _reply(text):
    return type("M", (), {"content": [type("B", (), {"type": "text", "text": text})()],
                          "stop_reason": "end_turn", "usage": None})()


class _Messages:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _reply(self.texts.pop(0))


def _run(replies, decided=None, answers=None, check=True, key="KEY"):
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config.update(enrich_missing_fields=False, validate_entries=False,
                        gemini_type_check=check, gemini_api_key=key)
    agent.client = type("C", (), {"messages": _Messages(replies)})()
    biblio_agent.EXTRACTORS[".fake"] = lambda path, **kw: extract_pages.SourceContent(
        text="Rokus de Groot, Music at the Limits", label="PDF")
    decide = mock.Mock(return_value=decided)
    ask = mock.Mock(return_value={"answers": answers or {}, "usage": [{"promptTokenCount": 5}]})
    try:
        with tempfile.TemporaryDirectory() as td, redirect_stderr(io.StringIO()), \
                mock.patch.object(gemini_search, "decide_type", decide), \
                mock.patch.object(gemini_search, "ask_questions", ask):
            src = Path(td) / "x.fake"
            src.write_text("x")
            result = agent.extract_bibtex(src)
    finally:
        del biblio_agent.EXTRACTORS[".fake"]
    return result, decide, ask, agent


IDENT = ('{"title": "Music at the Limits", "subtitle": null, "authors": ["De Groot, Rokus"], '
         '"editors": [], "container": "How the West Was Won", "entry_heading": null, '
         '"supplement": null, "reviewed_work": null, "year": "2010", "kind": "a chapter"}')


def test_off_by_default_makes_no_gemini_request():
    with mock.patch.object(gemini_search.requests, "post") as post:
        result, decide, _, agent = _run([DRAFT], check=None)
    post.assert_not_called()
    decide.assert_not_called()
    assert result.gemini is None and "@article" in result.entry
    assert [u["call"] for u in agent.usage] == ["extraction"], agent.usage


def test_decided_type_reaches_the_one_extraction_and_its_questions():
    decided = {"type": "incollection", "found": True, "reason": "Brill edited volume",
               "history": [], "usage": [{"promptTokenCount": 9}]}
    result, decide, ask, agent = _run([IDENT, REDONE], decided, answers={"chapter_number": "9"})
    g = result.gemini
    assert g["status"] == "decided" and g["final_type"] == "incollection", g
    assert decide.call_args.args[0] == '"Music at the Limits" AND "How the West Was Won" AND De Groot'
    assert [u["call"] for u in agent.usage] == ["identity", "extraction"], agent.usage
    assert "@incollection" in agent.client.messages.calls[1]["messages"][0]["content"][-1]["text"]
    assert "@incollection" in result.entry and "Chapter = {9}" in result.entry, result.entry
    asked = [q["id"] for q in ask.call_args.args[1]]
    assert "chapter_number" in asked, asked


def test_not_found_and_failures_let_claude_choose():
    for decided, status in (({"type": None, "found": False, "reason": "", "usage": []}, "not-found"),
                            ({"type": "letter", "found": True, "reason": "", "usage": []}, "invalid-type"),
                            ({"error": "HTTP 429: quota", "usage": []}, "error: HTTP 429: quota")):
        result, _, ask, agent = _run([IDENT, DRAFT], decided)
        assert result.gemini["status"] == status and "@article" in result.entry, (status, result.gemini)
        assert "external check" not in agent.client.messages.calls[1]["messages"][0]["content"][-1]["text"]
        ask.assert_not_called()


def test_an_extraction_that_ignores_the_type_is_logged_not_redone():
    decided = {"type": "incollection", "found": True, "reason": "r", "history": [], "usage": []}
    result, _, _, agent = _run([IDENT, DRAFT], decided)
    assert result.gemini["status"] == "not-followed" and "@article" in result.entry
    assert [u["call"] for u in agent.usage] == ["identity", "extraction"]


def test_unreadable_identity_skips_gemini():
    result, decide, _, _ = _run(["no json here", DRAFT], {})
    assert result.gemini["status"] == "skipped: no identity"
    decide.assert_not_called()


def test_a_reference_entry_is_searched_by_its_heading():
    ident = ('{"title": null, "authors": ["Lawlor, Leonard"], "container": "Stanford Encyclopedia of '
             'Philosophy", "entry_heading": "Jacques Derrida", "year": "2011"}')
    _, decide, _, _ = _run([ident, DRAFT], {"type": None, "found": False, "usage": []})
    assert decide.call_args.args[0] == '"Jacques Derrida" AND "Stanford Encyclopedia of Philosophy" AND Lawlor'


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} Gemini-step self-tests passed.")
    sys.exit(1 if failed else 0)
