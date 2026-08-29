#!/usr/bin/env python3
"""
Evaluation harness: run the extraction pipeline over dev/eval/sample/ and
score the result against dev/eval/expected.bib, field by field.

    python3 dev/eval/run.py                       # score the real sample
    python3 dev/eval/run.py --model claude-opus-5  # score with a different model
    python3 dev/eval/run.py --rescore              # re-score dev/eval/last-run/, no API call

Every produced entry is saved to dev/eval/last-run/<citekey>.bib as it's
produced, whether or not it scored well - a live run costs real API calls,
so its output is never just discarded. --rescore reads that directory back
instead of running the pipeline again: re-scoring and re-extracting are
different operations, and only the second needs biblio_agent, config.yaml,
or the anthropic package at all.

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
DEFAULT_LAST_RUN_DIR = ROOT / 'dev' / 'eval' / 'last-run'

# Chicago/babel Langid values seen in this project's .bib files (see
# CLAUDE.md's naming rules - "ngerman" not "german", "norsk" not
# "norwegian"), mapped to the Tesseract language code OCR needs. Used only
# as a per-source hint when a sample source needs OCR at all; unmapped or
# absent Langid falls back to the agent's configured default_ocr_language.
LANGID_TO_TESSERACT = {
    'english': 'eng', 'russian': 'rus', 'ngerman': 'deu', 'german': 'deu',
    'french': 'fra', 'italian': 'ita', 'spanish': 'spa', 'greek': 'ell',
    'polish': 'pol', 'latin': 'lat', 'ukrainian': 'ukr', 'czech': 'ces',
    'magyar': 'hun', 'hungarian': 'hun', 'norsk': 'nor', 'nynorsk': 'nor',
    'dutch': 'nld', 'danish': 'dan',
}


def ocr_language_hint(expected_entry, fallback: str) -> str:
    """Tesseract code to try first if this source needs OCR - from the
    expected entry's own Langid where it's both present and mapped, else
    fallback (the agent's configured default_ocr_language)."""
    langid_field = expected_entry.get('langid') if expected_entry else None
    if langid_field:
        code = LANGID_TO_TESSERACT.get(langid_field.value.strip().lower())
        if code:
            return code
    return fallback


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


def run_pipeline(agent, source_path, citekey=None, save_dir=None) -> "bib_audit.Entry | None":
    """Run one source through the real pipeline and parse its single output
    entry. None if extraction failed or produced nothing that parses.

    Either failure prints the pipeline's own message to stderr - run 1
    reported three blank '(no entry produced)' failures with the cause
    (a missing tesseract language pack) sitting unseen in the return value,
    because nothing printed it. Saves the raw output to
    save_dir/<citekey>.bib unconditionally, success or failure, when both
    are given - see load_last_run()/--rescore.
    """
    label = f"{citekey}: " if citekey else ""
    bibtex_entry = agent.extract_bibtex(source_path)
    if bibtex_entry.startswith("Error:"):
        print(f"   ⚠️  {label}{bibtex_entry}", file=sys.stderr)
        clean = f"% extraction failed: {bibtex_entry}\n"
        entry = None
    else:
        clean = agent.clean_bibtex(bibtex_entry)
        entries, _ = bib_audit.scan(clean)
        entry = entries[0] if entries else None
        if entry is None:
            print(f"   ⚠️  {label}pipeline returned no parseable entry:\n{clean}",
                  file=sys.stderr)

    if save_dir is not None and citekey is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / f"{citekey}.bib").write_text(clean, encoding='utf-8')

    return entry


def load_last_run(save_dir, citekey) -> "bib_audit.Entry | None":
    """Read one entry back from a previous run_pipeline() save. No agent, no
    API - this is what makes --rescore free."""
    path = Path(save_dir) / f"{citekey}.bib"
    if not path.exists():
        return None
    entries, _ = bib_audit.scan(path.read_text(encoding='utf-8'))
    return entries[0] if entries else None


