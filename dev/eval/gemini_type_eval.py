#!/usr/bin/env python3
"""Score Gemini's type decision (src/gemini_search.decide_type) on saved outputs.

Each source's query is built from the title and first author of a saved
extraction (<run-dir>/<key>.bib), as the pipeline would build it; Gemini
decides the type, grounded in Google Search. No Anthropic calls: only Gemini
is billed. Gemini's type is scored against expected.bib beside the saved
extraction's own type.

    python dev/eval/gemini_type_eval.py --run-dir dev/eval/out/last-run-2026-11-04-typestep3 \\
        --keys Zizek2005,Yip2010 --out dev/eval/out/2026-11-04-gemini-type.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'dev'))
sys.path.insert(0, str(ROOT / 'dev' / 'eval'))

import bib_audit  # noqa: E402
from run import load_bib  # noqa: E402

# Gemini 3.8 Flash, paid tier, through 2026-12-31 (aistudio.google.com/docs/pricing,
# read 2026-11-04); thinking tokens bill as output. Searches: 5,000 free a month.
PRICE_IN, PRICE_OUT = 0.75, 3.75


def main(argv=None):
    import api_keys
    import biblio_agent
    import enrich
    import gemini_search
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--keys', help='Comma-separated citekeys; default: every expected entry with a saved output.')
    ap.add_argument('--expected', default=str(ROOT / 'dev/eval/expected.bib'))
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(open(args.config)); api_keys.resolve_api_keys(cfg)
    expected = load_bib(args.expected)
    run_dir = Path(args.run_dir)
    keys = args.keys.split(',') if args.keys else sorted(k for k in expected if (run_dir / f"{k}.bib").exists())

    def one(ck):
        draft = bib_audit.scan((run_dir / f"{ck}.bib").read_text(encoding='utf-8'))[0][0]
        f = {x.key.lower(): x.value for x in draft.fields}
        clean = lambda k: enrich.strip_latex(f.get(k, '')).replace('~', ' ').strip()
        title = ': '.join(x for x in (clean('title'), clean('subtitle')) if x)
        container = next((clean(k) for k in ('booktitle', 'journaltitle', 'journal', 'maintitle') if clean(k)), '')
        author = clean('author') or clean('editor')
        q = gemini_search.type_query(title, author, container)
        r = gemini_search.decide_type(q, biblio_agent.TYPE_DEFINITIONS, cfg.get('gemini_api_key'))
        cost = sum(u.get('promptTokenCount', 0) * PRICE_IN
                   + (u.get('candidatesTokenCount', 0) + u.get('thoughtsTokenCount', 0)) * PRICE_OUT
                   for u in r.get('usage') or []) / 1e6
        exp = expected[ck].etype.lower()
        return {'citekey': ck, 'query': q, 'type_expected': exp, 'type_draft': draft.etype.lower(),
                'type_gemini': r.get('type'), 'found': r.get('found'), 'reason': r.get('reason'),
                'error': r.get('error'), 'searches': len(r.get('queries') or []),
                'sources': [s['title'] for s in r.get('sources') or []], 'cost_usd': round(cost, 5)}

    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(one, keys))
    Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False))

    for e in rows:
        mark = ('FIX ' if e['type_gemini'] == e['type_expected'] != e['type_draft'] else
                'BRK ' if e['type_draft'] == e['type_expected'] != e['type_gemini'] else '    ')
        print(f"{mark}{e['citekey']:18} exp {e['type_expected']:13} draft {e['type_draft']:13} "
              f"gemini {str(e['type_gemini']):13} {e['error'] or ''} | {(e['reason'] or '')[:110]}")
    n = len(rows)
    print(f"\nSources {n}: draft correct {sum(e['type_draft'] == e['type_expected'] for e in rows)}; "
          f"Gemini correct {sum(e['type_gemini'] == e['type_expected'] for e in rows)}; "
          f"Gemini not found {sum(e['found'] is False for e in rows)}; errors {sum(bool(e['error']) for e in rows)}")
    print(f"  if Gemini decides (draft kept where Gemini found nothing): "
          f"{sum((e['type_gemini'] if e['type_gemini'] and not e['error'] else e['type_draft']) == e['type_expected'] for e in rows)}")
    print(f"  Gemini cost ≈ ${sum(e['cost_usd'] for e in rows):.3f}; searches {sum(e['searches'] for e in rows)}")
    print("  errors:", dict(collections.Counter(e['error'] for e in rows if e['error'])) or 'none')
    return 0


if __name__ == '__main__':
    sys.exit(main())
