#!/usr/bin/env python3
"""Is the model's stated confidence in an entry type calibrated?

For a random draw of scored sources, show the model the source and the entry
the pipeline already produced for it (a saved run), and ask for the plausible
entry types with probabilities. Then compare the probability it gives the
produced type with whether that type was correct against expected.bib.

This decides whether a confidence threshold can serve as the stopping rule of
a search loop: it can only if stated confidence tracks accuracy.

No extraction is rerun: one call per source, on the same cached prefix
(extraction rules, template, house style) and the same source text.

    python dev/eval/calibrate.py --n 57 --seed 1 --out dev/eval/out/calibration.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'dev'))
sys.path.insert(0, str(ROOT / 'dev' / 'eval'))

from run import load_bib, load_manifest, ocr_language_hint  # noqa: E402

QUESTION = """Above is the source, and below is the BibLaTeX entry already drafted from it.

<draft_entry>
{entry}
</draft_entry>

Which biblatex-chicago entry types are plausible for this source? Give each
plausible type a probability that it is the correct type under the rules above;
the probabilities sum to 1. Include every type you give 0.05 or more. Judge from
the source; the draft may be wrong. Also name the one question whose answer
would most change these probabilities.

Reply with JSON only:
{{"types": {{"<type>": <probability>, ...}}, "deciding_question": "<question>"}}"""


def _bins(rows, edges=(0.0, 0.5, 0.7, 0.9, 1.01)):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [r for r in rows if lo <= r['p_produced'] < hi]
        if sel:
            out.append((lo, min(hi, 1.0), len(sel), sum(r['correct'] for r in sel),
                        sum(r['p_produced'] for r in sel) / len(sel)))
    return out


def _auc(rows):
    """Probability that a correct entry gets more confidence than a wrong one."""
    pos = [r['p_produced'] for r in rows if r['correct']]
    neg = [r['p_produced'] for r in rows if not r['correct']]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def summarize(rows):
    n = len(rows)
    lines = [f"Sources: {n}; produced type correct: {sum(r['correct'] for r in rows)}/{n}"]
    brier = sum((r['p_produced'] - r['correct']) ** 2 for r in rows) / n
    lines.append(f"Brier score (p of produced type vs correct): {brier:.3f}")
    auc = _auc(rows)
    lines.append(f"Discrimination (AUC): {auc:.2f}" if auc is not None else "AUC: n/a")
    lines.append("Confidence bin    n  correct  mean stated")
    for lo, hi, k, c, mean in _bins(rows):
        lines.append(f"  [{lo:.1f}, {hi:.1f})  {k:4}  {c:4} ({c / k:4.0%})  {mean:.2f}")
    top = sum(r['top_type'] == r['expected'] for r in rows)
    lines.append(f"Top-probability type correct: {top}/{n}; "
                 f"expected type among those listed: {sum(r['p_expected'] > 0 for r in rows)}/{n}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--sample-dir', default=str(ROOT / 'dev/eval/sample'))
    ap.add_argument('--expected', default=str(ROOT / 'dev/eval/expected.bib'))
    ap.add_argument('--run-dir', default=str(ROOT / 'dev/eval/out/last-run-2026-11-03-base172'))
    ap.add_argument('--scores', default=str(ROOT / 'dev/eval/out/2026-11-03-base172.json'))
    ap.add_argument('--n', type=int, default=57)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--out', required=True)
    ap.add_argument('--summarize', action='store_true', help='Only summarize an existing --out file.')
    args = ap.parse_args(argv)

    if args.summarize:
        print(summarize(json.loads(Path(args.out).read_text())['rows']))
        return 0

    import biblio_agent
    import enrich
    scores = {e['citekey']: e for e in json.loads(Path(args.scores).read_text())['entries']}
    expected = load_bib(args.expected)
    manifest = [m for m in load_manifest(args.sample_dir)
                if not m.get('excluded') and m['citekey'] in scores
                and (Path(args.run_dir) / f"{m['citekey']}.bib").exists()]
    draw = random.Random(args.seed).sample(manifest, min(args.n, len(manifest)))

    agent = biblio_agent.BiblioAgent(args.config)
    agent.config['interactive_ocr'] = False
    base_lang = agent.config.get('default_ocr_language', 'eng')
    context = agent.load_context_files()
    rows, usage = [], []
    for i, item in enumerate(draw, 1):
        ck = item['citekey']
        entry = (Path(args.run_dir) / f"{ck}.bib").read_text(encoding='utf-8')
        produced = (enrich.get_entry_type(entry) or '').lower()
        source = Path(args.sample_dir) / item['source']
        agent.config['default_ocr_language'] = ocr_language_hint(expected.get(ck), base_lang)
        suffix = source.suffix.lower()
        kwargs = agent._pdf_extractor_kwargs(source) if suffix == '.pdf' else (
            {'crossref_email': agent.config.get('crossref_email'), 'log': lambda m: None}
            if suffix == '.webloc' else {})
        content = biblio_agent.EXTRACTORS[suffix](source, **kwargs)
        if isinstance(content, str) or not produced:
            print(f"{i:3} {ck}: skipped ({content if isinstance(content, str) else 'no entry'})",
                  file=sys.stderr)
            continue
        meta = "\n".join(f"{k}: {v}" for k, v in (content.metadata or {}).items())
        dynamic = (f"The source is {content.label}.\n\n<source_text>\n{content.text}\n</source_text>\n"
                   + (f"\n<source_metadata>\n{meta}\n</source_metadata>\n" if meta else "")
                   + "\n" + QUESTION.format(entry=entry.strip()))
        agent.usage = []
        msg = agent._create('calibration', 800, model=agent.config['model'],
                            messages=[{"role": "user",
                                       "content": agent._cached_message_content(context, dynamic)}])
        usage += agent.usage
        reply = "".join(b.text for b in msg.content if getattr(b, 'type', '') == 'text')
        m = re.search(r"\{.*\}", reply, re.S)
        try:
            parsed = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            parsed = None
        if not parsed or not isinstance(parsed.get('types'), dict):
            print(f"{i:3} {ck}: unparsable reply: {reply[:200]!r}", file=sys.stderr)
            continue
        types = {k.lower().lstrip('@').strip(): float(v) for k, v in parsed['types'].items()}
        total = sum(types.values()) or 1.0
        types = {k: v / total for k, v in types.items()}
        exp = scores[ck]['type_expected'].lower()
        row = {'citekey': ck, 'produced': produced, 'expected': exp,
               'correct': produced == exp, 'p_produced': types.get(produced, 0.0),
               'p_expected': types.get(exp, 0.0), 'top_type': max(types, key=types.get),
               'types': types, 'deciding_question': parsed.get('deciding_question', '')}
        rows.append(row)
        print(f"{i:3} {ck:28} {'ok ' if row['correct'] else 'BAD'} p={row['p_produced']:.2f} "
              f"{produced} (exp {exp})", flush=True)

    cost_in = {'claude-sonnet-4-6': (3, 15)}.get(agent.config['model'], (3, 15))
    cost = sum(u['input'] * cost_in[0] + u['cache_write'] * cost_in[0] * 2
               + u['cache_read'] * cost_in[0] * 0.1 + u['output'] * cost_in[1] for u in usage) / 1e6
    Path(args.out).write_text(json.dumps({'seed': args.seed, 'model': agent.config['model'],
                                          'run_dir': args.run_dir, 'cost_usd': round(cost, 3),
                                          'rows': rows}, indent=1, ensure_ascii=False))
    print()
    print(summarize(rows))
    print(f"API cost (approx., 1h cache writes at 2x): ${cost:.2f}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