def evaluate(manifest, sample_dir, expected_entries, run_one):
    """Score every manifest item whose source file and matching expected
    entry both exist.

    run_one(citekey, source_path) -> Entry|None does the actual extraction
    (or, under --rescore, reads one back via load_last_run() instead).
    Injected rather than called directly so this loop needs nothing about
    how extraction works - test_eval.py passes a canned lookup instead of a
    real agent, exercising the exact same matching, hashing and scoring
    logic without touching the network or the API.

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

        produced_entry = run_one(citekey, source)
        entry_scores.append(score_entry(expected_entry, produced_entry))

    return entry_scores, warnings


def write_json_report(path, entry_scores, warnings) -> None:
    """Serialize what format_report()'s console table discards: the actual
    expected/produced value behind every 'different' and 'spurious' verdict.
    Pure serialization of EntryScore/FieldScore, already computed by
    evaluate() - no comparison logic here, that stays in scorer.py."""
    payload = {
        "entries": [
            {
                "citekey": es.citekey,
                "type_expected": es.type_expected,
                "type_produced": es.type_produced,
                "type_correct": es.type_correct,
                "fields": [
                    {"field": fs.field, "verdict": fs.verdict,
                     "expected": fs.expected, "produced": fs.produced}
                    for fs in es.fields
                ],
            }
            for es in entry_scores
        ],
        "warnings": warnings,
    }
    Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


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
    parser.add_argument('--json',
                         help='Also write full per-field detail (citekey, verdict, expected, '
                              'produced for every scored field) to this path - the console '
                              'report only carries aggregate counts, and a run is not worth '
                              'repeating just to see a value that scrolled past')
    parser.add_argument('--rescore', action='store_true',
                         help='Score --last-run-dir instead of running the pipeline again - '
                              'no API call, no config.yaml, no anthropic package needed')
    parser.add_argument('--last-run-dir', default=str(DEFAULT_LAST_RUN_DIR),
                         help='Where each produced entry is saved, keyed by citekey, and where '
                              '--rescore reads them back from (default: dev/eval/last-run/)')
    parser.add_argument('--only',
                         help='Comma-separated citekeys: extract/score only these manifest '
                              'items instead of the whole sample - for a targeted diagnostic '
                              'run without paying for the other 50-odd entries')
    args = parser.parse_args(argv)

    sample_dir = Path(args.sample_dir)
    manifest = load_manifest(sample_dir)
    if not manifest:
        print(f"No manifest entries in {sample_dir / 'manifest.json'} - nothing to score.\n"
              f"See dev/eval/README.md for the sample format.", file=sys.stderr)
        return 1

    if args.only:
        wanted = set(args.only.split(','))
        manifest = [item for item in manifest if item['citekey'] in wanted]
        missing = wanted - {item['citekey'] for item in manifest}
        if missing:
            print(f"--only named citekey(s) not in the manifest: {', '.join(sorted(missing))}",
                  file=sys.stderr)
        if not manifest:
            print("--only matched nothing in the manifest.", file=sys.stderr)
            return 1

    expected_path = Path(args.expected)
    if not expected_path.exists() or not expected_path.read_text(encoding='utf-8').strip():
        print(f"{expected_path} is empty - nothing to score against.\n"
              f"See dev/eval/README.md for its format.", file=sys.stderr)
        return 1
    expected_entries = load_bib(expected_path)

    if args.rescore:
        def run_one(citekey, source):
            return load_last_run(args.last_run_dir, citekey)
    else:
        sys.path.insert(0, str(ROOT / 'src'))
        import biblio_agent  # noqa: E402 - imported here, not at module load, so
        # test_eval.py (and --rescore above) can drive evaluate() directly
        # without needing a config.yaml or the anthropic package at all.

        agent = biblio_agent.BiblioAgent(args.config)
        if args.model:
            agent.config['model'] = args.model
        # An eval run has no one at the keyboard to answer the OCR-language
        # dropdown - see interactive_ocr in _pdf_extractor_kwargs(). Each
        # source's own expected Langid stands in for the human's answer.
        agent.config['interactive_ocr'] = False
        base_lang = agent.config.get('default_ocr_language', 'eng')

        def run_one(citekey, source):
            agent.config['default_ocr_language'] = ocr_language_hint(
                expected_entries.get(citekey), base_lang)
            return run_pipeline(agent, source, citekey=citekey, save_dir=args.last_run_dir)

    entry_scores, warnings = evaluate(manifest, sample_dir, expected_entries, run_one=run_one)

    print(format_report(entry_scores, warnings))

    if args.json:
        write_json_report(args.json, entry_scores, warnings)
        print(f"\nFull per-field detail written to {args.json}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
