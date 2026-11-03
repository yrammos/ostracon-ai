#!/usr/bin/env python3
"""Read a web page as evidence about a work: its structured metadata, its
visible text in windows, and, where it names a DOI, the Crossref record.

read_page(url) tries, in order:
1. a plain HTTP fetch (free);
2. headless Chrome (`--dump-dom`, a throwaway profile), when the fetch is
   blocked or yields no text: it renders JavaScript and passes some bot checks;
3. Crossref, when the URL or the page's own metadata names a DOI: publishers
   that block both fetches publish the same record there.

Measured on 138 search-result pages for 30 random items (2026-11-03): the plain
fetch alone gave usable text for 52; with headless Chrome and whole-page text,
94. Publisher platforms (JSTOR, Oxford Academic, SAGE, UC Press, Duke) still
blocked both.

The text is the page's whole visible text (scripts, styles and <noscript>
removed), not a main-content extract: on the same pages, trafilatura dropped
the item's title from 9 catalogue and bookseller records where the raw text
kept it. Clutter is left for the reader to ignore. Long pages are read in
windows: `find` jumps to a passage, `offset` pages on.

Every outcome is reported as its own status; nothing is collapsed into "no
page". Only network, timeout and PDF-parsing failures are caught: a fault in
this code raises.
"""
import io
import os
import re
import shutil
import subprocess
import tempfile

import requests
from bs4 import BeautifulSoup

import enrich

USER_AGENT = "ostracon-ai bibliographic lookup"
WINDOW = 6000           # characters per reply, about 1,500 tokens
MIN_TEXT = 300          # below this a page counts as empty
PDF_PAGES = 40
BLOCK_CODES = {202, 401, 403, 429, 451, 503}
# Rough: a few phrases that bot-check interstitials show. A false positive only
# costs a Chrome attempt.
_CHALLENGE = re.compile(r"just a moment|cf-browser-verification|captcha|are you a robot|"
                        r"verify you are human|access denied", re.I)
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)

