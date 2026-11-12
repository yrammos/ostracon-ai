#!/usr/bin/env python3
"""Self-tests for src/claude_search.py and its use in extract_bibtex(): the
type search through Claude's web search tool, capped per item.

Stubbed: the Anthropic client. Its replies are constructed in the shape the
web search tool's documentation shows (server_tool_use, web_search_tool_result,
usage.server_tool_use.web_search_requests), not recorded from the API, so
these tests check our handling of that shape, not the API's behaviour.
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
sys.path.insert(0, str(ROOT / "dev"))

import biblio_agent  # noqa: E402
import claude_search  # noqa: E402
import estimate_cost  # noqa: E402
import extract_pages  # noqa: E402
import gemini_search  # noqa: E402


class Block(dict):
    """A content block: attribute access like the SDK's, to_dict() like it too."""
    __getattr__ = dict.get

    def to_dict(self):
        return dict(self)


def _usage(searches=0):
    stu = type("S", (), {"web_search_requests": searches})()
    return type("U", (), {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0, "server_tool_use": stu})()


def _msg(blocks, searches=0, stop="end_turn"):
    return type("M", (), {"content": [Block(b) for b in blocks], "usage": _usage(searches),
                          "stop_reason": stop})()


SEARCHED = _msg([
    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
     "input": {"query": "Music at the Limits De Groot"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
     "content": [{"type": "web_search_result", "url": "https://brill.com/x", "title": "How the West Was Won",
                  "encrypted_content": "ENC"}]},
    {"type": "text", "text": "A chapter in the edited volume How the West Was Won (Brill, 2010)."}],
    searches=1)
CHOSEN = _msg([{"type": "text", "text": '<result>{"type": "@InCollection", "found": true, '
                                        '"reason": "Brill edited volume"}</result>'}])


