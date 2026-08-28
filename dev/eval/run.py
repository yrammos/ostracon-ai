#!/usr/bin/env python3
"""
Evaluation harness: run the extraction pipeline over dev/eval/sample/ and
score the result against dev/eval/expected.bib, field by field.

    python3 dev/eval/run.py                       # score the real sample
    python3 dev/eval/run.py --model claude-opus-5  # score with a different model

See dev/eval/README.md for the sample/manifest.json and expected.bib format,
and dev/eval/test_eval.py for a self-contained run against a synthetic
fixture pair (no sample data, no API calls) that exercises this same code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'dev'))

import bib_audit  # noqa: E402
from scorer import score_entry, format_report  # noqa: E402

DEFAULT_SAMPLE_DIR = ROOT / 'dev' / 'eval' / 'sample'
DEFAULT_EXPECTED = ROOT / 'dev' / 'eval' / 'expected.bib'


def load_bib(path) -> dict:
    """citekey -> Entry, for every entry in a .bib file."""
    text = Path(path).read_text(encoding='utf-8')
    entries, _ = bib_audit.scan(text)
    return {e.citekey: e for e in entries}


def load_manifest(sample_dir) -> list:
    """The list of {citekey, source, sha256?, note?} dicts describing the
    sample - see dev/eval/README.md. Empty (not an error) when the sample
    hasn't been populated yet."""
    manifest_path = Path(sample_dir) / 'manifest.json'
    if not manifest_path.exists():
        return []
    return json.loads(manifest_path.read_text(encoding='utf-8'))


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_pipeline(agent, source_path) -> "bib_audit.Entry | None":
    """Run one source through the real pipeline and parse its single output
    entry. None if extraction failed or produced nothing that parses."""
    bibtex_entry = agent.extract_bibtex(source_path)
    if bibtex_entry.startswith("Error:"):
        return None
    clean = agent.clean_bibtex(bibtex_entry)
    entries, _ = bib_audit.scan(clean)
    return entries[0] if entries else None


def evaluate(manifest, sample_dir, expected_entries, run_one):
    """Score every manifest item whose source file and matching expected
    entry both exist.

    run_one(source_path) -> Entry|None does the actual extraction. Injected
    rather than called directly so this loop needs nothing about how
    extraction works - test_eval.py passes a canned lookup instead of a real
    agent, exercising the exact same matching, hashing and scoring logic
    without touching the network or the API.

    Returns (entry_scores, warnings): warnings covers a manifest item with
    no matching expected.bib entry, a source file that's gone missing, or a
    source whose content has changed since the manifest recorded its hash -
    none of these stop the run, since the point of the harness is to report
    what it can score, not to be as fragile as its input data.
    """
    sample_dir = Path(sample_dir)
    entry_scores = []
    warnings = []

    for item in manifest:
        citekey = item['citekey']
        source = sample_dir / item['source']

        expected_entry = expected_entries.get(citekey)
        if expected_entry is None:
            warnings.append(f"{citekey}: no matching entry in expected.bib")
            continue
        if not source.exists():
            warnings.append(f"{citekey}: source {source} not found")
            continue

        recorded_hash = item.get('sha256')
        if recorded_hash and sha256_of(source) != recorded_hash:
            warnings.append(
                f"{citekey}: {source.name} has changed since the manifest "
                f"recorded its hash - re-verify and update manifest.json"
            )

        produced_entry = run_one(source)
        entry_scores.append(score_entry(expected_entry, produced_entry))

    return entry_scores, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sample-dir', default=str(DEFAULT_SAMPLE_DIR),
                         help='Directory holding manifest.json and the source files (default: dev/eval/sample/)')
    parser.add_argument('--expected', default=str(DEFAULT_EXPECTED),
                         help='Hand-verified .bib to score against (default: dev/eval/expected.bib)')
    parser.add_argument('--config', default='config.yaml',
                         help='Path to config file (default: config.yaml)')
    parser.add_argument('--model', help='Claude model to use (overrides config)')
    args = parser.parse_args(argv)

    sample_dir = Path(args.sample_dir)
    manifest = load_manifest(sample_dir)
    if not manifest:
        print(f"No manifest entries in {sample_dir / 'manifest.json'} - nothing to score.\n"
              f"See dev/eval/README.md for the sample format.", file=sys.stderr)
        return 1

    expected_path = Path(args.expected)
    if not expected_path.exists() or not expected_path.read_text(encoding='utf-8').strip():
        print(f"{expected_path} is empty - nothing to score against.\n"
              f"See dev/eval/README.md for its format.", file=sys.stderr)
        return 1
    expected_entries = load_bib(expected_path)

    sys.path.insert(0, str(ROOT / 'src'))
    import biblio_agent  # noqa: E402 - imported here, not at module load, so
    # test_eval.py can import this module and drive evaluate() directly
    # without needing a config.yaml or the anthropic package at all.

    agent = biblio_agent.BiblioAgent(args.config)
    if args.model:
        agent.config['model'] = args.model

    entry_scores, warnings = evaluate(
        manifest, sample_dir, expected_entries,
        run_one=lambda source: run_pipeline(agent, source),
    )

    print(format_report(entry_scores, warnings))
    return 0


if __name__ == '__main__':
    sys.exit(main())
