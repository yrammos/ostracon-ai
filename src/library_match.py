"""A new entry matched against the maintainer's library (library_bib_file).

Three uses, all deterministic and offline:

- duplicates(): the same work already in the library (DOI, or title + first
  author's surname + year). Logged, never coloured.
- container_fill(): a chapter or paper whose book is already in the library -
  as a sibling chapter with the same Booktitle, or as the book's own entry -
  takes the book's fields in the library's form: gaps filled, a value from a
  search (Scholar, Gemini) replaced, a value from the source kept. An article
  takes only its journal's spelling. Gated: same container title, same year,
  and no disagreement on volume or editor; a field the siblings disagree on is
  left alone.
- exemplars(): two or three library entries of the item's container or decided
  type, cleaned of bookkeeping fields, for the extraction prompt (opt-in:
  library_exemplars).

The library is read once per process and re-read when the file changes.
`exclude` keys are never matched: the eval harness passes its sample's
citekeys, which are library entries themselves.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

import bib_audit
from house_style import _key

# A chapter's container fields, as a sibling chapter writes them.
SIBLING_FIELDS = ('booktitle', 'booksubtitle', 'booktitleaddon', 'editor', 'editora',
                  'editorb', 'editortype', 'editoratype', 'editorbtype', 'publisher',
                  'location', 'series', 'number', 'volume', 'volumes', 'maintitle',
                  'mainsubtitle', 'maintitleaddon', 'edition')
# The book's own entry: its field -> the chapter's field.
PARENT_MAP = {'title': 'booktitle', 'subtitle': 'booksubtitle', 'titleaddon': 'booktitleaddon',
              'editor': 'editor', 'editora': 'editora', 'editorb': 'editorb',
              'editortype': 'editortype', 'editoratype': 'editoratype',
              'editorbtype': 'editorbtype', 'publisher': 'publisher', 'location': 'location',
              'series': 'series', 'number': 'number', 'volume': 'volume', 'volumes': 'volumes',
              'maintitle': 'maintitle', 'mainsubtitle': 'mainsubtitle',
              'maintitleaddon': 'maintitleaddon', 'edition': 'edition'}
PARENT_TYPES = {'book', 'collection', 'mvbook', 'mvcollection', 'proceedings', 'mvproceedings',
                'reference', 'mvreference'}
ROLE_NAMES = {'editor', 'editora', 'editorb'}
CHILD_TYPES = {'incollection', 'inproceedings', 'inbook', 'bookinbook', 'inreference'}
# Never shown to the extraction: BibDesk bookkeeping, paths, private notes.
PRIVATE = re.compile(r'^(bdsk-|date-added$|date-modified$|annote$|keywords$|file$|abstract$|'
                     r'read$|rating$|devonthink$)')
_CACHE: dict = {}


class Lib:
    def __init__(self, entries):
        self.entries = entries                        # [(key, type, {field: value})]
        self.by_container = collections.defaultdict(list)
        self.by_title = collections.defaultdict(list)
        self.by_journal = collections.defaultdict(list)
        self.by_doi = collections.defaultdict(list)
        for i, (_k, t, f) in enumerate(entries):
            if f.get('booktitle'):
                self.by_container[_key(f['booktitle'])].append(i)
            if f.get('title'):
                self.by_title[_key(f['title'])].append(i)
            j = f.get('journal') or f.get('journaltitle')
            if j:
                self.by_journal[_key(j)].append(i)
            if f.get('doi'):
                self.by_doi[f['doi'].strip().lower()].append(i)


def load(path):
    """The library's Lib, or None when no library is configured or readable."""
    if not path:
        return None
    p = Path(path).expanduser()
    try:
        stamp = p.stat().st_mtime
    except OSError:
        return None
    cached = _CACHE.get(str(p))
    if cached and cached[0] == stamp:
        return cached[1]
    text = p.read_text(encoding='utf-8', errors='replace')
    entries, _ = bib_audit.scan(text)
    lib = Lib([(e.citekey, e.etype, {f.key: f.value.strip() for f in e.fields if f.value.strip()})
               for e in entries])
    lib.text = text
    lib.spans = {e.citekey: e.span for e in entries}
    _CACHE[str(p)] = (stamp, lib)
    return lib


