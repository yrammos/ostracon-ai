"""Book imprint by ISBN from Open Library: an exact key, no search ambiguity.

A whole book's PDF usually prints its ISBN on the copyright page; a chapter's
almost never does (0 of 22 @incollection sources in the eval set). So this
serves books, collections and reference works. The entry keeps no ISBN (a
field this project omits); the number is only a key.

The record fills missing Publisher, Location, Date and, for an edited volume,
Editor, after an identity check on the title: an ISBN printed in a book can
belong to another book (an advertisement, a series list).
"""
from __future__ import annotations

import re

import evidence

ISBN_RE = re.compile(r"ISBN(?:-1[03])?[^0-9Xx]{0,12}((?:\d[\s-]?){9}[\dXx](?:[\s-]?\d){0,3})")
EDITED = ("collection", "reference", "proceedings", "mvcollection", "mvreference")
TYPES = ("book", "mvbook") + EDITED


def _valid(digits):
    if len(digits) == 10:
        total = sum((10 - i) * (10 if c in "Xx" else int(c)) for i, c in enumerate(digits))
        return total % 11 == 0
    if len(digits) == 13 and digits.isdigit():
        total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits))
        return total % 10 == 0
    return False


def find_isbns(text):
    """Valid ISBNs in `text`, in order of appearance, without repeats."""
    out = []
    for m in ISBN_RE.finditer(text or ""):
        digits = re.sub(r"[\s-]", "", m.group(1))
        for cand in (digits[:13], digits[:10]):
            if _valid(cand) and cand not in out:
                out.append(cand)
                break
    return out


def _name(contribution):
    """'Bent, Ian, 1938-' -> 'Bent, Ian'."""
    parts = [p.strip() for p in contribution.split(",")]
    parts = [p for p in parts if p and not re.fullmatch(r"[\d\s.?-]+", p)]
    return ", ".join(parts[:2])


def lookup(isbn, timeout=15, get=None):
    """{'title', 'subtitle', 'publisher', 'location', 'date', 'editor'} or {'error'}."""
    import requests
    get = get or requests.get
    try:
        resp = get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=timeout,
                   headers={"User-Agent": "ostracon-ai bibliographic lookup"}, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}"}
    try:
        d = resp.json()
    except ValueError:
        return {"error": "not JSON"}
    year = re.search(r"\d{4}", d.get("publish_date") or "")
    rec = {"title": d.get("title") or "", "subtitle": d.get("subtitle") or "",
           "publisher": (d.get("publishers") or [""])[0],
           "location": (d.get("publish_places") or [""])[0].strip(" :;,"),
           "date": year.group(0) if year else ""}
    if re.search(r"\bedit", d.get("by_statement") or "", re.I) and d.get("contributions"):
        rec["editor"] = " and ".join(_name(c) for c in d["contributions"] if _name(c))
    return rec


def same_book(fields, rec):
    """Whether the record's title is this entry's: most of the record's title
    words occur in the entry's Title and Subtitle, and the reverse."""
    mine = {w for w in evidence.words(f"{fields.get('title', '')} {fields.get('subtitle', '')}") if len(w) > 2}
    theirs = {w for w in evidence.words(f"{rec.get('title', '')} {rec.get('subtitle', '')}") if len(w) > 2}
    if not mine or not theirs:
        return False
    return len(mine & theirs) / len(theirs) >= 0.7 and len(mine & theirs) / len(mine) >= 0.5


def fill(entry_type, fields, source_text, get=None):
    """(values, report): the missing fields an ISBN record supplies."""
    t = (entry_type or "").lower()
    report = {"isbns": [], "status": None}
    if t not in TYPES:
        report["status"] = "skipped: type"
        return {}, report
    isbns = find_isbns(source_text)
    report["isbns"] = isbns[:3]
    if not isbns:
        report["status"] = "no isbn"
        return {}, report
    for isbn in isbns[:3]:
        rec = lookup(isbn, get=get)
        if rec.get("error"):
            report["status"] = f"error: {rec['error']}"
            continue
        if not same_book(fields, rec):
            report["status"] = f"other book: {rec.get('title')!r}"
            continue
        keys = ["publisher", "location", "date"] + (["editor"] if t in EDITED else [])
        values = {k: rec[k] for k in keys if rec.get(k) and not evidence.present(fields, k)}
        report.update(status="matched", isbn=isbn)
        return values, report
    return {}, report
