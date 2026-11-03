#!/usr/bin/env python3
"""Render every entry of .bib files or directories and report fields that
never reach the page, and entries that break the build; no API calls.

    OSTRACON_PREVIEW_TEMPLATE=/path/previewtemplate.tex \\
        python3 dev/render_entries.py dev/eval/expected.bib dev/eval/out/last-run-X/

Each entry is compiled alone (three LaTeX passes and biber, a few seconds),
several at a time. A directory is read as one entry per file, the shape
dev/eval/run.py saves; files with no entry are skipped.
"""
import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import bib_audit  # noqa: E402
import bib_render  # noqa: E402
from bib_validate import ValidatorUnavailable  # noqa: E402


def entries(path):
    """[(label, entry_text, citekey, {field: value})]"""
    path = Path(path)
    files = [path] if path.is_file() else sorted(path.glob("*.bib"))
    out = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for e in bib_audit.scan(text)[0]:
            label = e.citekey if path.is_file() else f.stem
            fields = {fl.key: fl.value for fl in e.fields}
            out.append((label, text[e.span[0]:e.span[1]], e.citekey, fields))
    return out


def check(item):
    label, text, key, fields = item
    try:
        rendered = bib_render.render(text, key)
    except bib_render.RenderFailed as e:
        return label, None, str(e).splitlines()[0]
    return label, bib_render.dropped_fields(fields, rendered), None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-j", type=int, default=6, help="parallel compiles (default 6)")
    args = ap.parse_args(argv)

    for p in args.paths:
        items = entries(p)
        print(f"== {p}: {len(items)} entries")
        try:
            with ThreadPoolExecutor(args.j) as pool:
                results = list(pool.map(check, items))
        except ValidatorUnavailable as e:
            print(f"   no verdict: {e}")
            return 2
        dropped, failed = Counter(), 0
        for label, fields, error in results:
            if error:
                failed += 1
                print(f"   {label}: COMPILE FAILED ({error})")
            elif fields:
                dropped.update(fields)
                print(f"   {label}: not printed: {', '.join(fields)}")
        print(f"   -- {failed} failed compiles; "
              f"{sum(1 for _, f, _ in results if f)} entries with unprinted fields")
        for f, c in dropped.most_common():
            print(f"   {c:4}  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
