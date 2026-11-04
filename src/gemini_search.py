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
import re

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

Decide the entry type of the item the search names, choosing from the list below,
where each line defines the type as this library uses it. The item is what the
first quoted phrase names (or, for a review or a supplement, the review or
supplement itself), not the work containing it or the work it discusses. The type is the work's: a draft or
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
    return choose_type(query, answer, types, api_key, model, timeout, retries, sleep, out)


def choose_type(query, answer, types, api_key, model=MODEL, timeout=120, retries=3, sleep=None, out=None):
    """The second call of decide_type(), alone: the type, from what the search
    answered. Also reruns the choice on a saved answer without searching again."""
    import json
    import time
    sleep = sleep or time.sleep
    out = out if out is not None else {"answer": answer, "usage": []}
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
               found=bool(result.get("found")), reason=result.get("reason", ""),
               history=[{"role": "user", "parts": [{"text": query}]},
                        {"role": "model", "parts": [{"text": answer}]}])
    return out


def listed_types(types):
    """The type names in a TYPE_DEFINITIONS-style list: the first word of each
    line indented by six spaces."""
    import re
    return set(re.findall(r"^ {6}([a-z]+) ", types, re.M))


SUPP_TYPES = {"suppbook", "suppcollection", "suppperiodical"}


def query_from_fields(fields, entry_type=""):
    """(name, query) for an extracted entry: the query names the item cited,
    which is not always what Title holds.

    - Lista present (an entry in a reference work): the entry's heading, then
      the work's title.
    - A supplement: its kind (from Type, e.g. "introduction"), then the book.
    - An untitled review: "review", then the work reviewed and the journal.
    - Otherwise: the title, then the container's title.
    Then the first author's (or editor's) surname. `name` is empty when
    nothing names the item."""
    import enrich
    f = {k.lower(): v for k, v in fields.items()}
    clean = lambda k: enrich.strip_latex(f.get(k, "")).replace("~", " ").strip()
    title = ": ".join(x for x in (clean("title"), clean("subtitle")) if x)
    container = next((clean(k) for k in ("booktitle", "journaltitle", "journal", "maintitle") if clean(k)), "")
    author = clean("author") or clean("editor")
    t = (entry_type or "").lower()
    if clean("lista"):
        return clean("lista"), type_query(clean("lista"), author, title)
    if t in SUPP_TYPES:
        kind = re.sub(r"\s+(to|of)$", "", clean("type").lower()) or "introduction"
        return title, f"{kind} AND {type_query(title, author, container)}"
    if t == "review":
        return title, f"review AND {type_query(title, author, container)}"
    return title, type_query(title, author, container)


# --- Step 3a: a bank of closed questions, chosen by type --------------------

QUESTIONS_PROMPT = """Answer these questions about the work above, from Google Search.
Answer each briefly: a number as the number only; a choice as one of the
options given, word for word. A line naming a BibLaTeX field asks for that
field's value for this work: give it only where a page you found shows it,
written as BibLaTeX expects (names as "Family, Given", several joined by
" and "; a date as YYYY, YYYY-MM or YYYY-MM-DD; a page range as first-last; a
title without its subtitle, which has its own field). If you cannot find the
answer, give null; never guess.

{questions}

Reply with exactly this and nothing else:
<result>{{{skeleton}}}</result>"""


def load_bank(path):
    """The question bank: a list of {id, when, ask, use, expect?}. `when` is a
    list of types or "any"; `use` is "field <name>" or "check"."""
    import yaml
    bank = yaml.safe_load(open(path, encoding="utf-8")) or []
    for q in bank:
        assert {"id", "when", "ask", "use"} <= set(q), f"question bank entry lacks a key: {q}"
        assert q["use"] == "check" or str(q["use"]).startswith("field "), f"unknown use: {q}"
        assert q["use"] != "check" or isinstance(q.get("expect"), dict), f"a check needs expect: {q}"
    return bank


def select(bank, entry_type):
    """The questions whose `when` names the type (or is "any")."""
    return [q for q in bank if q["when"] == "any" or entry_type in q["when"]]