def _year(fields):
    m = re.search(r'\b(\d{4})\b', fields.get('date') or fields.get('year') or '')
    return m.group(1) if m else None


def _surnames(names):
    """Surnames of a BibTeX name list, as identity keys."""
    out = []
    for n in re.split(r'\s+and\s+', bib_audit.unwrap(names or '')):
        n = n.strip()
        if not n:
            continue
        last = n.split(',')[0] if ',' in n else n.split()[-1]
        out.append(_key(last))
    return out


def _fields(entry_text):
    import enrich
    return {k.lower(): v.strip() for k, v in enrich.parse_bibtex_fields(entry_text).items()
            if v.strip()}


def _title_key(f):
    return _key(f.get('title', '') + ' ' + f.get('subtitle', ''))


def duplicates(entry_text, lib, exclude=()):
    """Citekeys of library entries that are this work."""
    if lib is None:
        return []
    f = _fields(entry_text)
    hits = []
    doi = f.get('doi', '').lower()
    if doi:
        hits += [lib.entries[i][0] for i in lib.by_doi.get(doi, [])]
    t, y = f.get('title'), _year(f)
    first = (_surnames(f.get('author') or f.get('editor')) or [None])[0]
    if t and y and first:
        tk = _title_key(f)
        for i in lib.by_title.get(_key(t), []):
            k, _ty, g = lib.entries[i]
            if (_title_key(g) == tk and _year(g) == y
                    and first in _surnames(g.get('author') or g.get('editor'))):
                hits.append(k)
    seen = set(exclude)
    return [k for k in hits if not (k in seen or seen.add(k))]


def _agree(f, g, field):
    """Neither has the field, only one has it, or both say the same."""
    a, b = f.get(field), g.get(field)
    return not a or not b or _key(a) == _key(b)


def siblings(entry_text, lib, exclude=(), year_gate=True):
    """[(citekey, {chapter field: value})]: the library's other chapters in the
    same book, and the book itself, that pass the gates."""
    if lib is None:
        return []
    f = _fields(entry_text)
    etype = _etype(entry_text)
    book = f.get('booktitle')
    if etype not in CHILD_TYPES or not book or (year_gate and not _year(f)):
        return []
    skip = set(exclude) | set(duplicates(entry_text, lib))
    ck = _key(book)
    found = []
    for i in lib.by_container.get(ck, []):
        k, _t, g = lib.entries[i]
        if k not in skip:
            found.append((k, {x: g[x] for x in SIBLING_FIELDS if x in g}, g))
    for i in lib.by_title.get(ck, []) + lib.by_title.get(_key(book + ' ' + f.get('booksubtitle', '')), []):
        k, t, g = lib.entries[i]
        if t in PARENT_TYPES and k not in skip and k not in (x[0] for x in found):
            found.append((k, {PARENT_MAP[x]: g[x] for x in PARENT_MAP if x in g}, g))
    out = []
    for k, mapped, g in found:
        if year_gate and _year(g) != _year(f):
            continue
        if not all(_agree(f, mapped, x) for x in ('volume', 'editor', 'maintitle', 'edition')):
            continue
        out.append((k, mapped))
    return out


def _etype(entry_text):
    m = re.search(r'@(\w+)\s*\{', entry_text)
    return m.group(1).lower() if m else ''


