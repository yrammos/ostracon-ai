#!/usr/bin/env python3
"""Decide which BibDesk entries need enrichment, by reading the saved .bib.

Parsing is NOT done here. It is delegated to bib_audit alongside, whose scanner
is hardened by this project's ten gates: scan() yields Entry objects and treats
@comment/@preamble/@string as opaque spans, Entry.get() fetches a field by
lowercased key, and bibtool_errors() runs an independent parser. Re-implementing
any of that was a mistake worth not repeating -- a hand-rolled non-greedy regex
silently truncated 85 entries whose filenames contain braces before the
brace-counting was got right.

This lives here rather than in ostracon-git because it acts on the BibDesk
database, not on the git mirror; nrmlz and strip-bibdesk.rsc, which produce the
mirror, stay there. The move also made the bib_audit import local.

Emits, for nrch:

    STAMP   <max Date-Modified seen, or empty>
    COUNTS  <changed> <deficit> <entries>
    PENDING <comma-separated cite keys with a deficit>
    KEY     <cite key>                       ... one per line

or with --parse-check, a single line:

    PARSE   clean | <n> error(s) | unavailable

SCOPE IS THE UNION OF TWO PREDICATES, because each is blind where the other sees.

  A. changed since the stamp -- Date-Modified later than the value passed in.
     The ONLY thing that catches an attachment swapped for a different file: the
     counts stay equal while the stored URI silently goes wrong.

  B. deficit -- more Bdsk-File-N attachments than Local-Url* or Devonthink*
     values. This is what makes the design self-healing: lose the stamp, or hand
     it a wrong one, and genuine deficits are still found.

No stamp means A matches everything, which is exactly a full audit -- so --audit
needs no separate code path, it just declines to pass a stamp.

WHY Bdsk-File AND NOT Bdsk-Url anchors the attachment count: only Bdsk-File-N
holds the Base64 plist bookmark for an attached file. Bdsk-Url-N is a plain URI
string BibDesk generates from Url, Doi, Citeseerurl and Devonthink*, so a DOI
alone mints one -- 26 entries here have a Doi and no attachment whatever.

THE DEVONTHINK HALF OF B IS A HEURISTIC, NOT A CRITERION. Local-Url tracks the
attachment count exactly (5,747 of 5,747 entries agree). Devonthink does not: it
is hand-maintained, and one item may map to several DEVONthink records, so 489
entries legitimately hold more URIs than attachments. Hence `>` and never `!=`.
It follows that this cannot detect an entry whose counts agree while the URI
points at the wrong record; only re-resolving path -> UUID can, which is --audit.
"""

import argparse
import os
import re
import sys
from datetime import datetime

# Sibling module. sys.path already carries this directory when the script is run
# directly, but nrch invokes it by absolute path from AppleScript, where the
# interpreter's idea of "here" is the caller's working directory instead.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import scan, bibtool_errors  # noqa: E402

# BibDesk writes "2026-08-05 22:58:13 +0300".
STAMP_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*([+-]\d{4})\s*$")

# Field families, matched against Field.key which bib_audit has lowercased.
ATTACHMENT = re.compile(r"^bdsk-file(-\d+)?$")
LOCAL_URL = re.compile(r"^local-url(-\d+)?$")
DEVONTHINK = re.compile(r"^devonthink\d*$")


def parse_stamp(s):
    """Offset-aware, deliberately. The corpus carries +0300, +0200 and +0100, so
    comparing these as plain strings can invert by up to an hour across a DST
    change and quietly skip an entry."""
    if not s:
        return None
    m = STAMP_RE.match(s)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}",
                                 "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def count_matching(entry, pattern):
    return sum(1 for f in entry.fields if pattern.match(f.key))


def scan_bib(path, stamp_str):
    stamp = parse_stamp(stamp_str)
    text = open(path, encoding="utf-8", errors="replace").read()
    entries, _opaque = scan(text)          # opaque = @comment/@string, not ours

    keys, pending = [], []
    changed = deficit = 0
    max_seen = None

    for e in entries:
        dm_field = e.get("date-modified")
        dm = parse_stamp(dm_field.value) if dm_field else None
        if dm and (max_seen is None or dm > max_seen):
            max_seen = dm

        attachments = count_matching(e, ATTACHMENT)
        is_deficit = (attachments > count_matching(e, LOCAL_URL)
                      or attachments > count_matching(e, DEVONTHINK))
        # No stamp => everything is "changed", which is the audit.
        is_changed = stamp is None or (dm is not None and dm > stamp)

        if is_deficit:
            deficit += 1
            pending.append(e.citekey)
        if is_changed:
            changed += 1
        if is_deficit or is_changed:
            keys.append(e.citekey)

    return {"keys": keys,
            "stamp": max_seen.strftime("%Y-%m-%d %H:%M:%S %z") if max_seen else "",
            "changed": changed, "deficit": deficit,
            "pending": pending, "entries": len(entries)}


def emit(r):
    """Tab-separated, one record per line. AppleScript parses this with text item
    delimiters in a few lines; JSON would need a parser it does not have. Cite
    keys are BibTeX tokens, so they cannot contain a tab or newline."""
    out = [f"STAMP\t{r['stamp']}",
           f"COUNTS\t{r['changed']}\t{r['deficit']}\t{r['entries']}",
           f"PENDING\t{','.join(r['pending'])}"]
    out += [f"KEY\t{k}" for k in r["keys"]]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="scope decisions for nrch")
    ap.add_argument("path", help="the .bib to scan")
    ap.add_argument("--stamp", default="",
                    help="max Date-Modified from the last clean run; omit for a full audit")
    ap.add_argument("--parse-check", action="store_true",
                    help="run bib_audit.bibtool_errors and report, scanning nothing")
    args = ap.parse_args()

    try:
        if args.parse_check:
            errs = bibtool_errors(args.path)
            if errs is None:
                sys.stdout.write("PARSE\tunavailable (bibtool not installed)")
            elif errs:
                sys.stdout.write(f"PARSE\t{len(errs)} error(s)")
            else:
                sys.stdout.write("PARSE\tclean")
        else:
            sys.stdout.write(emit(scan_bib(args.path, args.stamp.strip())))
    except OSError as e:
        sys.stdout.write(f"ERROR\t{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