def ask_questions(history, questions, api_key, model=MODEL, timeout=120, retries=3, sleep=None):
    """One grounded call continuing the search conversation: {'answers':
    {id: value}, 'usage'} or one with 'error'."""
    import json
    import re
    import time
    sleep = sleep or time.sleep
    if not questions:
        return {"answers": {}, "usage": []}
    lines = []
    for q in questions:
        opts = sorted(set(q.get("expect", {}).values())) if q["use"] == "check" else []
        lines.append(f"- {q['id']}: {q['ask']}" + (f" Options: {'; '.join(opts)}." if opts else ""))
    skeleton = ", ".join(f'"{q["id"]}": ...' for q in questions)
    body = {"contents": history + [{"role": "user", "parts": [{"text": QUESTIONS_PROMPT.format(
        questions="\n".join(lines), skeleton=skeleton)}]}], "tools": [{"google_search": {}}]}
    status, data = _post(body, api_key, model, timeout, retries, sleep)
    if status != 200:
        return {"error": f"HTTP {status}: {str((data.get('error') or {}).get('message', ''))[:120]}"}
    text, _ = _text(data)
    m = re.search(r"<result>\s*(\{.*?\})\s*</result>", text, re.S)
    try:
        answers = json.loads(m.group(1)) if m else None
    except ValueError:
        answers = None
    if not isinstance(answers, dict):
        return {"error": "unparsable answer", "text": text[:500], "usage": [data.get("usageMetadata", {})]}
    wanted = {q["id"] for q in questions}
    return {"answers": {k: v for k, v in answers.items() if k in wanted},
            "usage": [data.get("usageMetadata", {})]}


def _plain(value):
    import enrich
    return " ".join(enrich.strip_latex(str(value)).replace("~", " ").lower().split()).strip(" .")


def apply_answers(entry_text, questions, answers, entry_type):
    """(entry_text, report) for step 3a's answers. A field answer fills a
    missing field and is never written over a value the entry has; a differing
    value, or a check whose answer is not what the type implies, goes to review.
    report = {'added': [field], 'confirmed': [field], 'conflicts': [str],
    'differs': [str], 'checks': {id: ok}}; 'differs' holds a field asked for
    automatically (q['auto']) whose value differs: logged, the entry's kept."""
    import enrich
    fields = {k.lower(): v for k, v in enrich.parse_bibtex_fields(entry_text).items()}
    report = {"added": [], "confirmed": [], "conflicts": [], "differs": [], "checks": {}}
    for q in questions:
        value = answers.get(q["id"])
        if value in (None, "", "null"):
            continue
        if q["use"] == "check":
            implied = q["expect"].get(entry_type)
            if implied is None:
                continue
            ok = _plain(value) == _plain(implied)
            report["checks"][q["id"]] = ok
            if not ok:
                report["conflicts"].append(f"{q['id']}: Gemini says {value!r}; @{entry_type} implies {implied!r}")
            continue
        name = q["use"].split(None, 1)[1].strip().lower()
        if name not in fields:
            entry_text = enrich.add_field(entry_text, name.capitalize(), str(value))
            report["added"].append(name)
        elif _plain(fields[name]) == _plain(value):
            report["confirmed"].append(name)
        elif q.get("auto"):
            # A field asked for because the type takes it: the entry's value
            # came from the source or a primary record, so it stands.
            report["differs"].append(f"{name}: entry has {fields[name]!r}; Gemini says {value!r}")
        else:
            report["conflicts"].append(f"{name}: entry has {fields[name]!r}; Gemini says {value!r}")
    # A subtitle just filled may still sit at the end of its title, written
    # "Title: Subtitle" by the extraction: one value in two fields, so the
    # title gives it up. Only an exact ": <subtitle>" tail is removed.
    fields = {k.lower(): v for k, v in enrich.parse_bibtex_fields(entry_text).items()}
    for parent, sub in (("title", "subtitle"), ("booktitle", "booksubtitle"),
                        ("maintitle", "mainsubtitle"), ("journaltitle", "journalsubtitle"),
                        ("issuetitle", "issuesubtitle")):
        if sub in report["added"] and parent in fields:
            tail = ": " + fields[sub]
            if fields[parent].endswith(tail) and len(fields[parent]) > len(tail):
                entry_text = enrich.set_field(entry_text, parent, fields[parent][:-len(tail)].rstrip())
                report.setdefault("trimmed", []).append(parent)
    return entry_text, report
