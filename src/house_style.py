"""House cleanup for a new entry, the same rules the library was cleaned with.

Two steps, both deterministic:

- normalize(): the per-entry edits of bib_normalize.plan_edits() (title/
  subtitle split at a colon, Shorttitle, range punctuation, forbidden fields,
  an unearned Url, \\bibstring{nodate} in Year). The rules live in
  bib_normalize.py and bib_audit.py, shared with the library tools, so a new
  entry and the library cannot drift apart.
- snap_to_library(): where the house form is "the form the library already
  uses", a new value equivalent to a library value (same words, case, accents,
  punctuation and ties aside) takes the library's spelling. Fields: SNAP_FIELDS.
  The library is read once per process and cached; no library, no snapping.
"""
from __future__ import annotations

import collections
import re
import unicodedata
from pathlib import Path

import bib_audit
import bib_normalize

# Fields whose house form is the library's own spelling. Type: degree and
# report strings ("PhD diss."); Location: place names; Publisher.
SNAP_FIELDS = ('type', 'location', 'publisher')

_FORMS_CACHE: dict = {}


def normalize(entry_text):
    """(text, rules): entry_text with bib_normalize's per-entry edits applied,
    and the names of the rules that fired. Unchanged on any parse failure."""
    try:
        entries, _ = bib_audit.scan(entry_text)
        edits, _reports = bib_normalize.plan_edits(entry_text, entries)
        if not edits:
            return entry_text, []
        out = bib_normalize.apply_edits(entry_text, edits)
        # The rules insert field names in the library's lowercase; a new
        # entry written with capitalized names keeps its own style.
        if re.search(r'(?m)^\s*[A-Z][a-z]+\s*=', entry_text):
            before = {m.lower() for m in re.findall(r'(?m)^\s*([A-Za-z][\w-]*)\s*=', entry_text)}
            out = re.sub(r'(?m)^(\s*)([a-z]+)(\s*=)',
                         lambda m: m.group(0) if m.group(2) in before
                         else m.group(1) + m.group(2).capitalize() + m.group(3), out)
        return out, sorted({e.rule for e in edits})
    except Exception:  # noqa: BLE001 - cleanup must never cost the entry
        return entry_text, []


def _key(value):
    """A value's identity for snapping: wrapper, ties, case, accents and
    punctuation removed."""
    v = bib_audit.unwrap(value or '')
    v = re.sub(r'(?<!\\)~', ' ', v)
    v = re.sub(r'\\[a-zA-Z]+\s*', '', v).replace('{', '').replace('}', '')
    v = unicodedata.normalize('NFKD', v.lower())
    v = ''.join(c for c in v if not unicodedata.combining(c))
    v = re.sub(r'[^\w\s]', '', v)
    return ' '.join(v.split())


def library_forms(path):
    """{field: {key: the library's most frequent spelling}} for SNAP_FIELDS,
    read once per path (and re-read if the file changes)."""
    p = Path(path).expanduser()
    try:
        stamp = p.stat().st_mtime
    except OSError:
        return {}
    cached = _FORMS_CACHE.get(str(p))
    if cached and cached[0] == stamp:
        return cached[1]
    counts = {f: collections.defaultdict(collections.Counter) for f in SNAP_FIELDS}
    entries, _ = bib_audit.scan(p.read_text(encoding='utf-8', errors='replace'))
    for e in entries:
        for f in e.fields:
            k = f.key.lower()
            if k in counts and f.value.strip():
                counts[k][_key(f.value)][f.value.strip()] += 1
    forms = {f: {k: c.most_common(1)[0][0] for k, c in by.items() if k}
             for f, by in counts.items()}
    _FORMS_CACHE[str(p)] = (stamp, forms)
    return forms


def snap_to_library(entry_text, library_path):
    """(text, changes): each SNAP_FIELDS value given the library's spelling
    when the library holds an equivalent one; changes is [(field, old, new)]."""
    if not library_path:
        return entry_text, []
    forms = library_forms(library_path)
    if not forms:
        return entry_text, []
    import enrich
    fields = enrich.parse_bibtex_fields(entry_text)
    changes = []
    for f in SNAP_FIELDS:
        value = fields.get(f)
        if not value:
            continue
        house = forms.get(f, {}).get(_key(value))
        if house and house != value.strip():
            entry_text = enrich.set_field(entry_text, f, house)
            changes.append((f, value, house))
    return entry_text, changes
