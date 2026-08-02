#!/usr/bin/env python3
"""Score two extraction runs against the curated entries they were drawn from.

The A/B this exists for: does replacing the hand-condensed field reference
(`biblatex-chicago-notes-ref.md`) with a mechanical extraction of §4.2 of the
manual (`biblatex-chicago-fields.md`) improve extraction quality, or does the
extra ~40k tokens of context dilute attention and make it worse?

Ground truth is the entry already in biblio.bib for each source PDF. That is
not a perfect oracle -- the curated entry can itself be wrong, and this session
found several that were -- so disagreement is reported per field rather than
reduced to a single score, and the entry-type and field-presence signals are
kept separate from the value-match signal.

    python3 dev/ab_compare.py <ab_dir>
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import scan  # noqa: E402

# Bookkeeping the extractor is not asked to reproduce.
IGNORE = re.compile(r"^(date-added|date-modified|keywords|rating|read|reference|"
                    r"bdsk-|local-url|remote-url|devonthink)")


def norm(v: str) -> str:
    """Compare on substance, not on whitespace or brace-level trivia."""
    v = re.sub(r"\s+", " ", v).strip().rstrip(".").strip()
    return v.casefold()


def load(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"@[A-Za-z]+\{", txt)
    if not m:
        return None
    entries, _ = scan(txt[m.start():])
    return entries[0] if entries else None


def item_truth(man, citekey):
    for it in man:
        if it["citekey"] == citekey:
            return {k: v for k, v in it["truth"].items() if not IGNORE.match(k)}
    return {}


def main():
    D = sys.argv[1]
    man = json.load(open(os.path.join(D, "manifest.json"), encoding="utf-8"))
    stats = {c: Counter() for c in ("A", "B")}
    per_field = {c: defaultdict(Counter) for c in ("A", "B")}
    rows = []

    for item in man:
        base = f"{item['n']:02d}_{item['citekey']}"
        truth = {k: v for k, v in item["truth"].items() if not IGNORE.match(k)}
        row = {"key": item["citekey"], "etype": item["etype"],
               "traits": item["traits"]}
        for cfg in ("A", "B"):
            e = load(os.path.join(D, f"out_{cfg}", base + ".bib"))
            if e is None:
                stats[cfg]["failed"] += 1
                row[cfg] = None
                continue
            got = {f.key: f.value for f in e.fields if not IGNORE.match(f.key)}
            same_type = e.etype == item["etype"]
            stats[cfg]["type_ok"] += same_type
            match = sum(1 for k, v in truth.items()
                        if k in got and norm(got[k]) == norm(v))
            missing = [k for k in truth if k not in got]
            extra = [k for k in got if k not in truth]
            differ = [k for k, v in truth.items()
                      if k in got and norm(got[k]) != norm(v)]
            stats[cfg]["fields_truth"] += len(truth)
            stats[cfg]["fields_match"] += match
            stats[cfg]["fields_missing"] += len(missing)
            stats[cfg]["fields_extra"] += len(extra)
            stats[cfg]["fields_differ"] += len(differ)
            for k in missing:
                per_field[cfg][k]["missing"] += 1
            for k in differ:
                per_field[cfg][k]["differ"] += 1
            row[cfg] = {"etype": e.etype, "type_ok": same_type, "match": match,
                        "of": len(truth), "missing": missing, "differ": differ,
                        "extra": extra, "keys": sorted(got)}
        rows.append(row)

    n = len(man)
    print("=" * 74)
    print(f"A/B over {n} sources    A = condensed reference    B = manual §4.2")
    print("=" * 74)
    hdr = f"{'':26}{'A':>10}{'B':>10}"
    print(hdr)
    for label, key, tot in (
            ("entry type correct", "type_ok", n),
            ("field values matched", "fields_match", None),
            ("fields missing", "fields_missing", None),
            ("fields differing", "fields_differ", None),
            ("extra fields emitted", "fields_extra", None),
            ("extractions failed", "failed", None)):
        a, b = stats["A"][key], stats["B"][key]
        sa = f"{a}/{tot}" if tot else str(a)
        sb = f"{b}/{tot}" if tot else str(b)
        print(f"{label:26}{sa:>10}{sb:>10}")
    for cfg in ("A", "B"):
        t = stats[cfg]["fields_truth"]
        if t:
            print(f"{'  value accuracy ' + cfg:26}"
                  f"{stats[cfg]['fields_match'] / t:>9.1%}"
                  .rjust(36 if cfg == 'A' else 46))

    print("\nper source (matched / fields in the curated entry):")
    print(f"  {'citekey':<22}{'type':<16}{'A':>8}{'B':>8}   traits")
    for r in rows:
        a = f"{r['A']['match']}/{r['A']['of']}" if r["A"] else "FAIL"
        b = f"{r['B']['match']}/{r['B']['of']}" if r["B"] else "FAIL"
        ta = "" if not r["A"] or r["A"]["type_ok"] else f" A:{r['A']['etype']}"
        tb = "" if not r["B"] or r["B"]["type_ok"] else f" B:{r['B']['etype']}"
        print(f"  {r['key']:<22}{r['etype']:<16}{a:>8}{b:>8}   "
              f"{','.join(r['traits'])}{ta}{tb}")

    # The fields this experiment actually turns on. Aggregate accuracy is
    # dominated by location/date/author -- source-reading, not field semantics
    # -- so the semantics-bearing fields are scored separately or the signal
    # is swamped by noise from a different failure mode entirely.
    SEMANTIC = ("foreword", "introduction", "afterword", "lista", "entrysubtype",
                "type", "editortype", "issuetitle", "institution", "eventtitle",
                "bookauthor", "userd", "organization")
    # biblatex-chicago accepts TWO encodings for supplementary matter in
    # @SuppBook/@SuppCollection. Tier 1, prose:intro's annote: "Instead of the
    # mechanism using a defined introduction field, here I use the alternative
    # of putting the type of supplemental material in the type field, with the
    # appropriate preposition." The worked examples pair each with an authorship
    # case -- polakow:afterw (flag, no bookauthor, the book's own author)
    # against prose:intro (type, bookauthor present, someone else's book).
    #
    # Scoring these on one criterion is where an earlier pass went wrong: it
    # checked the flag's VALUE but accepted a `type` field on mere presence, so
    # `foreword = {X}` against a truth of `introduction` scored 0 while
    # `type = {preface to}` against the same truth scored 1 -- the same error
    # class, opposite scores. Two symmetric rules are reported instead, and
    # neither is the headline on its own.
    FLAGS = ("foreword", "introduction", "afterword")

    def supp(r, cfg):
        """(reached for some markup, named the right kind) -- judged alike."""
        got = r.get(cfg)
        if not got:
            return False, False
        want = set(FLAGS) & set(item_truth(man, r["key"]))
        emitted = set(got["keys"]) & (set(FLAGS) | {"type"})
        exact = bool(want) and not (want & (set(got["missing"]) | set(got["differ"])))
        return bool(emitted), exact

    supp_rows = [r for r in rows if set(FLAGS) & set(item_truth(man, r["key"]))]
    if supp_rows:
        n_s = len(supp_rows)
        print(f"\nsupplementary matter (@SuppBook/@SuppCollection), {n_s} sources:")
        for label, idx in (("reached for some markup", 0),
                           ("named the right kind", 1)):
            a = sum(supp(r, "A")[idx] for r in supp_rows)
            b = sum(supp(r, "B")[idx] for r in supp_rows)
            print(f"  {label:<26} A {a}/{n_s}   B {b}/{n_s}")

    print("\nsemantics-bearing fields (the ones this experiment turns on):")
    print(f"  {'field':<16}{'in truth':>9}{'A got':>8}{'B got':>8}   sources")
    any_row = False
    for f in SEMANTIC:
        want = [r for r in rows if f in item_truth(man, r["key"])]
        if not want:
            continue
        any_row = True
        a = sum(1 for r in want if r["A"] and f not in r["A"]["missing"]
                and f not in r["A"]["differ"])
        b = sum(1 for r in want if r["B"] and f not in r["B"]["missing"]
                and f not in r["B"]["differ"])
        print(f"  {f:<16}{len(want):>9}{a:>8}{b:>8}   "
              f"{', '.join(r['key'] for r in want)[:44]}")
    if not any_row:
        print("  (none present in this sample)")

    print("\nfields most often wrong, by config:")
    for cfg in ("A", "B"):
        worst = sorted(per_field[cfg].items(),
                       key=lambda kv: -sum(kv[1].values()))[:8]
        print(f"  {cfg}: " + ", ".join(
            f"{k}({sum(v.values())})" for k, v in worst))

    json.dump({"stats": {c: dict(stats[c]) for c in stats}, "rows": rows},
              open(os.path.join(D, "results.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"\nwritten: {os.path.join(D, 'results.json')}")


if __name__ == "__main__":
    sys.exit(main())
