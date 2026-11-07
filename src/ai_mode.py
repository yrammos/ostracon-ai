#!/usr/bin/env python3
"""The type search and its gap-filling fields (extract_bibtex steps 2 and 7)
through Google Search's AI Mode, in Google Chrome (type_search: "ai-mode").

One query per item, in the maintainer's wording: find the optimal
biblatex-chicago type for the work, research it, and write the entry. Chrome
is driven by AppleScript, and the answer is read from the page's text; no API
key and no model call. Measured on 20 hold-out items (PR #111): 18/20 types
right, its field values less often right than values read from the source,
so they only fill gaps (gemini_search.apply_answers, as for the other
engines).

Needs Google Chrome running and signed in, View > Developer > "Allow
JavaScript from Apple Events", and Automation permission for whatever runs
the pipeline. Any failure (a block page, a refused permission, no entry
within the time limit) is reported as such, and the extraction then chooses
the type itself.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse

import enrich
import web_source as ws

PROMPT = ('Find the optimal biblatex-chicago type for {author}\'s "{title}". Consider all '
          'available types according to the biblatex-chicago ontology. Then do online research '
          'and populate a biblatex-chicago entry with the optimal fields for this item.')
# AI Mode is Google Search with udm=50 (the parameter its own link uses; not
# documented by Google). Confirmed in Chrome on 2026-11-07 (PR #111).
URL = "https://www.google.com/search?udm=50&q={q}"
BLOCKED = ("unusual traffic", "our systems have detected", "before you continue to google",
           "not a robot")
JS_TEXT = "document.body ? document.body.innerText : ''"
WAIT = 180          # seconds for the answer to appear and settle
# One query at a time, whatever the batch's parallelism: one Chrome, one
# Google account.
_LOCK = threading.Lock()


def first_entry(text):
    """The first brace-balanced @type{key, ...} in text, or None."""
    for m in re.finditer(r"@([A-Za-z]+)\s*\{", text or ""):
        depth, i = 0, m.end() - 1
        while i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                return text[m.start():i + 1]
            i += 1
    return None


def _natural(name):
    family, _, given = name.partition(",")
    return f"{given.strip()} {family.strip()}".strip() if given else family.strip()


def query_parts(fields):
    """(author, title) for the prompt, from BiblioAgent._identity_fields():
    the first author (or editor) in natural order, 'et al.' when there are
    more; an entry's heading (Lista) for an entry in a reference work."""
    def plain(v):
        return enrich.strip_latex(v or "").replace("~", " ").strip()
    names = [n.strip() for n in re.split(r"\s+and\s+", plain(fields.get("author") or fields.get("editor")))
             if n.strip()]
    author = _natural(names[0]) + (" et al." if len(names) > 1 else "") if names else ""
    title = plain(fields.get("lista") or fields.get("title"))
    if fields.get("subtitle") and not fields.get("lista"):
        title = f"{title}: {plain(fields['subtitle'])}"
    return author, title


def _page_text(tab_id, timeout=20):
    script = ('tell application "Google Chrome"\n'
              '  repeat with w in windows\n'
              f'    repeat with t in (every tab of w whose id is {int(tab_id)})\n'
              f'      return execute t javascript "{JS_TEXT}"\n'
              '    end repeat\n'
              '  end repeat\n'
              '  return ""\n'
              'end tell')
    return ws._osascript(script, timeout=timeout)


def ask(author, title, log=None, wait=WAIT, poll=4.0, settle=3):
    """(status, entry, text): status is 'ok', 'no entry', 'blocked', 'refused'
    or 'error'. Waits until the page holds an entry and has not changed for
    `settle` polls (AI Mode streams its answer); always closes its tab."""
    log = log or (lambda m: None)
    url = URL.format(q=urllib.parse.quote_plus(PROMPT.format(author=author, title=title)))
    with _LOCK:
        tab, detail = ws.open_in_chrome(url, log=log, wait=30)
        if not tab:
            refused = ws.OSA_REFUSED in detail or "-1743" in detail
            return ("refused" if refused else "error"), None, detail
        try:
            deadline, last, same, text = time.monotonic() + wait, None, 0, ""
            while time.monotonic() < deadline:
                time.sleep(poll)
                status, text = _page_text(tab)
                if status == ws.OSA_REFUSED:
                    return "refused", None, text
                if status != ws.OSA_OK:
                    continue
                if any(b in text.lower() for b in BLOCKED):
                    return "blocked", None, text
                entry = first_entry(text)
                same = same + 1 if entry and text == last else 0
                last = text
                if entry and same >= settle:
                    return "ok", entry, text
            entry = first_entry(text)
            return ("ok" if entry else "no entry"), entry, text
        finally:
            ws.close_chrome_tab(tab)


def decide_type(fields, log=None, asker=None):
    """{'type', 'found', 'reason', 'entry', 'searches': 0} or one with 'error'.
    `asker` replaces ask() in tests."""
    author, title = query_parts(fields)
    if not title:
        return {"error": "no title"}
    status, entry, text = (asker or ask)(author, title, log=log)
    if status in ("blocked", "refused", "error"):
        return {"error": f"AI Mode {status}: {(text or '')[:120]}", "searches": 0}
    if not entry:
        return {"found": False, "type": None, "reason": "no entry in the answer", "searches": 0}
    t = re.match(r"@([A-Za-z]+)", entry).group(1).lower()
    return {"found": True, "type": t, "entry": entry, "searches": 0,
            "reason": f"AI Mode wrote @{t} for {author}, \"{title}\""}


def answers(entry, final_type, allowed):
    """(questions, answers) in gemini_search.apply_answers()'s shape: one
    automatic 'field' question per field of AI Mode's entry that the final
    type takes, after the house cleanup, so its values only fill gaps."""
    import house_style
    entry, _ = enrich.strip_forbidden_fields(entry)
    entry, _ = house_style.normalize(entry)
    entry, _ = house_style.repair(entry, None)
    fields = {k.lower(): v for k, v in enrich.parse_bibtex_fields(entry).items()}
    qs, ans = [], {}
    for name, value in fields.items():
        if name in allowed and str(value).strip():
            qid = f"field_{name}"
            qs.append({"id": qid, "when": [final_type], "use": f"field {name}", "auto": True,
                       "ask": name})
            ans[qid] = value
    return qs, ans
