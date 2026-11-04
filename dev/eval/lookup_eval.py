#!/usr/bin/env python3
"""Measure the lookup steps (ISBN, cited pages) on saved outputs, offline
except for the lookups themselves.

For each source: the saved entry (<run-dir>/<key>.bib) and Gemini's saved
cited sources (<key>.gemini.json); the source text is extracted again
locally. The ISBN step and the evidence step run as in extract_bibtex(), and
the result is written to <out-dir>/after/<key>.bib beside a copy of the saved
entry in <out-dir>/before/. Both are then scored, with exact counts and edit
counts, and every filled value is judged against expected.bib.

    python dev/eval/lookup_eval.py --run-dir dev/eval/out/last-run-X \\
        --out-dir dev/eval/out/lookups-X --budget 3.8
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'dev'), str(ROOT / 'dev' / 'eval')]


def main(argv=None):
    import biblio_agent
    import enrich
    import isbn_lookup
    import run
    import scorer
    from estimate_cost import usage_cost
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--keys')
    ap.add_argument('--budget', type=float, default=3.8, help='stop starting sources past this Claude cost (USD)')
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args(argv)

    expected = run.load_bib(str(ROOT / 'dev/eval/expected.bib'))
    manifest = {m['citekey']: m for m in json.load(open(ROOT / 'dev/eval/sample/manifest.json'))}
    run_dir, out = Path(args.run_dir), Path(args.out_dir)
    keys = args.keys.split(',') if args.keys else sorted(k for k in expected if (run_dir / f'{k}.bib').exists())
    before, after = out / 'before', out / 'after'
    for d in (before, after):
        d.mkdir(parents=True, exist_ok=True)

    agent = biblio_agent.BiblioAgent(args.config)
    agent.config.update(interactive_ocr=False, verbose=False, evidence_page_chars=10000)
    lock, spent, reports = threading.Lock(), [0.0], {}

    def one(key):
        saved = (run_dir / f'{key}.bib').read_text(encoding='utf-8')
        (before / f'{key}.bib').write_text(saved, encoding='utf-8')
        entry, _ = enrich.isolate_entry(saved)
        report = {}
        with lock:
            over = spent[0] >= args.budget
        if entry is None or over:
            (after / f'{key}.bib').write_text(saved, encoding='utf-8')
            reports[key] = {'skipped': 'budget' if over else 'no entry'}
            return
        src = ROOT / 'dev/eval/sample' / manifest[key]['source']
        ext = biblio_agent.EXTRACTORS[src.suffix.lower()]
        kw = agent._pdf_extractor_kwargs(src) if src.suffix.lower() == '.pdf' else {}
        try:
            content = ext(src, **kw)
        except Exception as e:  # noqa: BLE001
            content = f'{type(e).__name__}: {e}'
        text = '' if isinstance(content, str) else content.text
        etype = enrich.get_entry_type(entry)
        values, report['isbn'] = isbn_lookup.fill(etype, enrich.parse_bibtex_fields(entry), text)
        for f, v in values.items():
            entry = enrich.add_field(entry, f, v)
        report['isbn_filled'] = values
        gpath = run_dir / f'{key}.gemini.json'
        gemini = json.loads(gpath.read_text()) if gpath.exists() else {}
        agent.usage = []
        if gemini.get('sources'):
            entry, _, _ = agent._read_cited_pages(entry, gemini, None, agent.config['model'])
            report['pages'] = gemini.get('pages')
        cost = usage_cost(agent.usage, '1h') if agent.usage else 0.0
        with lock:
            spent[0] += cost
        report['cost'] = cost
        (after / f'{key}.bib').write_text(saved.replace(enrich.isolate_entry(saved)[0], entry), encoding='utf-8')
        reports[key] = report

    # agent.usage is shared; one worker keeps per-source costs honest.
    with ThreadPoolExecutor(1) as ex:
        list(ex.map(one, keys))
    json.dump(reports, open(out / 'reports.json', 'w'), indent=1, ensure_ascii=False, default=str)

    import edits
    for name, d in (('before', before), ('after', after)):
        js = out / f'{name}.json'
        subprocess.run([sys.executable, str(ROOT / 'dev/eval/run.py'), '--rescore', '--last-run-dir', str(d),
                        '--only', ','.join(keys), '--json', str(js)], capture_output=True, text=True, check=True)
        ents = json.load(open(js))['entries']
        c = collections.Counter(f['verdict'] for e in ents for f in e['fields'])
        print(f"{name:6} exact {c['exact']}, different {c['different']}, missing {c['missing']}, "
              f"spurious {c['spurious']} | {edits.format_summary(edits.summarize(ents))}")

    judged = collections.Counter()
    by_field = collections.defaultdict(collections.Counter)
    for key, r in reports.items():
        filled = dict(r.get('isbn_filled') or {})
        for f, (v, _d) in ((r.get('pages') or {}).get('filled') or {}).items():
            filled[f] = v
        exp = {scorer._canonical(f.key.lower()): f.value for f in expected[key].fields} if key in expected else {}
        for f, v in filled.items():
            ev = exp.get(scorer._canonical(f))
            verdict = ('no expected value' if ev is None else
                       'right' if scorer._normalize(ev) == scorer._normalize(v) else
                       'same words' if edits._words(ev) == edits._words(v) else 'wrong')
            judged[verdict] += 1
            by_field[f][verdict] += 1
    print(f"filled values: {dict(judged)}")
    for f, c in sorted(by_field.items(), key=lambda x: -sum(x[1].values())):
        print(f"  {f:12} {dict(c)}")
    pages = [r.get('pages') or {} for r in reports.values()]
    print(f"pages read {sum(len(p.get('read', [])) for p in pages)}; skipped "
          f"{collections.Counter(why.split(':')[0] for p in pages for _, why in p.get('skipped', []))}; "
          f"ISBN {collections.Counter(str((r.get('isbn') or {}).get('status')).split(':')[0] for r in reports.values())}")
    print(f"Claude cost ${spent[0]:.2f}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
