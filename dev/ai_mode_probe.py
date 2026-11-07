#!/usr/bin/env python3
"""Measure Google Search's AI Mode alone (src/ai_mode.py, type_search:
"ai-mode") on items with known entries, and save its answers for scoring.
No API key, no Claude: Chrome is driven by AppleScript.

    python dev/ai_mode_probe.py --only Juslin2001,Konak2016
    python dev/eval/run.py --rescore --sample-dir dev/eval/holdout/sample \\
        --expected dev/eval/holdout/expected.bib --last-run-dir dev/eval/out/ai-mode \\
        --only Juslin2001,Konak2016

Each item's author and title come from its expected entry (the pipeline takes
them from its identity call). Results land in --out as <citekey>.raw.bib (the
entry in the answer), <citekey>.bib (that entry after the pipeline's own
cleanup, which is what --rescore scores; the citekey is the file name) and
<citekey>.txt (the page text, for checking what was read). --reclean redoes
the cleanup on saved answers without opening Chrome.

Needs what type_search: "ai-mode" needs (src/ai_mode.py). Stops at the first
block page or refused permission rather than saving empties.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "dev"))

import ai_mode  # noqa: E402
import bib_audit  # noqa: E402
import enrich  # noqa: E402
import house_style  # noqa: E402


def query_parts(entry):
    """(author, title) for the prompt, read from an expected entry."""
    return ai_mode.query_parts({x.key: x.value for x in entry.fields})


def clean(entry, library=None):
    """The pipeline's own cleanup (before validation), so that scoring
    compares content rather than form."""
    entry, _ = enrich.strip_forbidden_fields(entry)
    for f in ('url', 'urldate'):           # as ai_mode.answers() does
        entry = enrich.remove_field(entry, f)
    entry, _ = enrich.normalize_langid(entry)
    entry, _ = house_style.normalize(entry)
    entry, _ = house_style.repair(entry, library)
    entry, _ = house_style.snap_to_library(entry, library)
    return entry


def _library():
    import yaml
    cfg = ROOT / "config.yaml"
    path = (yaml.safe_load(cfg.read_text()) or {}).get("library_bib_file") if cfg.exists() else None
    return str(Path(path).expanduser()) if path else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expected", default=str(ROOT / "dev/eval/holdout/expected.bib"))
    ap.add_argument("--only", required=True, help="comma-separated citekeys")
    ap.add_argument("--out", default=str(ROOT / "dev/eval/out/ai-mode"))
    ap.add_argument("--pause", type=float, default=20.0, help="seconds between items")
    ap.add_argument("--reclean", action="store_true",
                    help="no Chrome: clean the saved <key>.raw.bib answers again into <key>.bib")
    args = ap.parse_args(argv)
    library = _library()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = [k for k in args.only.split(",") if k]
    if args.reclean:
        for key in keys:
            raw = out / f"{key}.raw.bib"
            if raw.exists():
                (out / f"{key}.bib").write_text(clean(raw.read_text(encoding="utf-8"), library) + "\n",
                                                encoding="utf-8")
                print(f"{key}: cleaned", file=sys.stderr)
            else:
                print(f"{key}: no {raw.name}", file=sys.stderr)
        return 0
    expected = {e.citekey: e for e in bib_audit.scan(Path(args.expected).read_text(encoding="utf-8"))[0]}
    for n, key in enumerate(keys):
        if key not in expected:
            print(f"{key}: not in {args.expected}", file=sys.stderr)
            continue
        author, title = query_parts(expected[key])
        print(f"{key}: {author} - {title}", file=sys.stderr)
        status, entry, text = ai_mode.ask(author, title, log=lambda m: print(m, file=sys.stderr))
        (out / f"{key}.txt").write_text(text or "", encoding="utf-8")
        if entry:
            (out / f"{key}.raw.bib").write_text(entry + "\n", encoding="utf-8")
            (out / f"{key}.bib").write_text(clean(entry, library) + "\n", encoding="utf-8")
        print(f"  {status}" + (f": {entry.splitlines()[0]}" if entry else ""), file=sys.stderr)
        if status in ("blocked", "refused"):
            print(f"Stopped: {status}. See {out / (key + '.txt')}.", file=sys.stderr)
            return 2
        if n + 1 < len(keys):
            time.sleep(args.pause)
    return 0


if __name__ == "__main__":
    sys.exit(main())
