#!/usr/bin/env python3
"""Self-tests for steps 3 and 3a in extract_bibtex(): Gemini's type check and
question bank, with the network stubbed.

Stubbed: the Anthropic client (scripted replies, constructed, not recorded),
gemini_search.decide_type / ask_questions (constructed results), and a `.fake`
source extractor. Not stubbed: query building, the type-list check, the
re-encode path, the question selection, answer application and the result.
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


def test_off_by_default_makes_no_gemini_request():
    with mock.patch.object(gemini_search.requests, "post") as post:
        result, decide, _, _ = _run([DRAFT], check=None)
    post.assert_not_called()
    decide.assert_not_called()
    assert result.gemini is None and "@article" in result.entry


def test_disagreement_reencodes_under_geminis_type_and_asks_its_questions():
    decided = {"type": "incollection", "found": True, "reason": "Brill edited volume",
               "history": [], "usage": [{"promptTokenCount": 9}]}
    result, decide, ask, agent = _run([DRAFT, REDONE], decided, answers={"chapter_number": "9"})
    g = result.gemini
    assert g["status"] == "overrode" and g["draft_type"] == "article" and g["final_type"] == "incollection", g
    assert "@incollection" in result.entry and "Chapter = {9}" in result.entry, result.entry
    assert g["draft_entry"].startswith("@article"), g
    assert decide.call_args.args[0] == '"Music at the Limits" AND "How the West Was Won" AND De Groot'
    asked = [q["id"] for q in ask.call_args.args[1]]
    assert "chapter_number" in asked and "other_chapters_same_author" in asked, asked
    assert [u["call"] for u in agent.usage] == ["extraction", "reencode"], agent.usage
    assert "@incollection" in agent.client.messages.calls[1]["messages"][0]["content"][-1]["text"]
    assert result.needs_color is True          # a field filled from Gemini goes to review


def test_agreement_keeps_the_draft_and_makes_no_second_claude_call():
    decided = {"type": "article", "found": True, "reason": "journal", "history": [], "usage": []}
    result, _, _, agent = _run([DRAFT], decided)
    assert result.gemini["status"] == "agreed" and [u["call"] for u in agent.usage] == ["extraction"]


def test_not_found_and_off_list_types_keep_the_draft():
    for decided, status in (({"type": None, "found": False, "reason": "", "usage": []}, "not-found"),
                            ({"type": "letter", "found": True, "reason": "", "usage": []}, "invalid-type"),
                            ({"error": "HTTP 429: quota", "usage": []}, "error: HTTP 429: quota")):
        result, _, ask, _ = _run([DRAFT], decided)
        assert result.gemini["status"] == status and "@article" in result.entry, (status, result.gemini)
        ask.assert_not_called()


def test_reencode_of_the_wrong_type_keeps_the_draft_for_review():
    decided = {"type": "incollection", "found": True, "reason": "r", "history": [], "usage": []}
    result, _, _, _ = _run([DRAFT, DRAFT], decided)
    assert result.gemini["status"] == "reencode-failed" and "@article" in result.entry
    assert result.needs_color is True


def test_no_title_skips_gemini():
    result, decide, _, _ = _run(["@article{X,\n  Author = {Yip, Joyce},\n}"], {})
    assert result.gemini["status"] == "skipped: no title"
    decide.assert_not_called()


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
