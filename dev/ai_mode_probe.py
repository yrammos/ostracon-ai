#!/usr/bin/env python3
"""Proof of concept: ask Google Search's AI Mode for a biblatex-chicago entry,
through Google Chrome, and save the answers for scoring. No API key, no
Claude: Chrome is driven by AppleScript and reads the answer from the page's
text (document.body.innerText).

    python3 dev/ai_mode_probe.py --only Juslin2001,Konak2016
    python3 dev/eval/run.py --rescore --sample-dir dev/eval/holdout/sample \\
        --expected dev/eval/holdout/expected.bib --last-run-dir dev/eval/out/ai-mode \\
        --only Juslin2001,Konak2016

Each item's author and title come from the expected entry (the pipeline would
take them from its identity call). The prompt is the maintainer's, from their
Gemini experiment. Results land in --out as <citekey>.raw.bib (the first
@type{...} entry in the answer), <citekey>.bib (that entry after the
pipeline's own cleanup, which is what --rescore scores; the citekey is the
file name) and <citekey>.txt (the page text, for checking what was read).

Needs: Google Chrome running and signed in, and View > Developer > "Allow
JavaScript from Apple Events" switched on; the Terminal (or whatever runs
this) allowed to control Chrome under System Settings > Privacy & Security >
Automation. Stops at the first sign of a block (Google's "unusual traffic"
page, a consent wall) or a refused permission, rather than saving empties.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "dev"))

import bib_audit  # noqa: E402
import enrich  # noqa: E402
import house_style  # noqa: E402
import web_source as ws  # noqa: E402

PROMPT = ('Find the optimal biblatex-chicago type for {author}\'s "{title}". Consider all '
          'available types according to the biblatex-chicago ontology. Then do online research '
          'and populate a biblatex-chicago entry with the optimal fields for this item.')
# AI Mode is Google Search with udm=50 (the parameter its own link uses; not
# documented by Google, so it is a flag).
URL = "https://www.google.com/search?udm=50&q={q}"
BLOCKED = ("unusual traffic", "our systems have detected", "before you continue to google",
           "not a robot")
JS_TEXT = "document.body ? document.body.innerText : ''"


def first_entry(text):
    """The first brace-balanced @type{key, ...} in text, or None."""
    for m in re.finditer(r"@([A-Za-z]+)\s*\{", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                return text[m.start():i + 1]
            i += 1
    return None


def _natural(name):
    family, _, given = name.partition(",")
    return f"{given.strip()} {family.strip()}".strip() if given else family.strip()


def query_parts(entry):
    """(author, title) for the prompt, read from an expected entry."""
    f = {x.key: enrich.strip_latex(x.value).replace("~", " ").strip() for x in entry.fields}
    names = [n.strip() for n in re.split(r"\s+and\s+", f.get("author") or f.get("editor") or "") if n.strip()]
    author = _natural(names[0]) + (" et al." if len(names) > 1 else "") if names else ""
    title = f.get("lista") or f.get("title") or ""
    if f.get("subtitle"):
        title = f"{title}: {f['subtitle']}"
    return author, title


def clean(entry, library=None):
    """The pipeline's own cleanup (extract_bibtex, before validation), so
    that scoring compares content rather than form: forbidden fields, Langid,
    then house_style's normalize, repair and snap_to_library."""
    entry, _ = enrich.strip_forbidden_fields(entry)
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


def page_text(tab_id, timeout=20):
    script = ('tell application "Google Chrome"\n'
              '  repeat with w in windows\n'
              f'    repeat with t in (every tab of w whose id is {int(tab_id)})\n'
              f'      return execute t javascript "{JS_TEXT}"\n'
              '    end repeat\n'
              '  end repeat\n'
              '  return ""\n'
              'end tell')
    return ws._osascript(script, timeout=timeout)


def ask(author, title, log, wait=180, poll=4.0, settle=3):
    """(status, entry, text). status: 'ok', 'no entry', 'blocked', 'refused',
    'error'. Waits until the page text holds an entry and has stopped changing
    for `settle` polls (AI Mode streams its answer)."""
    url = URL.format(q=urllib.parse.quote_plus(PROMPT.format(author=author, title=title)))
    tab, detail = ws.open_in_chrome(url, log=log, wait=30)
    if not tab:
        return ("refused" if "refused" in detail.lower() or "-1743" in detail else "error"), None, detail
    try:
        deadline, last, same, text = time.monotonic() + wait, None, 0, ""
        while time.monotonic() < deadline:
            time.sleep(poll)
            status, text = page_text(tab)
            if status == ws.OSA_REFUSED:
                return "refused", None, text
            if status != ws.OSA_OK:
                continue
            if any(b in text.lower() for b in BLOCKED):
                return "blocked", None, text
            entry = first_entry(text)
            same = same + 1 if entry and text == last else 0
            last = text
            if entry and same >= settle:
                return "ok", entry, text
        entry = first_entry(text or "")
        return ("ok" if entry else "no entry"), entry, text
    finally:
        ws.close_chrome_tab(tab)


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
    expected = {e.citekey: e for e in bib_audit.scan(Path(args.expected).read_text(encoding="utf-8"))[0]}
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
    for n, key in enumerate(keys):
        if key not in expected:
            print(f"{key}: not in {args.expected}", file=sys.stderr)
            continue
        author, title = query_parts(expected[key])
        print(f"{key}: {author} - {title}", file=sys.stderr)
        status, entry, text = ask(author, title, log=lambda m: print(m, file=sys.stderr))
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
