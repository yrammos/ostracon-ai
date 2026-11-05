#!/usr/bin/env python3
"""Read-only measurement of src/library_match.py against a set of known entries.

For each entry in EXPECTED (each a library entry itself, excluded from its own
matching): strip its container fields but Booktitle and Date, as a thin
extraction would leave them, let container_fill() refill them from the rest of
the library, and compare with the entry as it stands. Prints coverage (entries
with a sibling) and precision (filled values equal to the expected ones, by
_key). Also counts duplicate hits, which should be none once the entry's own
key is excluded.

    python3 dev/library_match_coverage.py dev/eval/expected.bib [LIBRARY]

LIBRARY defaults to library_bib_file in config.yaml. Fills are limited to the
fields each entry's type takes, as in extraction.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import bib_audit  # noqa: E402
import enrich  # noqa: E402
import library_match as lm  # noqa: E402
import type_fields  # noqa: E402
from house_style import _key  # noqa: E402

KEEP = {'booktitle', 'date', 'year'}
YEAR_GATE = '--no-year-gate' not in sys.argv
sys.argv = [a for a in sys.argv if a != '--no-year-gate']


def main():
    expected = Path(sys.argv[1])
    if len(sys.argv) > 2:
        library = sys.argv[2]
    else:
        import yaml
        cfg = ROOT / 'config.yaml'
        library = (yaml.safe_load(cfg.read_text()) or {}).get('library_bib_file') if cfg.exists() else None
        if not library:
            sys.exit("No library: pass one, or set library_bib_file in config.yaml")
    lib = lm.load(library)
    if lib is None:
        sys.exit(f"No library at {library}")
    text = expected.read_text(encoding='utf-8')
    entries, _ = bib_audit.scan(text)
    sample = {e.citekey for e in entries}
    stats = collections.Counter()
    wrong = []
    for e in entries:
        stats['entries'] += 1
        entry = lm._clean(text[e.span[0]:e.span[1]])
        dups = lm.duplicates(entry, lib, exclude={e.citekey})
        if dups:
            stats['duplicate hits (other keys)'] += 1
            print(f"dup  {e.citekey}: {', '.join(dups)}")
        if e.etype in ('article', 'review'):
            stats['articles'] += 1
            _t, r = lm.container_fill(entry, lib, exclude={e.citekey})
            if r['siblings']:
                stats['articles with journal in library'] += 1
            if r['snapped']:
                stats['articles journal respelled'] += 1
                print(f"snap {e.citekey}: journal {lm._fields(entry).get('journal')!r} -> {lm._fields(_t).get('journal')!r}")
            continue
        if e.etype not in lm.CHILD_TYPES or not e.has('booktitle'):
            continue
        stats['chapters'] += 1
        full = lm._fields(entry)
        thin = entry
        for f in e.fields:
            if f.key in lm.SIBLING_FIELDS and f.key not in KEEP:
                thin = enrich.remove_field(thin, f.name)
        # Exclude only the entry itself: siblings elsewhere in the sample are
        # library entries a new item could meet.
        allowed = {f for f, _ in type_fields.fields_for(e.etype)} or None
        out, r = lm.container_fill(thin, lib, allowed=allowed, exclude={e.citekey},
                                   year_gate=YEAR_GATE)
        if not r['siblings']:
            continue
        stats['chapters with sibling'] += 1
        got = lm._fields(out)
        for x in r['added']:
            stats['fields filled'] += 1
            if x in full and _key(full[x]) == _key(got[x]):
                stats['fields filled correctly'] += 1
            else:
                wrong.append(f"{e.citekey}.{x}: filled {got[x]!r}; expected {full.get(x)!r}")
        stats['fields left (siblings disagree)'] += len(r['disagree'])
        stats['fields left (most siblings lack it)'] += len(r['minority'])
        missed = [x for x in full if x in lm.SIBLING_FIELDS and x not in KEEP and x not in got]
        stats['fields not recovered'] += len(missed)
    for k, v in stats.items():
        print(f"{k}: {v}")
    for w in wrong:
        print("WRONG", w)


if __name__ == '__main__':
    main()
