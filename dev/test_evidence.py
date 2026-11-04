#!/usr/bin/env python3
"""Self-tests for the lookup steps: CrossRef subtitle, the page reader, the
evidence step (cited pages) and the ISBN lookup.

Network and Claude are stubbed with constructed replies (not recorded). The
pages are constructed from the shape of real publisher pages: citation_* meta
tags and a visible imprint line. Not stubbed: HTML-to-text, the domain,
identity and quote gates, ISBN checksums, and how values enter the entry.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import biblio_agent  # noqa: E402
import enrich  # noqa: E402
import evidence  # noqa: E402
import isbn_lookup  # noqa: E402
import page_reader  # noqa: E402

DRAFT = ("@incollection{Saslaw1996,\n\tAuthor = {Saslaw, Janna~K.},\n"
         "\tTitle = {Life Forces},\n\tSubtitle = {Conceptual Structures in Schenker's Free Composition},\n"
         "\tBooktitle = {Music Theory in the Age of Romanticism},\n\tPages = {217--244},\n"
         "\tLangid = {american},\n}")
PAGE = ("<html><head><meta name=\"citation_title\" content=\"Life Forces: Conceptual Structures in "
        "Schenker's Free Composition\"><meta name=\"citation_publisher\" content=\"Cambridge University Press\">"
        "<script>var x = 1;</script></head><body><h1>Life Forces</h1><p>Janna K. Saslaw</p>"
        "<p>In: Music Theory in the Age of Romanticism, edited by Ian Bent.<br>"
        "Cambridge: Cambridge University Press, 1996.</p></body></html>")
OTHER = "<html><body><h1>How the West Was Won</h1><p>A novel by Louis L'Amour, 1963.</p></body></html>"


class Resp:
    def __init__(self, status=200, text="", ctype="text/html; charset=utf-8", url="https://cambridge.org/x", js=None):
        self.status_code, self.url, self.encoding = status, url, "utf-8"
        self.headers = {"Content-Type": ctype}
        self.raw, self.content, self._js = None, text.encode(), js

    def json(self):
        if self._js is None:
            raise ValueError
        return self._js


# --- 1. CrossRef subtitle ---------------------------------------------------

def test_crossref_subtitle_is_read_and_admitted_only_from_a_doi():
    found = enrich.crossref_fields({"subtitle": ["The Human Capacity for Caring"], "volume": "3"})
    assert found["subtitle"] == "The Human Capacity for Caring"
    assert "subtitle" in enrich._admit(found, enrich.CROSSREF_DOI, "article", {"title": "How Social Is the Animal?"}, {})
    assert "subtitle" not in enrich._admit(found, enrich.CROSSREF_SEARCH, "article", {"title": "X"}, {})
    unsplit = {"title": "How Social Is the Animal? The Human Capacity for Caring"}
    assert "subtitle" not in enrich._admit(found, enrich.CROSSREF_DOI, "article", unsplit, {})


# --- 2. Page reader ------------------------------------------------------------

def test_page_reader_keeps_meta_records_and_drops_scripts():
    text = page_reader.html_to_text(PAGE)
    assert "citation_publisher: Cambridge University Press" in text
    assert "var x" not in text and "edited by Ian Bent." in text


def test_read_page_follows_redirects_and_reports_failures():
    ok = page_reader.read_page("https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                               session=type("S", (), {"get": lambda self, u, **k: Resp(text=PAGE, url="https://www.cambridge.org/core/x")})())
    assert ok["domain"] == "cambridge.org" and "Ian Bent" in ok["text"], ok
    bad = page_reader.read_page("u", session=type("S", (), {"get": lambda self, u, **k: Resp(status=403)})())
    assert bad["error"] == "HTTP 403"
    pdf = page_reader.read_page("u", session=type("S", (), {"get": lambda self, u, **k: Resp(ctype="application/pdf", text="junk")})())
    assert pdf["error"].startswith("unreadable PDF"), pdf
    img = page_reader.read_page("u", session=type("S", (), {"get": lambda self, u, **k: Resp(ctype="image/png")})())
    assert img["error"].startswith("not a web page")


# --- 3. Evidence step ----------------------------------------------------------

def test_identity_gate_rejects_another_work_with_the_same_title_words():
    fields = enrich.parse_bibtex_fields(DRAFT)
    assert evidence.identity(fields, page_reader.html_to_text(PAGE))[0]
    west = {"title": "Music at the Limits", "author": "De Groot, Rokus", "booktitle": "How the West Was Won"}
    assert not evidence.identity(west, page_reader.html_to_text(OTHER))[0]


def test_accept_needs_a_quote_on_the_page_that_contains_the_value():
    page = page_reader.html_to_text(PAGE)
    reply = {"same_work": True, "fields": {
        "editor": {"value": "Bent, Ian", "quote": "edited by Ian Bent"},
        "location": {"value": "Cambridge", "quote": "Cambridge: Cambridge University Press, 1996"},
        "date": {"value": "1996", "quote": "published in 1996 by CUP"},        # not on the page
        "publisher": {"value": "Oxford University Press", "quote": "Cambridge University Press"},
    }}
    ok, rejected = evidence.accept(reply, page, ["editor", "location", "date", "publisher"])
    assert ok == {"editor": "Bent, Ian", "location": "Cambridge"}, ok
    assert rejected == {"date": "quote not on the page", "publisher": "value not in its quote"}, rejected
    assert evidence.accept({"same_work": False}, page, ["editor"])[0] == {}


def test_wanted_lists_container_fields_a_chapter_lacks():
    assert evidence.wanted("incollection", enrich.parse_bibtex_fields(DRAFT)) == \
        ["editor", "publisher", "location", "date"]


def _agent(replies):
    agent = biblio_agent.BiblioAgent.__new__(biblio_agent.BiblioAgent)
    agent.config = {"verbose": False}
    agent.usage, agent._local, agent._progress_callback = [], threading.local(), None
    msgs = list(replies)
    reply = lambda text: type("M", (), {"content": [type("B", (), {"type": "text", "text": text})()],
                                        "stop_reason": "end_turn", "usage": None})()
    agent.client = type("C", (), {"messages": type("Ms", (), {
        "create": lambda self, **kw: reply(msgs.pop(0))})()})()
    return agent


def test_cited_pages_fill_missing_fields_and_skip_excluded_and_other_works():
    sources = [{"title": "researchgate.net", "url": "r"}, {"title": "other.org", "url": "o"},
               {"title": "cambridge.org", "url": "c"}]
    pages = {"o": {"url": "o", "domain": "other.org", "text": page_reader.html_to_text(OTHER)},
             "c": {"url": "c", "domain": "cambridge.org", "text": page_reader.html_to_text(PAGE)}}
    answer = ('<result>{"same_work": true, "fields": {"editor": {"value": "Bent, Ian", "quote": "edited by Ian Bent"},'
              '"publisher": {"value": "Cambridge University Press", "quote": "Cambridge: Cambridge University Press, 1996"},'
              '"location": {"value": "Cambridge", "quote": "Cambridge: Cambridge"},'
              '"date": {"value": "1996", "quote": "Cambridge University Press, 1996"}}}</result>')
    gemini = {"sources": sources}
    out, fs, color = _agent([answer])._read_cited_pages(DRAFT, gemini, None, "m", read=lambda u: pages[u])
    f = enrich.parse_bibtex_fields(out)
    assert f["editor"] == "Bent, Ian" and f["publisher"] == "Cambridge University Press" and f["date"] == "1996", out
    assert f["booktitle"] == "Music Theory in the Age of Romanticism"        # untouched
    assert color and "cambridge.org" in fs
    skipped = dict(gemini["pages"]["skipped"])
    assert skipped["researchgate.net"] == "excluded domain" and skipped["other.org"].startswith("not this work")


# --- 4. ISBN ---------------------------------------------------------------------

def test_isbns_are_found_with_valid_checksums_only():
    text = "Copyright 1996. ISBN 0-521-55102-1 (hardback). ISBN 978-0-521-02008-4. ISBN 0-521-55102-2"
    assert isbn_lookup.find_isbns(text) == ["0521551021", "9780521020084"]


def test_isbn_record_fills_imprint_and_editor_of_the_same_book_only():
    rec = {"title": "Music theory in the age of Romanticism", "publishers": ["Cambridge University Press"],
           "publish_places": ["Cambridge", "New York"], "publish_date": "1996",
           "by_statement": "edited by Ian Bent.", "contributions": ["Bent, Ian, 1938-"]}
    get = lambda url, **kw: Resp(js=rec)
    fields = {"title": "Music Theory in the Age of Romanticism"}
    values, report = isbn_lookup.fill("collection", fields, "ISBN 0-521-55102-1", get=get)
    assert values == {"publisher": "Cambridge University Press", "location": "Cambridge",
                      "date": "1996", "editor": "Bent, Ian"}, values
    values, report = isbn_lookup.fill("collection", {"title": "Schenker's Argument"}, "ISBN 0-521-55102-1", get=get)
    assert values == {} and report["status"].startswith("other book"), report


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
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} lookup self-tests passed.")
    sys.exit(1 if failed else 0)
