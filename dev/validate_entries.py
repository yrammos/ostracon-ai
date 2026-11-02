#!/usr/bin/env python3
"""Run src/bib_validate.py over .bib files or directories; no API calls.

    python3 dev/validate_entries.py dev/eval/expected.bib
    python3 dev/validate_entries.py prompt-context/biblio-template.bib
    python3 dev/validate_entries.py dev/eval/out/last-run-2026-11-run0/

A directory is read as one entry per file, the shape dev/eval/run.py saves:
each entry's citekey is replaced by its filename stem, so two sources that
produced the same key stay distinct, and a file holding only a
`% extraction failed` comment is skipped and counted.

Prints the warnings per entry, then a count per kind of warning. Exit status
is 0 whether or not warnings are found; 2 when no verdict could be reached.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bib_validate  # noqa: E402

KEY = re.compile(r"(@\w+\{)([^,\s]+),")


def collect(path):
    """(bib_text, number of entries, failed files) for a file or directory."""
    path = Path(path)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        return text, len(KEY.findall(text)), []
    parts, failed = [], []
    for f in sorted(path.glob("*.bib")):
        text = f.read_text(encoding="utf-8")
        if not KEY.search(text):
            failed.append(f.stem)
            continue
        parts.append(KEY.sub(lambda m: f"{m.group(1)}{f.stem},", text, count=1))
    return "\n\n".join(parts) + "\n", len(parts), failed


def kind(message):
    """The warning with its entry-specific values removed, for counting."""
    return re.sub(r"'[^']*'(?= must be)|value '[^']*'|year '[^']*'", "…", message)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help=".bib files or directories of per-entry .bib files")
    args = ap.parse_args(argv)

    status = 0
    for p in args.paths:
        text, n, failed = collect(p)
        print(f"== {p}: {n} entries" + (f", {len(failed)} failed extractions skipped" if failed else ""))
        try:
            found = bib_validate.validate(text)
        except bib_validate.ValidatorUnavailable as e:
            print(f"   no verdict: {e}")
            status = 2
            continue
        by_key = defaultdict(list)
        for key, msg in found:
            by_key[key].append(msg)
        for key, msgs in by_key.items():
            print(f"   {key}")
            for m in msgs:
                print(f"     {m}")
        counts = Counter(kind(m) for _, m in found)
        print(f"   -- {len(found)} warnings in {len(by_key)} of {n} entries")
        for k, c in counts.most_common():
            print(f"   {c:4}  {k}")
    return status


if __name__ == "__main__":
    sys.exit(main())