class Create:
    """BiblioAgent._create's signature, scripted."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, call, max_tokens, **kwargs):
        self.calls.append((call, kwargs))
        return self.replies.pop(0)


def test_type_search_caps_searches_and_chooses_without_tools():
    create = Create([SEARCHED, CHOSEN])
    r = claude_search.decide_type('"Music at the Limits" AND De Groot', "types", create)
    assert r["type"] == "incollection" and r["found"] and r["searches"] == 1, r
    (c1, k1), (c2, k2) = create.calls
    assert c1 == "type-search" and k1["tools"] == [claude_search.TOOL], k1
    assert claude_search.TOOL["max_uses"] == claude_search.MAX_USES == 2
    assert k1["model"] == "claude-haiku-5-5"
    assert c2 == "type-choice" and "tools" not in k2, k2
    # The search results go back unchanged, encrypted_content included.
    assert any(b.get("content", [{}])[0].get("encrypted_content") == "ENC"
               for b in k2["messages"][1]["content"] if b["type"] == "web_search_tool_result")
    assert r["sources"] == [{"title": "How the West Was Won", "url": "https://brill.com/x"}]
    assert r["queries"] == ["Music at the Limits De Groot"]


def test_an_answer_without_a_search_is_not_found():
    create = Create([_msg([{"type": "text", "text": "From memory: a chapter."}], searches=0)])
    r = claude_search.decide_type("q", "types", create)
    assert r["found"] is False and r["type"] is None and len(create.calls) == 1, r


def test_a_paused_search_is_resumed_and_its_searches_added():
    first = _msg([{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "a"}}],
                 searches=1, stop="pause_turn")
    create = Create([first, SEARCHED, CHOSEN])
    r = claude_search.decide_type("q", "types", create)
    assert r["searches"] == 2 and r["queries"] == ["a", "Music at the Limits De Groot"], r
    assert create.calls[1][1]["messages"][-1]["role"] == "assistant"


def test_questions_are_answered_without_tools():
    create = Create([_msg([{"type": "text", "text": '<result>{"pub": "Brill", "x": 1}</result>'}])])
    q = [{"id": "pub", "when": "any", "ask": "Publisher?", "use": "field publisher"}]
    out = claude_search.ask_questions([{"role": "user", "content": "q"}], q, create)
    assert out == {"answers": {"pub": "Brill"}}, out
    prompt = " ".join(create.calls[0][1]["messages"][-1]["content"].split())
    assert "tools" not in create.calls[0][1] and "never answer from memory" in prompt


def test_the_search_history_is_cached_for_both_follow_up_calls():
    create = Create([SEARCHED, CHOSEN])
    r = claude_search.decide_type("q", "types", create)
    marked = create.calls[1][1]["messages"][1]["content"]
    assert marked[-1]["cache_control"] == {"type": "ephemeral"}, marked[-1]
    assert sum("cache_control" in b for b in marked) == 1
    # The returned history is untouched, so questions sets its own breakpoint.
    assert not any("cache_control" in b for m in r["history"] if isinstance(m["content"], list)
                   for b in m["content"])
    create = Create([_msg([{"type": "text", "text": '<result>{"pub": null}</result>'}])])
    q = [{"id": "pub", "when": "any", "ask": "Publisher?", "use": "field publisher"}]
    claude_search.ask_questions(r["history"], q, create)
    sent = create.calls[0][1]["messages"]
    assert sent[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(sent[-1]["content"], str) and len(sent) == len(r["history"]) + 1


def test_searches_are_recorded_and_priced():
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.client = type("C", (), {"messages": type("Ms", (), {"create": lambda self, **k: SEARCHED})()})()
    agent.usage = []
    agent._create("type-search", 100, model="claude-haiku-5-5", messages=[])
    assert agent.usage[-1]["web_searches"] == 1, agent.usage
    rec = dict(agent.usage[-1], input=0, output=0)
    assert abs(estimate_cost.usage_cost([rec]) - 0.01) < 1e-9


DRAFT = "@article{X,\n  Author = {De Groot, Rokus},\n  Title = {Music at the Limits},\n  Journaltitle = {How the West Was Won},\n}"
IDENT = ('{"title": "Music at the Limits", "subtitle": null, "authors": ["De Groot, Rokus"], '
         '"editors": [], "container": "How the West Was Won", "entry_heading": null, '
         '"supplement": null, "reviewed_work": null, "year": "2010", "kind": "a chapter"}')
REDONE = ("@incollection{X,\n  Author = {De Groot, Rokus},\n  Title = {Music at the Limits},\n"
          "  Booktitle = {How the West Was Won},\n}")


def test_the_pipeline_uses_claude_by_default_and_never_calls_gemini():
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config.update(enrich_missing_fields=False, validate_entries=False, gemini_type_check=True,
                        gemini_api_key=None, require_langid=False, library_match=False,
                        house_cleanup=False, render_check=False)
    agent.config.pop("type_search", None)
    replies = [_msg([{"type": "text", "text": IDENT}]), SEARCHED, CHOSEN,
               _msg([{"type": "text", "text": REDONE}]),
               _msg([{"type": "text", "text": "<result>{}</result>"}])]
    agent.client = type("C", (), {"messages": type("Ms", (), {
        "create": lambda self, **k: replies.pop(0)})()})()
    biblio_agent.EXTRACTORS[".fake"] = lambda path, **kw: extract_pages.SourceContent(
        text="Rokus de Groot, Music at the Limits", label="PDF")
    try:
        with tempfile.TemporaryDirectory() as td, redirect_stderr(io.StringIO()), \
                mock.patch.object(gemini_search.requests, "post") as post:
            src = Path(td) / "x.fake"
            src.write_text("x")
            result = agent.extract_bibtex(src)
    finally:
        del biblio_agent.EXTRACTORS[".fake"]
    post.assert_not_called()
    calls = [u["call"] for u in agent.usage]
    assert calls[:3] == ["identity", "type-search", "type-choice"], calls
    assert result.gemini["status"] == "decided" and result.gemini["engine"] == "claude", result.gemini
    assert "@incollection" in result.entry, result.entry
    assert sum(u["web_searches"] for u in agent.usage) == 1


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
          f"All {len(tests)} Claude-search self-tests passed.")
    sys.exit(1 if failed else 0)
