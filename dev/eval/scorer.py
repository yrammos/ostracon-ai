#!/usr/bin/env python3
"""Field-level comparison of one pipeline-produced entry against one
hand-verified expected entry.

Reuses dev/bib_audit.py's scanner (Entry/Field/scan()) rather than
re-parsing .bib text here, per this project's rule against duplicating a
shared predicate - see CLAUDE.md's "rules that are executable rather than
written down".
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field as dc_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bib_audit  # noqa: E402

VERDICTS = ('exact', 'different', 'missing', 'spurious')

# Fields that can never appear in the pipeline's output, so counting them
# would fill 'missing' (expected side) or 'spurious' (produced side) with
# fields that are correctly absent. Three groups, kept separate because they
# come from different sources and drift differently:
#
#   - CLAUDE.md's suppression list: issn, isbn, keywords, reference,
#     devonthink. CLAUDE.md is prose, not a file this module can read, so
#     this set has to be kept in step by hand if that list changes. The
#     devonthink\d*, local-url(-\d+)? and bdsk- forms below follow this
#     project's own numbered-attachment convention (dev/bib_audit.py's
#     PROTECTED regex already treats them the same way).
#   - BibDesk bookkeeping: rating, read, date-added, date-modified,
#     local-url (and its numbered siblings for a second/third attachment),
#     and any bdsk-* field BibDesk itself writes (bdsk-file-N, bdsk-url-N).
#   - Free-text notes carried over from BibDesk (abstract, annote): the
#     pipeline never produces either, and in dev/eval/biblio.bib both still
#     carry BibDesk's line-wrap artifact verbatim (see
#     populate_sample.py's _unwrap(), which reconstructs the same wrap in
#     local-url) - scoring them would count formatting the ground truth
#     never claims to have normalized.
_EXCLUDED_FIELD_RE = re.compile(
    r"^(?:issn|isbn|keywords|reference|devonthink\d*"
    r"|rating|read|date-added|date-modified|local-url(?:-\d+)?"
    r"|bdsk-.*"
    r"|abstract|annote)$"
)


def is_excluded_field(key: str) -> bool:
    """True for a field that must never be scored 'missing' or 'spurious' -
    see _EXCLUDED_FIELD_RE above. Case-insensitive on the caller's behalf."""
    return bool(_EXCLUDED_FIELD_RE.match(key.lower()))


@dataclass
class FieldScore:
    field: str            # lowercased field key
    verdict: str           # one of VERDICTS
    expected: str = None
    produced: str = None


@dataclass
class EntryScore:
    citekey: str
    type_expected: str
    type_produced: str | None
    type_correct: bool
    fields: list = dc_field(default_factory=list)


def _normalize(value: str) -> str:
    """Collapse whitespace so line-wrapping or pretty-printing differences
    in the raw .bib text don't register as a content difference."""
    return ' '.join((value or '').split())


def score_entry(expected: bib_audit.Entry, produced: "bib_audit.Entry | None") -> EntryScore:
    """Compare one hand-verified entry against the pipeline's output for the
    same source (matched by citekey by the caller).

    produced=None means extraction produced no usable entry at all (a
    failed run, or output that didn't parse as a .bib entry) - every
    expected field is then 'missing' and the type can't be judged correct.
    """
    type_produced = produced.etype if produced else None
    type_correct = (type_produced == expected.etype)

    # Filtered once, up front, so an excluded field can't leak back in
    # through the spurious loop below.
    expected_fields = [f for f in expected.fields if not is_excluded_field(f.key)]
    produced_fields = [f for f in produced.fields if not is_excluded_field(f.key)] if produced else []

    expected_keys = {f.key for f in expected_fields}
    produced_by_key = {f.key: f for f in produced_fields}
    scores = []

    for f in expected_fields:
        pf = produced_by_key.get(f.key)
        if pf is None:
            scores.append(FieldScore(f.key, 'missing', expected=f.value))
        elif _normalize(pf.value) == _normalize(f.value):
            scores.append(FieldScore(f.key, 'exact', expected=f.value, produced=pf.value))
        else:
            scores.append(FieldScore(f.key, 'different', expected=f.value, produced=pf.value))

    for f in produced_fields:
        if f.key not in expected_keys:
            scores.append(FieldScore(f.key, 'spurious', produced=f.value))

    return EntryScore(
        citekey=expected.citekey,
        type_expected=expected.etype,
        type_produced=type_produced,
        type_correct=type_correct,
        fields=scores,
    )


def aggregate(entry_scores: list) -> dict:
    """Per-field verdict counts across every scored entry, plus entry-type
    accuracy kept separate - a wrong type invalidates everything else the
    entry got right, so it shouldn't be buried inside a field tally."""
    field_counts = Counter()
    for es in entry_scores:
        for fs in es.fields:
            field_counts[(fs.field, fs.verdict)] += 1

    n = len(entry_scores)
    n_type_correct = sum(1 for es in entry_scores if es.type_correct)

    return {
        'n_entries': n,
        'n_type_correct': n_type_correct,
        'type_accuracy': (n_type_correct / n) if n else None,
        'field_counts': field_counts,  # {(field, verdict): count}
    }


def format_report(entry_scores: list, warnings: list = None) -> str:
    """Human-readable report: entry-type accuracy, then a field x verdict
    table, then any per-source warnings (missing source, missing expected
    entry, changed hash)."""
    warnings = warnings or []
    lines = []
    lines.append("=" * 60)
    lines.append(f"Evaluation report — {len(entry_scores)} entr"
                  f"{'y' if len(entry_scores) == 1 else 'ies'} scored")
    lines.append("=" * 60)

    if not entry_scores:
        lines.append("\nNothing scored.")
        if warnings:
            lines.append("\nWarnings:")
            lines.extend(f"  - {w}" for w in warnings)
        return "\n".join(lines)

    agg = aggregate(entry_scores)
    pct = f"{agg['type_accuracy']:.0%}" if agg['type_accuracy'] is not None else "n/a"
    lines.append(f"\nEntry type: {agg['n_type_correct']}/{agg['n_entries']} correct ({pct})")
    for es in entry_scores:
        if not es.type_correct:
            got = es.type_produced or "(no entry produced)"
            lines.append(f"  {es.citekey}: expected '{es.type_expected}', got '{got}'")

    lines.append("\nField-level results:\n")
    fields = sorted({fs.field for es in entry_scores for fs in es.fields})
    name_w = max([len("Field")] + [len(f) for f in fields])
    header = f"{'Field':<{name_w}}  " + "  ".join(f"{v.capitalize():<9}" for v in VERDICTS)
    lines.append(header)
    lines.append("-" * len(header))
    for f in fields:
        counts = [agg['field_counts'].get((f, v), 0) for v in VERDICTS]
        lines.append(f"{f:<{name_w}}  " + "  ".join(f"{c:<9}" for c in counts))

    if warnings:
        lines.append("\nWarnings:")
        lines.extend(f"  - {w}" for w in warnings)

    return "\n".join(lines)
