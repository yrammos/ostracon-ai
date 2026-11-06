#!/usr/bin/env python3
"""The type search and its questions (extract_bibtex steps 2 and 7) through
Claude's web search tool, with a hard limit on searches per item.

Three calls, one conversation:

1. search(): the bare query, with the web_search tool capped at MAX_USES.
   The API enforces the cap (a search past it returns max_uses_exceeded and
   is not billed) and reports the searches run in
   usage.server_tool_use.web_search_requests, which the agent records.
2. decide_type(): the type, chosen from what the search found, without tools.
3. ask_questions(): the question bank, answered from the same search results,
   without tools. The results stay in the conversation (the API restores them
   from their encrypted_content), so the answers draw on the pages themselves.

`create` is BiblioAgent._create: every call is recorded in agent.usage,
searches included, and priced by dev/estimate_cost.usage_cost().
The return values have the shape of gemini_search's, so the agent handles
both alike.
"""
from __future__ import annotations

import json
import re

import gemini_search

MODEL = "claude-haiku-5-5"
# Searches per item. Each costs $0.01 (platform.claude.com, web search tool,
# read 2026-11-06), plus its results as input tokens.
MAX_USES = 2
# The basic tool: the dynamically filtered versions are not listed for Haiku
# 5.5, and the basic one keeps the request the same whatever the model.
TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_USES}
MAX_TOKENS = 8000

SEARCH_PROMPT = """Search the web for the work this query names: {query}

Then report, from the pages you found, what they say about it: what kind of
publication it is (book, chapter in an edited collection, journal article,
entry in a reference work, and so on), where it appeared, its editors,
publisher, place, year, volume and pages. Report only what the pages show."""

TYPE_PROMPT = gemini_search.TYPE_PROMPT.replace(
    "Above is what a Google-grounded search found for: {query}",
    "Above is what a web search found for: {query}")

QUESTIONS_PROMPT = """Answer these questions about the work above, using only the
search results above. Answer each briefly: a number as the number only; a
choice as one of the options given, word for word. A line naming a BibLaTeX
field asks for that field's value for this work: give it only where those
results show it, written as BibLaTeX expects (names as "Family, Given",
several joined by " and "; a date as YYYY, YYYY-MM or YYYY-MM-DD; a page range
as first-last; a title without its subtitle, which has its own field). If the
results do not show the answer, give null; never guess, and never answer from
memory.

{questions}

Reply with exactly this and nothing else:
<result>{{{skeleton}}}</result>"""


def _blocks(message):
    """The reply's content blocks as request-ready dicts, unchanged (search
    results must go back with their encrypted_content)."""
    return [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in message.content]


def _text(message):
    return "".join(getattr(b, "text", "") or "" for b in message.content
                   if getattr(b, "type", "") == "text")


def _searches(message):
    stu = getattr(getattr(message, "usage", None), "server_tool_use", None)
    return getattr(stu, "web_search_requests", 0) or 0


def search(query, create, model=MODEL, max_pauses=2):
    """{'answer', 'sources', 'queries', 'searches', 'history'} or one with 'error'."""
    messages = [{"role": "user", "content": SEARCH_PROMPT.format(query=query)}]
    content, searches = [], 0
    try:
        for _ in range(max_pauses + 1):
            message = create("type-search", MAX_TOKENS, model=model, tools=[TOOL],
                             messages=messages + ([{"role": "assistant", "content": content}]
                                                  if content else []))
            content += _blocks(message)
            searches += _searches(message)
            if getattr(message, "stop_reason", None) != "pause_turn":
                break
    except Exception as e:  # noqa: BLE001 - the extraction still runs
        return {"error": f"{type(e).__name__}: {str(e)[:120]}", "searches": searches}
    queries = [b.get("input", {}).get("query", "") for b in content
               if b.get("type") == "server_tool_use" and b.get("name") == "web_search"]
    sources = [{"title": r.get("title", ""), "url": r.get("url", "")}
               for b in content if b.get("type") == "web_search_tool_result"
               and isinstance(b.get("content"), list)
               for r in b["content"] if r.get("type") == "web_search_result"]
    answer = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    return {"answer": answer[:4000], "sources": sources, "queries": queries,
            "searches": searches, "history": messages + [{"role": "assistant", "content": content}]}


def decide_type(query, types, create, model=MODEL):
    """Search, then choose the type from what was found: {'type', 'found',
    'reason', 'answer', 'sources', 'queries', 'searches', 'history'} or one
    with 'error'. A reply that ran no search is 'not found': the type must
    come from pages, not from memory."""
    out = search(query, create, model=model)
    if out.get("error"):
        return out
    if not out["searches"]:
        return {**out, "found": False, "type": None, "reason": "no search was run"}
    try:
        message = create("type-choice", MAX_TOKENS, model=model, messages=out["history"] + [
            {"role": "user", "content": TYPE_PROMPT.format(query=query, types=types)}])
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    m = re.search(r"<result>\s*(\{.*?\})\s*</result>", _text(message), re.S)
    try:
        result = json.loads(m.group(1)) if m else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        return {**out, "error": "unparsable answer"}
    t = result.get("type")
    return {**out, "type": str(t).lower().lstrip("@").strip() if t else None,
            "found": bool(result.get("found")), "reason": result.get("reason", "")}


def ask_questions(history, questions, create, model=MODEL):
    """The question bank, answered from the search results in `history`, with
    no tool: {'answers': {id: value}} or one with 'error'."""
    if not questions:
        return {"answers": {}}
    lines = []
    for q in questions:
        opts = sorted(set(q.get("expect", {}).values())) if q["use"] == "check" else []
        lines.append(f"- {q['id']}: {q['ask']}" + (f" Options: {'; '.join(opts)}." if opts else ""))
    skeleton = ", ".join(f'"{q["id"]}": ...' for q in questions)
    try:
        message = create("questions", MAX_TOKENS, model=model, messages=history + [
            {"role": "user", "content": QUESTIONS_PROMPT.format(
                questions="\n".join(lines), skeleton=skeleton)}])
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    m = re.search(r"<result>\s*(\{.*?\})\s*</result>", _text(message), re.S)
    try:
        answers = json.loads(m.group(1)) if m else None
    except ValueError:
        answers = None
    if not isinstance(answers, dict):
        return {"error": "unparsable answer"}
    wanted = {q["id"] for q in questions}
    return {"answers": {k: v for k, v in answers.items() if k in wanted}}
