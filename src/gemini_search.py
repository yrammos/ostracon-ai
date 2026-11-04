#!/usr/bin/env python3
"""Ask Gemini a question grounded in Google Search: an AI-Overview-like answer
with the pages it cites.

The answer guides; it settles nothing. Its cited pages are the reading list:
src/attribute_search.py settles an attribute only by a quote from a page
actually read with read_page. Cited links are Google redirects
(vertexaisearch.cloud.google.com/grounding-api-redirect/...), which read_page
follows to the page itself.

Grounding needs a billed Google AI Studio project; on the free tier the API
answers HTTP 429 (checked 2026-11-04). Google bills each search query the
model runs, after 5,000 free a month shared across Gemini 3 models.
"""
import requests

MODEL = "gemini-3.8-flash"
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

TOOL = {
    "name": "ask_google",
    "description": (
        "Ask Google's Gemini, grounded in Google Search. Returns a short answer, like "
        "Google's AI Overview, and the pages it cites. The answer tells you what is "
        "probably true and where to look; it settles nothing and is sometimes slightly "
        "wrong. To settle an attribute, open a cited page with read_page and quote it. "
        "Short queries naming the work work well, e.g. '\"Music at the Limits\" AND De Groot'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


def ask_google(query, api_key, model=MODEL, timeout=90):
    """{'answer', 'sources': [{'title', 'url'}], 'queries'}, or {'error'}."""
    if not api_key:
        return {"error": "no Gemini API key configured"}
    body = {"contents": [{"parts": [{"text": query}]}], "tools": [{"google_search": {}}]}
    try:
        resp = requests.post(URL.format(model=model), headers={"x-goog-api-key": api_key},
                             json=body, timeout=timeout)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        return {"error": type(e).__name__}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {str((data.get('error') or {}).get('message', ''))[:120]}"}
    cand = (data.get("candidates") or [{}])[0]
    answer = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    meta = cand.get("groundingMetadata") or {}
    sources = [{"title": c["web"].get("title", ""), "url": c["web"].get("uri", "")}
               for c in meta.get("groundingChunks", []) if c.get("web")]
    return {"answer": answer[:4000], "sources": sources, "queries": meta.get("webSearchQueries", [])}



TYPE_PROMPT = """Above is what a Google-grounded search found for: {query}

Decide the entry type of that work, choosing from the list below, where each line
defines the type as this library uses it. The type is the work's: a draft or
manuscript of a work published since takes the published work's type. If the
search did not identify the work, say so rather than guess.

{types}
Reply with exactly this and nothing else:
<result>{{"type": "one type from the list, or null", "found": true or false,
"reason": "one sentence naming what was found and where"}}</result>"""


def type_query(title, author, container=""):
    """A verb-free query in the maintainer's form: '"<title>" AND <surname>', with
    the container's title quoted too when there is one (a chapter called
    "Introduction" is found by its book)."""
    surname = (author or "").split(" and ")[0].split(",")[0].strip()
    parts = [f'"{t.strip()}"' for t in (title, container) if t and t.strip()]
    if surname:
        parts.append(surname)
    return " AND ".join(parts)


def _post(body, api_key, model, timeout, retries, sleep):
    """(status, data) for one generateContent call, retrying Google's transient
    'high demand' (503) and timeouts."""
    for attempt in range(retries):
        try:
            resp = requests.post(URL.format(model=model), headers={"x-goog-api-key": api_key},
                                 json=body, timeout=timeout)
            data = resp.json()
        except requests.Timeout:
            if attempt + 1 < retries:
                continue
            return None, {"error": {"message": "Timeout"}}
        except (requests.RequestException, ValueError) as e:
            return None, {"error": {"message": type(e).__name__}}
        if resp.status_code == 503 and attempt + 1 < retries:
            sleep(10 * (attempt + 1))
            continue
        return resp.status_code, data
    return None, {"error": {"message": "retries exhausted"}}


def _text(data):
    cand = (data.get("candidates") or [{}])[0]
    return "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])), cand


def decide_type(query, types, api_key, model=MODEL, timeout=120, retries=3, sleep=None):
    """Gemini's type for the work the query names: {'type', 'found', 'reason',
    'answer', 'sources', 'queries', 'usage'} or one with 'error'.

    Two calls. The first sends the bare query with Google Search grounding, as
    the maintainer queries Google; asked a longer question, Gemini answered
    from memory without searching (2026-11-04). The second, without tools,
    chooses the type from what the search found."""
    import json
    import re
    import time
    sleep = sleep or time.sleep
    if not api_key:
        return {"error": "no Gemini API key configured"}
    status, data = _post({"contents": [{"parts": [{"text": query}]}], "tools": [{"google_search": {}}]},
                         api_key, model, timeout, retries, sleep)
    if status != 200:
        return {"error": f"HTTP {status}: {str((data.get('error') or {}).get('message', ''))[:120]}"}
    answer, cand = _text(data)
    meta = cand.get("groundingMetadata") or {}
    out = {"answer": answer[:4000],
           "sources": [{"title": c["web"].get("title", ""), "url": c["web"].get("uri", "")}
                       for c in meta.get("groundingChunks", []) if c.get("web")],
           "queries": meta.get("webSearchQueries", []), "usage": [data.get("usageMetadata", {})]}
    status, data = _post({"contents": [
        {"role": "user", "parts": [{"text": query}]},
        {"role": "model", "parts": [{"text": answer}]},
        {"role": "user", "parts": [{"text": TYPE_PROMPT.format(query=query, types=types)}]}]},
        api_key, model, timeout, retries, sleep)
    if status != 200:
        return {**out, "error": f"HTTP {status}: {str((data.get('error') or {}).get('message', ''))[:120]}"}
    out["usage"].append(data.get("usageMetadata", {}))
    text, _ = _text(data)
    m = re.search(r"<result>\s*(\{.*?\})\s*</result>", text, re.S)
    try:
        result = json.loads(m.group(1)) if m else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        return {**out, "error": "unparsable answer", "text": text[:500]}
    t = result.get("type")
    out.update(type=(str(t).lower().lstrip("@").strip() if t else None),
               found=bool(result.get("found")), reason=result.get("reason", ""))
    return out