TOOL = {
    "name": "read_page",
    "description": (
        "Read a web page: its structured metadata (citation tags, schema.org, "
        "Open Graph), the Crossref record when it names a DOI, and a window of its "
        "visible text. Pages include site menus and unrelated listings; use only "
        "what concerns this work. Use `find` to jump to a passage (e.g. 'Contents', "
        "'edited by') and `offset` to read on."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "find": {"type": "string", "description": "Jump to the first occurrence (case-insensitive)."},
            "offset": {"type": "integer", "description": "Character offset to read from."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}

_cache = {}             # url -> fetched page, so paging does not refetch


def chrome_binary():
    for path in _CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def _visible_text(soup):
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def _parse_html(html):
    """(metadata, text). Metadata first: the text pass removes the <script>
    blocks that hold JSON-LD."""
    import web_source
    soup = BeautifulSoup(html, "html.parser")
    metadata = web_source._page_metadata(soup) or {}
    return metadata, _visible_text(soup)


def _pdf_text(data):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        return " ".join(" ".join((p.extract_text() or "").split())
                        for p in reader.pages[:PDF_PAGES])
    except (PdfReadError, ValueError, KeyError):
        return ""


def _http(url, timeout):
    """{'status', 'http_status', 'metadata', 'text', 'pdf'}; status is ok,
    blocked, empty, not-found or error."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException as e:
        return {"status": "error", "note": type(e).__name__}
    page = {"http_status": resp.status_code}
    ctype = resp.headers.get("content-type", "")
    if resp.status_code == 404 or resp.status_code == 410:
        return {**page, "status": "not-found"}
    if resp.status_code in BLOCK_CODES:
        return {**page, "status": "blocked"}
    if resp.status_code != 200:
        return {**page, "status": "error", "note": f"HTTP {resp.status_code}"}
    if "pdf" in ctype or resp.content[:5] == b"%PDF-":
        text = _pdf_text(resp.content)
        return {**page, "status": "ok" if len(text) >= MIN_TEXT else "empty",
                "metadata": {}, "text": text, "pdf": True}
    metadata, text = _parse_html(resp.text)
    if _CHALLENGE.search(resp.text[:6000]) and len(text) < 3 * MIN_TEXT:
        return {**page, "status": "blocked", "metadata": metadata, "text": text}
    return {**page, "status": "ok" if len(text) >= MIN_TEXT else "empty",
            "metadata": metadata, "text": text}


def _chrome(url, timeout):
    """The DOM after rendering, from headless Chrome with a throwaway profile
    (never the user's)."""
    binary = chrome_binary()
    if not binary:
        return {"status": "error", "note": "Chrome not found"}
    with tempfile.TemporaryDirectory() as profile:
        try:
            html = subprocess.run(
                [binary, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}",
                 "--virtual-time-budget=8000", "--dump-dom", url],
                capture_output=True, text=True, timeout=timeout).stdout
        except subprocess.TimeoutExpired:
            return {"status": "error", "note": "Chrome timed out"}
    if not html.strip():
        return {"status": "error", "note": "Chrome returned no DOM"}
    metadata, text = _parse_html(html)
    if _CHALLENGE.search(html[:6000]) and len(text) < 3 * MIN_TEXT:
        return {"status": "blocked", "metadata": metadata, "text": text}
    return {"status": "ok" if len(text) >= MIN_TEXT else "empty",
            "metadata": metadata, "text": text}


def _doi(url, metadata):
    """A DOI the page names for itself: in its URL or its own metadata, never
    its body text (which cites other works)."""
    for value in (url, *(v for k, v in metadata.items() if "doi" in k.lower())):
        found = enrich.extract_doi(str(value))
        if found:
            return found
    return None


def _crossref_record(doi, timeout):
    msg = enrich.crossref_by_doi(doi, timeout=timeout)
    if not msg:
        return None

    def names(key):
        return "; ".join(f"{p.get('family', '')}, {p.get('given', '')}".strip(", ")
                         for p in msg.get(key) or [])
    issued = (msg.get("issued") or {}).get("date-parts") or [[]]
    record = {
        "doi": doi,
        "type": msg.get("type"),
        "title": "; ".join(msg.get("title") or []),
        "container": "; ".join(msg.get("container-title") or []),
        "author": names("author"),
        "editor": names("editor"),
        "publisher": msg.get("publisher"),
        "issued": "-".join(str(x) for x in issued[0]),
        "isbn": "; ".join(msg.get("ISBN") or []),
        "page": msg.get("page"),
    }
    return {k: v for k, v in record.items() if v}


def _fetch(url, timeout, chrome):
    page = _http(url, timeout)
    page["via"] = "pdf" if page.get("pdf") else "http"
    if chrome and page["status"] in ("blocked", "empty"):
        rendered = _chrome(url, timeout * 3)
        if rendered["status"] == "ok":
            page = {**rendered, "via": "chrome", "http_status": page.get("http_status")}
        else:
            page["note"] = f"Chrome: {rendered['status']}" + (
                f" ({rendered['note']})" if rendered.get("note") else "")
    doi = _doi(url, page.get("metadata") or {})
    if doi:
        page["crossref"] = _crossref_record(doi, timeout)
    return page


def read_page(url, find=None, offset=0, window=WINDOW, timeout=20, chrome=True):
    """{'url', 'status', 'via', 'metadata', 'crossref', 'text', 'offset',
    'total', 'next_offset', 'note'}; the text is one window of the page."""
    if url not in _cache:
        _cache[url] = _fetch(url, timeout, chrome)
    page = _cache[url]
    text = page.get("text") or ""
    start = max(0, int(offset or 0))
    note = page.get("note")
    if find:
        hit = text.lower().find(find.lower(), start)
        if hit >= 0:
            start = max(0, hit - 200)
        else:
            note = (note + "; " if note else "") + f"'{find}' not found"
    end = min(len(text), start + window)
    return {
        "url": url,
        "status": page["status"],
        "via": page.get("via"),
        "http_status": page.get("http_status"),
        "metadata": page.get("metadata") or {},
        "crossref": page.get("crossref"),
        "text": text[start:end],
        "offset": start,
        "total": len(text),
        "next_offset": end if end < len(text) else None,
        "note": note,
    }