def container_fill(entry_text, lib, allowed=None, replaceable=(), exclude=(), year_gate=True):
    """(entry_text, report). allowed: the fields the entry's type takes (None:
    any); replaceable: fields whose value came from a search, which the
    library outranks. report = {'siblings', 'added', 'replaced', 'snapped',
    'confirmed', 'differs', 'disagree', 'minority'}; a field in added,
    replaced, snapped or confirmed now holds the library's value."""
    import enrich
    report = {'siblings': [], 'added': [], 'replaced': [], 'snapped': [], 'confirmed': [],
              'differs': [], 'disagree': [], 'minority': []}
    if lib is None:
        return entry_text, report
    f = _fields(entry_text)
    etype = _etype(entry_text)
    if etype in ('article', 'review', 'suppperiodical'):
        return _journal_snap(entry_text, f, lib, exclude, report)
    sibs = siblings(entry_text, lib, exclude, year_gate)
    if not sibs:
        return entry_text, report
    report['siblings'] = [k for k, _ in sibs]
    values = collections.defaultdict(collections.Counter)
    for _k, m in sibs:
        for x, v in m.items():
            values[x][v] += 1
    # Each name list goes with its role (Editor with Editortype): both from
    # the library, or neither. A role the type list omits rides on its name.
    def unit(x):
        return x[:-4] if x.endswith('type') and x[:-4] in ROLE_NAMES else x
    usable = {}
    for x, counter in values.items():
        if allowed is not None and unit(x) not in allowed:
            continue
        # A field most siblings lack is one sibling's own (a series only the
        # book's entry records, an editor written in error): not the book's.
        if 2 * sum(counter.values()) <= len(sibs):
            report['minority'].append(x)
        elif len({_key(v) for v in counter}) > 1:
            report['disagree'].append(x)
        else:
            usable[x] = counter.most_common(1)[0][0]
    blocked = {unit(x) for x in report['minority'] + report['disagree']}
    for x, house in usable.items():
        if unit(x) in blocked:
            continue
        if unit(x) != x and not f.get(unit(x)) and unit(x) not in usable:
            continue
        have = f.get(x)
        if not have:
            new = enrich.add_field(entry_text, x, house)
            if new != entry_text:
                entry_text = new
                report['added'].append(x)
        elif have == house:
            report['confirmed'].append(x)
        elif _key(have) == _key(house):
            entry_text = enrich.set_field(entry_text, x, house)
            report['snapped'].append(x)
        elif x in replaceable:
            entry_text = enrich.set_field(entry_text, x, house)
            report['replaced'].append(x)
        else:
            report['differs'].append(f"{x}: entry has {have!r}; library has {house!r}")
    return entry_text, report


def _journal_snap(entry_text, f, lib, exclude, report):
    import enrich
    name = 'journal' if 'journal' in f else 'journaltitle' if 'journaltitle' in f else None
    if not name:
        return entry_text, report
    skip = set(exclude)
    forms = collections.Counter()
    for i in lib.by_journal.get(_key(f[name]), []):
        k, _t, g = lib.entries[i]
        if k not in skip:
            forms[g.get('journal') or g.get('journaltitle')] += 1
    if not forms:
        return entry_text, report
    house = forms.most_common(1)[0][0]
    if house != f[name]:
        entry_text = enrich.set_field(entry_text, name, house)
        report['snapped'].append(name)
    else:
        report['confirmed'].append(name)
    report['siblings'] = [f'{sum(forms.values())} in {house}']
    return entry_text, report


def _clean(entry_text):
    """An entry with BibDesk bookkeeping and private fields removed."""
    entries, _ = bib_audit.scan(entry_text)
    if not entries:
        return entry_text
    e = entries[0]
    keep = [f for f in e.fields if not PRIVATE.match(f.key)]
    body = ',\n'.join(f"\t{f.name} = {{{f.value.strip()}}}" for f in keep)
    return f"@{e.etype}{{{e.citekey},\n{body}\n}}"


def exemplars(lib, entry_type=None, container=None, exclude=(), n=3):
    """Up to n library entries, cleaned, as house-form examples: the
    container's own chapters or articles first, then the decided type's most
    recently modified entries."""
    if lib is None:
        return []
    skip = set(exclude)
    picked = []
    if container:
        ck = _key(container)
        for i in lib.by_container.get(ck, []) + lib.by_journal.get(ck, []):
            if lib.entries[i][0] not in skip and i not in picked:
                picked.append(i)
    if entry_type and len(picked) < n:
        typed = [i for i, (k, t, _f) in enumerate(lib.entries)
                 if t == entry_type and k not in skip and i not in picked]
        typed.sort(key=lambda i: lib.entries[i][2].get('date-modified', ''), reverse=True)
        picked += typed
    out = []
    for i in picked[:n]:
        a, b = lib.spans[lib.entries[i][0]]
        out.append(_clean(lib.text[a:b]))
    return out
