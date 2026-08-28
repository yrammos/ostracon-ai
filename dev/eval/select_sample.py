#!/usr/bin/env python3
"""
Build the evaluation sample in one pass: choose a stratified set of entries
from a legacy .bib, resolve each one's attachment, copy it into sample/, and
write expected.bib and manifest.json.

    python3 dev/eval/select_sample.py
    python3 dev/eval/select_sample.py --n 61 --dry-run

Reads dev/eval/biblio.bib by default: a stripped copy of the library, placed
there by hand and kept out of version control because it quotes the library.

Two filters decide what may be a candidate. First, provenance: an entry counts
as ground truth only if it predates the pipeline, so its date-added must parse
and fall before --created-before. An entry whose date-added cannot be read is
excluded -- provenance is shown, not assumed. Second, availability: the
attachment must resolve to a file that exists right now. Filtering before
selecting rather than after is what removes the propose/check/substitute cycle;
an entry with a dead bookmark is never a candidate in the first place.

Re-running with a different --seed gives a disjoint sample from the same
strata, which is how to check later whether CLAUDE.md has been tuned to one
particular 61. The seed and cutoff are recorded in sample/selection.json so a
sample can be reproduced.

What this cannot do is verify that the chosen entries are *correct*. They are
ground truth because you wrote them from the sources before the pipeline
existed. The consistency report at the end flags entries that are internally
odd -- a chapter with no booktitle, a citekey year disagreeing with its date --
which is a check on the file, not a judgement about what any source says.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "dev"))
sys.path.insert(0, str(ROOT / "dev" / "eval"))

import bib_audit  # noqa: E402

# The attachment resolver already exists in populate_sample.py (bdsk-file-N
# bookmark decoding, local-url preference, extension filtering). Import it
# rather than reimplementing -- same rule as the audit/normalize split.
from populate_sample import resolve_attachment  # noqa: E402


# ── Strata ───────────────────────────────────────────────────────────────
#
# Quotas are by entry type, weighted toward what biblatex-chicago actually has
# to disambiguate rather than toward what the library contains most of: a
# corpus that is 48% @article would otherwise produce a sample that measures
# almost nothing the harder rules are for.
#
# FLOOR/CAP then shape the feature mix within those quotas. Floors force the
# awkward cases in; caps stop a greedy pass from filling the sample with
# whichever feature is commonest (without them, 40 of 61 came back non-Latin).
# AUTHOR_CAP stops the same author's house conventions dominating.
#
# These reflect one library. Fields absent from it -- origtitle, nameaddon,
# pubstate, origlanguage, related -- are deliberately not listed; adding a
# floor for a field with no instances would make the run unsatisfiable.

QUOTA = {
    "article": 11,
    "incollection": 8,
    "book": 6,
    "review": 6,
    "inbook": 4,
    "collection": 4,
    "thesis": 3,
    "inproceedings": 3,
    "inreference": 3,
    "online": 3,
    "unpublished": 2,
    "reference": 2,
    "suppbook": 2,
    "periodical": 2,
    "report": 1,
    "suppcollection": 1,
}

FEATURE_FIELDS = (
    "translator",
    "origdate",
    "entrysubtype",
    "titleaddon",
    "bookauthor",
    "volumes",
    "edition",
    "series",
    "editortype",
)

FLOOR = {
    "translator": 6,
    "origdate": 4,
    "entrysubtype": 3,
    "titleaddon": 4,
    "nonascii": 12,
    "volumes": 2,
    "edition": 3,
    "series": 6,
    "bookauthor": 2,
}
CAP = {
    "translator": 10,
    "origdate": 7,
    "entrysubtype": 5,
    "titleaddon": 7,
    "nonascii": 18,
    "volumes": 4,
    "edition": 5,
    "series": 10,
    "bookauthor": 3,
}
AUTHOR_CAP = 2

FEATURE_NOTE = {
    "translator": "translated",
    "origdate": "reprint/origdate",
    "entrysubtype": "subtype",
    "titleaddon": "titleaddon",
    "bookauthor": "bookauthor",
    "volumes": "multi-volume",
    "edition": "edition",
    "series": "series",
    "editortype": "editor role",
    "nonascii": "non-Latin/foreign title",
}


def field(entry, key):
    f = entry.get(key)
    return " ".join(f.value.split()) if f else ""


def features(entry):
    got = {k for k in FEATURE_FIELDS if entry.get(k)}
    title = field(entry, "title")
    if any(ord(c) > 127 for c in title) or "\\foreignlanguage" in title:
        got.add("nonascii")
    return got


def created_before(entry, cutoff_year):
    """True if the entry's date-added parses and predates cutoff_year.

    BibDesk writes 'YYYY-MM-DD HH:MM:SS +ZZZZ'. date-modified is useless for
    this: the August 2026 normalization pass touched some 740 entries, so a
    recent modification says nothing about when an entry was written. An
    unparsable date-added excludes the entry rather than admitting it.
    """
    m = re.match(r"\s*(\d{4})", field(entry, "date-added"))
    return bool(m) and int(m.group(1)) < cutoff_year


def surname(entry):
    s = field(entry, "author") or field(entry, "editor") or entry.citekey
    s = re.sub(r"\\foreignlanguage\{[^}]*\}", "", s).split(" and ")[0]
    parts = s.split(",") if "," in s else s.split()
    return (parts[0] if "," in s else parts[-1] if parts else s).strip("{} ").lower()


# ── Consistency flags ────────────────────────────────────────────────────
#
# Internal contradictions only. Never a claim about what a source says, and
# never a reason for this script to change anything -- the entries are the
# standard, so a flag is something for the maintainer to look at, not to fix
# automatically.


def consistency_flags(entry):
    out = []
    t = entry.etype.lower()
    has = lambda k: bool(entry.get(k))  # noqa: E731

    if t in ("incollection", "inbook", "suppbook") and not has("booktitle"):
        out.append("chapter-type entry with no booktitle")
    if t == "incollection" and not has("editor") and not has("bookauthor"):
        out.append("chapter with neither editor nor bookauthor")
    if t == "article" and not has("journaltitle") and not has("journal"):
        out.append("article with no journaltitle")
    if t == "review" and not any(
        has(k) for k in ("related", "userd", "booktitle", "titleaddon", "note")
    ):
        out.append("review with nothing identifying the work reviewed")
    if t == "thesis" and not has("institution") and not has("school"):
        out.append("thesis with no institution")
    if t == "online" and not has("url"):
        out.append("online with no url")
    if not any(has(k) for k in ("date", "year", "urldate")):
        out.append("no dating evidence at all")

    pages = field(entry, "pages")
    m = re.match(r"^(\d+)\s*-+\s*(\d+)$", pages)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if hi < lo:
            out.append(f"page range runs backwards ({pages})")
        elif t == "article" and hi - lo > 150:
            out.append(f"implausibly wide page range for an article ({pages})")

    key_year = re.search(r"(1[6-9]\d\d|20[0-2]\d)", entry.citekey)
    date_year = re.search(
        r"(1[6-9]\d\d|20[0-2]\d)", field(entry, "date") or field(entry, "year")
    )
    if key_year and date_year and key_year.group(1) != date_year.group(1):
        out.append(f"citekey year disagrees with date ({date_year.group(1)})")

    if field(entry, "entrysubtype").isdigit():
        out.append(f"entrysubtype holds a bare number ({field(entry, 'entrysubtype')})")

    return out


# ── Selection ────────────────────────────────────────────────────────────


def select(candidates, quota, seed):
    """Stratified pick over entries already known to have a live attachment."""
    rng = random.Random(seed)
    pool = list(candidates)
    rng.shuffle(pool)

    chosen, have, by_author = [], collections.Counter(), collections.Counter()

    def admissible(c):
        return (
            all(have[f] < CAP.get(f, 99) for f in c["feat"])
            and by_author[c["surname"]] < AUTHOR_CAP
        )

    def gain(c):
        return sum(1 for f in c["feat"] if have[f] < FLOOR.get(f, 0))

    def accept(c):
        chosen.append(c)
        have.update(c["feat"])
        by_author[c["surname"]] += 1

    for etype, n in quota.items():
        of_type = [c for c in pool if c["etype"] == etype]
        taken = []
        # Half feature-bearing, prioritising whatever is still below its floor.
        for c in sorted(
            [c for c in of_type if c["feat"]], key=lambda c: (-gain(c), len(c["feat"]))
        ):
            if len(taken) >= (n + 1) // 2:
                break
            if admissible(c) and gain(c) > 0:
                taken.append(c)
                accept(c)
        # Remainder plain, as a control stratum: if a change ever degrades
        # these, something is wrong that no exotic-type result would show.
        for c in sorted(
            [c for c in of_type if c not in taken], key=lambda c: len(c["feat"])
        ):
            if len(taken) >= n:
                break
            if admissible(c):
                taken.append(c)
                accept(c)

    return chosen, have, by_author


# ── Main ─────────────────────────────────────────────────────────────────


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "bib",
        nargs="?",
        default=str(ROOT / "dev" / "eval" / "biblio.bib"),
        help="Library to draw from (default: dev/eval/biblio.bib)",
    )
    p.add_argument("--sample-dir", default=str(ROOT / "dev" / "eval" / "sample"))
    p.add_argument("--expected", default=str(ROOT / "dev" / "eval" / "expected.bib"))
    p.add_argument(
        "--created-before",
        type=int,
        default=2026,
        metavar="YYYY",
        help="Only entries added before this year are eligible (default: 2026)",
    )
    p.add_argument("--n", type=int, default=61, help="Target size (quotas scale to it)")
    p.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Change for a different sample from the same strata",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    bib_path = Path(args.bib)
    if not bib_path.exists():
        print(
            f"{bib_path} not found. It is placed by hand and is not in version "
            f"control -- see dev/eval/README.md.",
            file=sys.stderr,
        )
        return 1
    text = bib_path.read_text(encoding="utf-8")
    entries, _ = bib_audit.scan(text)
    print(f"{len(entries)} entries in {bib_path}")

    eligible = [e for e in entries if created_before(e, args.created_before)]
    print(
        f"{len(eligible)} added before {args.created_before}; "
        f"{len(entries) - len(eligible)} excluded on provenance"
    )

    # Then resolve: only entries with a file on disk can be candidates.
    candidates, unresolved = [], 0
    for e in eligible:
        path = resolve_attachment(e)
        if not path or not Path(path).exists():
            unresolved += 1
            continue
        candidates.append(
            {
                "entry": e,
                "etype": e.etype.lower(),
                "citekey": e.citekey,
                "path": Path(path),
                "feat": features(e),
                "surname": surname(e),
            }
        )
    print(f"{len(candidates)} with a live attachment; {unresolved} without\n")

    scale = args.n / sum(QUOTA.values())
    quota = {t: max(1, round(n * scale)) for t, n in QUOTA.items()}

    chosen, have, by_author = select(candidates, quota, args.seed)
    chosen.sort(key=lambda c: c["citekey"])

    print(
        f"selected {len(chosen)}; {len(set(c['surname'] for c in chosen))} distinct authors; "
        f"{sum(1 for c in chosen if not c['feat'])} controls"
    )
    print("\ntype mix:")
    for t, n in collections.Counter(c["etype"] for c in chosen).most_common():
        print(f"  {t:16s} {n:3d}")
    print("\nfeature coverage (floor/cap):")
    for f in FLOOR:
        mark = " UNDER FLOOR" if have[f] < FLOOR[f] else ""
        print(f"  {f:14s} {have[f]:3d}   {FLOOR[f]}/{CAP[f]}{mark}")

    flagged = [
        (c["citekey"], flags)
        for c in chosen
        if (flags := consistency_flags(c["entry"]))
    ]
    print(
        f"\nconsistency flags ({len(flagged)} of {len(chosen)}) "
        f"-- check these against the source before trusting them:"
    )
    for citekey, flags in flagged:
        for fl in flags:
            print(f"  {citekey:24s} {fl}")
    if not flagged:
        print("  none")

    if args.dry_run:
        print("\n--dry-run: nothing copied, nothing written.")
        return 0

    sample_dir = Path(args.sample_dir)
    sample_dir.mkdir(parents=True, exist_ok=True)

    manifest, bib_out = [], []
    for c in chosen:
        dest = sample_dir / f"{c['citekey']}{c['path'].suffix.lower()}"
        shutil.copy2(c["path"], dest)
        note = f"@{c['etype']} — " + (
            ", ".join(sorted(FEATURE_NOTE[f] for f in c["feat"]))
            if c["feat"]
            else "plain (control)"
        )
        manifest.append(
            {
                "citekey": c["citekey"],
                "source": dest.name,
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "note": note,
            }
        )
        # Verbatim source bytes: expected.bib keeps the entry exactly as it was
        # written. Fields the pipeline never produces are excluded at scoring
        # time by scorer.py, not stripped here.
        start, end = c["entry"].span
        bib_out.append(text[start:end])

    (sample_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Kept beside the manifest rather than inside it: run.py expects
    # manifest.json to be a plain array, and this is provenance about the
    # selection, not about any one source.
    (sample_dir / "selection.json").write_text(
        json.dumps(
            {
                "source": str(bib_path),
                "created_before": args.created_before,
                "seed": args.seed,
                "n": len(manifest),
                "eligible": len(eligible),
                "with_attachment": len(candidates),
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    expected = Path(args.expected)
    header = expected.read_text(encoding="utf-8") if expected.exists() else ""
    header = "".join(l for l in header.splitlines(keepends=True) if l.startswith("%"))
    expected.write_text(header + "\n" + "\n\n".join(bib_out) + "\n", encoding="utf-8")

    print(f"\nwrote {len(manifest)} sources to {sample_dir}")
    print(f"wrote {len(bib_out)} entries to {expected}")
    print("\nRe-run with a different --seed for a second, disjoint sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
