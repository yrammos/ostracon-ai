#!/usr/bin/env python3
"""Binary-search a .bib file for the entry that crashes BibDesk.

Writes a candidate subset, opens it in BibDesk, and watches whether the
process survives. Halves the range on each round, so ~13 rounds locate one
entry among 5,746.

    python3 dev/bib_bisect.py ~/Documents/Bibdesk/biblio_backup.bib
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import scan  # noqa: E402

WORK = "/private/tmp/claude-501/-Users-rammos-Dev-ostracon-ai/" \
       "bf44bd3c-ae1c-43f6-a8fb-47736ab1150c/scratchpad/bibtest"


def bibdesk_pid():
    r = subprocess.run(["pgrep", "-x", "BibDesk"], capture_output=True, text=True)
    return r.stdout.strip() or None


def quit_bibdesk():
    subprocess.run(["killall", "BibDesk"], capture_output=True)
    for _ in range(20):
        if not bibdesk_pid():
            return
        time.sleep(0.25)


def survives(text: str, tag: str, settle: float = 7.0) -> bool:
    """True if BibDesk is still alive `settle` seconds after opening `text`."""
    path = os.path.join(WORK, f"bisect_{tag}.bib")
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))
    quit_bibdesk()
    subprocess.run(["open", "-a", "BibDesk", path], capture_output=True)

    launched = False
    deadline = time.time() + settle
    while time.time() < deadline:
        if bibdesk_pid():
            launched = True
        elif launched:
            return False              # came up, then died -> crash
        time.sleep(0.25)
    return bool(bibdesk_pid())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--settle", type=float, default=7.0)
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    text = open(args.path, "rb").read().decode("utf-8")
    entries, _ = scan(text)
    print(f"{len(entries)} entries in {args.path}\n")

    def render(sel):
        return "\n\n".join(text[e.span[0]:e.span[1]] for e in sel) + "\n"

    lo, hi = 0, len(entries)          # invariant: entries[lo:hi] crashes
    if survives(render(entries), "full", args.settle):
        print("The full entry set does NOT crash. Nothing to bisect.")
        return 1
    print(f"confirmed: entries[{lo}:{hi}] crashes\n")

    rounds = 0
    while hi - lo > 1:
        rounds += 1
        mid = (lo + hi) // 2
        left = entries[lo:mid]
        tag = f"{lo}_{mid}"
        crashed = not survives(render(left), tag, args.settle)
        print(f"  round {rounds:>2}: [{lo}:{mid}] n={len(left):<5} "
              f"{'CRASH' if crashed else 'ok'}")
        if crashed:
            hi = mid
        else:
            lo = mid

    culprit = entries[lo]
    print(f"\nCULPRIT: entry index {lo} — {culprit.citekey} [{culprit.etype}]")
    print("-" * 70)
    print(text[culprit.span[0]:culprit.span[1]][:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
