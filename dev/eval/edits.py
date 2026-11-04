#!/usr/bin/env python3
"""Edit count: how much correcting an entry would take, per entry.

Exact-match counts treat every miss alike. This weighs each field by the
work it would cost the maintainer, and penalizes confident error:

    wrong value (different)                 1
    same words, different form              0.5   (name order, case, markup)
    missing field                           1
    spurious field                          1
    wrong value in an entry NOT marked      +0.5  (1.5 in all)
    for review (colour), when known

Reported as median and 90th percentile per entry, with the share of entries
marked for review: a method cannot buy a low edit count by marking all.

    python dev/eval/edits.py dev/eval/out/<run>.json [--run-dir <saved outputs>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))


def _words(v):
    import unicodedata
    import enrich
    t = unicodedata.normalize("NFKD", enrich.strip_latex(v or "").replace("~", " ").lower())
    return set(re.findall(r"\w+", "".join(c for c in t if not unicodedata.combining(c))))


def entry_edits(entry, colored=None):
    """Edit cost of one entry from the run's --json detail."""
    cost = 0.0
    for f in entry["fields"]:
        v = f["verdict"]
        if v == "exact":
            continue
        if v == "different" and _words(f["expected"]) == _words(f["produced"]):
            cost += 0.5
        elif v == "different":
            cost += 1.5 if colored is False else 1.0
        else:
            cost += 1.0
    return cost


def colored_of(run_dir, key):
    """True/False from <run_dir>/<key>.meta.json, or None when not recorded."""
    if not run_dir:
        return None
    p = Path(run_dir) / f"{key}.meta.json"
    if not p.exists():
        return None
    return bool(json.loads(p.read_text()).get("needs_color"))


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def summarize(entries, run_dir=None):
    costs, colored = [], []
    for e in entries:
        c = colored_of(run_dir, e["citekey"])
        colored.append(c)
        costs.append(entry_edits(e, c))
    known = [c for c in colored if c is not None]
    return {"entries": len(costs), "total": sum(costs), "median": _pct(costs, 0.5),
            "p90": _pct(costs, 0.9), "mean": sum(costs) / len(costs) if costs else 0.0,
            "colored_share": (sum(known) / len(known)) if known else None}


def format_summary(s):
    col = "not recorded" if s["colored_share"] is None else f"{s['colored_share']:.0%}"
    return (f"Edit count per entry: median {s['median']:.1f}, 90th percentile {s['p90']:.1f}, "
            f"mean {s['mean']:.2f}, total {s['total']:.1f} over {s['entries']} entries; "
            f"marked for review: {col}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("--run-dir")
    ap.add_argument("--only", help="comma-separated citekeys")
    args = ap.parse_args(argv)
    entries = json.loads(Path(args.json).read_text())["entries"]
    if args.only:
        keep = set(args.only.split(","))
        entries = [e for e in entries if e["citekey"] in keep]
    print(format_summary(summarize(entries, args.run_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
