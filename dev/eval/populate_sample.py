#!/usr/bin/env python3
"""One-off helper: populate dev/eval/sample/ from a .bib file's BibDesk
attachments. Run by hand, not part of the extraction pipeline.

For each entry in <bib-file>:
  - resolve its source file from local-url (a plain path, preferred when
    present, reconstructed from BibDesk's line-wrap first - see _unwrap())
    or bdsk-file-1 (a base64-encoded macOS bookmark, decoded via the same
    NSURL bookmark API src/biblio_agent.py's add_bdsk_bookmark() used to
    write it - never by reading the bookmark plist's own 'relativePath'
    string, since resolving that needs a base directory this script has no
    reliable way to supply for an arbitrary <bib-file> argument);
  - if the resolved path exists, copy it into --sample-dir named after the
    entry's citekey, keeping the original extension;
  - record {citekey, source, sha256} in manifest.json, per
    dev/eval/sample/README.md's format.

Only the first attachment (local-url / bdsk-file-1) is consulted - a
second or third attachment (local-url-2, bdsk-file-2, ...) is reported,
not used, since the manifest connects one source file to one citekey.

    python3 dev/eval/populate_sample.py <bib-file>
    python3 dev/eval/populate_sample.py <bib-file> --sample-dir dev/eval/sample --dry-run

Uses dev/bib_audit.py's scanner (Entry/Field/scan()) to read the .bib file,
per this project's rule against re-parsing .bib text - see scorer.py.

resolve_attachment()/resolve_attachment_verbose() below are also imported by
select_sample.py, so the decode logic lives here exactly once.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import plistlib
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
import bib_audit  # noqa: E402

DEFAULT_SAMPLE_DIR = ROOT / 'dev' / 'eval' / 'sample'

# sample/README.md documents the sample as .pdf/.webloc sources, and the
# repository's .gitignore only excludes dev/eval/sample/*.pdf and *.webloc -
# so a resolved attachment of any other type (a .docx, a .ppt, ...) would
# both be the wrong kind of source for run.py to feed the pipeline and an
# untracked file the next `git add` could accidentally pick up. Refused
# rather than silently copied.
ALLOWED_SUFFIXES = {'.pdf', '.webloc'}

# NSURLBookmarkResolutionWithoutUI - resolve without prompting the user
# (e.g. to mount a volume). See Apple's Foundation/NSURL.h.
_NSURL_BOOKMARK_RESOLUTION_WITHOUT_UI = 1 << 8

# BibDesk wraps long field values at a fixed column, always breaking on a
# space; the value in the .bib file then carries that break as a literal
# newline plus 18 trailing spaces of indent (still present verbatim in
# abstract/annote - see scorer.py's exclusion list). In local-url the
# newline itself was lost at some point upstream of this file and collapsed
# into a plain space, leaving a run of exactly 19 spaces where the wrap
# fell. Both are the same wrap, reconstructed here rather than "normalized":
# collapsing either form to the single space it replaces is lossless,
# because the wrap always falls on a space.
_WRAP_RE = re.compile(r'\n {18}| {19}')


def _unwrap(value: str) -> str:
    return _WRAP_RE.sub(' ', value)


def resolve_attachment_verbose(entry: "bib_audit.Entry"):
    """Resolve one entry's source file path, with a reason on failure.

    Returns (path: str|None, note: str|None, error: str|None).
    - path is set on success; note carries a non-fatal remark (e.g. a
      stale-but-resolved bookmark, or an ignored second attachment).
    - error is set (path is None) when nothing usable could be resolved,
      naming why - including a resolved path that doesn't exist or whose
      type sample/ doesn't accept (see ALLOWED_SUFFIXES).

    The only place bdsk-file-1 bookmark / local-url decoding happens;
    resolve_attachment() below and select_sample.py's candidate filter both
    go through this function rather than reimplementing it.
    """
    note = None
    if entry.has('local-url-2') or entry.has('bdsk-file-2'):
        note = "entry also carries a second attachment (local-url-2/bdsk-file-2), not used"

    local_url = entry.get('local-url')
    if local_url is not None:
        path = _unwrap(local_url.value).strip()
    else:
        bdsk_file = entry.get('bdsk-file-1')
        if bdsk_file is None:
            return None, note, "no local-url or bdsk-file-1 field"

        try:
            from Foundation import NSURL
        except ImportError:
            return None, note, "pyobjc (Foundation) not available - pip install pyobjc-framework-Cocoa"

        try:
            plist = plistlib.loads(base64.b64decode(bdsk_file.value))
            bookmark_data = plist['bookmark']
        except Exception as e:
            return None, note, f"could not decode bdsk-file-1: {e}"

        resolved_url, stale, err = NSURL.URLByResolvingBookmarkData_options_relativeToURL_bookmarkDataIsStale_error_(
            bookmark_data, _NSURL_BOOKMARK_RESOLUTION_WITHOUT_UI, None, None, None
        )
        if err is not None or resolved_url is None:
            return None, note, f"bookmark resolution failed: {err}"

        path = resolved_url.path()
        if stale:
            note = (note + "; " if note else "") + "bookmark is stale (file may have moved)"

    source_path = Path(path)
    if not source_path.exists():
        return None, note, f"resolved to {source_path}, which does not exist"
    if source_path.suffix.lower() not in ALLOWED_SUFFIXES:
        return None, note, (
            f"resolved to {source_path}, whose type "
            f"({source_path.suffix or '(no extension)'}) sample/ doesn't accept - "
            f"see sample/README.md"
        )
    return path, note, None


def resolve_attachment(entry: "bib_audit.Entry"):
    """Resolve one entry's source file to a live, accepted-type Path, or
    None. Thin wrapper over resolve_attachment_verbose() for callers (this
    module's populate(), and select_sample.py) that only need path-or-None
    and not the failure reason."""
    path, _note, error = resolve_attachment_verbose(entry)
    return Path(path) if path and not error else None


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def populate(bib_path: Path, sample_dir: Path, dry_run: bool):
    """Returns (manifest: list, resolved_type_counts: Counter, failures: list[(citekey, reason)])."""
    text = bib_path.read_text(encoding='utf-8')
    entries, _ = bib_audit.scan(text)

    manifest = []
    resolved_type_counts = Counter()
    failures = []

    for entry in entries:
        citekey = entry.citekey
        if '/' in citekey or '\\' in citekey:
            failures.append((citekey, "citekey contains a path separator, refusing to use it as a filename"))
            continue

        path, note, error = resolve_attachment_verbose(entry)
        if error:
            failures.append((citekey, error))
            continue

        source_path = Path(path)
        dest_name = f"{citekey}{source_path.suffix}"
        dest_path = sample_dir / dest_name

        if not dry_run:
            sample_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)

        manifest.append({
            "citekey": citekey,
            "source": dest_name,
            "sha256": sha256_of(source_path),
        })
        resolved_type_counts[entry.etype] += 1
        if note:
            failures.append((citekey, f"resolved and copied, but: {note}"))

    manifest.sort(key=lambda m: m['citekey'])
    return manifest, resolved_type_counts, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('bib_file', help="Hand-verified .bib file whose entries carry BibDesk attachments")
    parser.add_argument('--sample-dir', default=str(DEFAULT_SAMPLE_DIR),
                         help='Directory to populate (default: dev/eval/sample/)')
    parser.add_argument('--dry-run', action='store_true',
                         help='Resolve and report, but copy nothing and write nothing')
    args = parser.parse_args(argv)

    bib_path = Path(args.bib_file)
    if not bib_path.exists():
        print(f"{bib_path} does not exist.", file=sys.stderr)
        return 1
    sample_dir = Path(args.sample_dir)

    manifest, resolved_type_counts, failures = populate(bib_path, sample_dir, args.dry_run)

    # Non-fatal notes (stale bookmark, ignored second attachment) were
    # folded into `failures` for a single report loop; separate them back
    # out so the summary count only reflects genuine resolution failures.
    notes = [(k, r) for k, r in failures if r.startswith("resolved and copied, but:")]
    hard_failures = [(k, r) for k, r in failures if not r.startswith("resolved and copied, but:")]

    print(f"{'Would resolve' if args.dry_run else 'Resolved'} {len(manifest)} of "
          f"{len(manifest) + len(hard_failures)} entries.")

    if hard_failures:
        print(f"\n{len(hard_failures)} entr{'y' if len(hard_failures) == 1 else 'ies'} could not be resolved:")
        for citekey, reason in hard_failures:
            print(f"  - {citekey}: {reason}")

    if notes:
        print("\nNotes on resolved entries:")
        for citekey, reason in notes:
            print(f"  - {citekey}: {reason}")

    if resolved_type_counts:
        print("\nEntry-type counts among resolved entries:")
        for etype, count in resolved_type_counts.most_common():
            print(f"  {etype:<15} {count}")

    if args.dry_run:
        print("\n--dry-run: nothing copied, nothing written. Manifest that would be written:")
        print(json.dumps(manifest, indent=2))
        return 0

    manifest_path = sample_dir / 'manifest.json'
    if manifest_path.exists():
        target = sample_dir / 'manifest.json.new'
        print(f"\n{manifest_path} already exists - not overwriting it.\n"
              f"Wrote the new manifest to {target} instead; review it and "
              f"`mv` it into place if that's what you want.")
    else:
        target = manifest_path
        sample_dir.mkdir(parents=True, exist_ok=True)

    target.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f"\nWrote {target}.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
