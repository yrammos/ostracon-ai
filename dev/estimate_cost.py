#!/usr/bin/env python3
"""
Measure what a run currently costs, from the real assembled prompt.

The README's cost figures go stale whenever the context files, the prompt, the
model, or Anthropic's pricing change - and a stale figure fails silently, since
nothing checks it. This script derives them instead: it builds the same static
context block and the same extraction prompt that biblio_agent.py sends, counts
their tokens with the API's own tokenizer, and prints the current numbers.

Usage:
    python3 dev/estimate_cost.py                 # summary for the configured model
    python3 dev/estimate_cost.py --markdown      # paste-ready tables for the README
    python3 dev/estimate_cost.py --model claude-opus-5    # price a different model
    python3 dev/estimate_cost.py --no-api        # rough estimate, no network/key

Token counting uses /v1/messages/count_tokens, which is free and consumes no
rate-limit quota - but it does need a valid API key, so --no-api falls back to a
characters-per-token ratio. That ratio is materially wrong for BibTeX (which is
punctuation-dense) and is only there for a quick offline sanity check.
"""
import argparse
import sys
from pathlib import Path

# biblio_agent lives in src/ and this script in dev/, so it is not importable
# by default. Prepend rather than append: a stray biblio_agent.py on the
# PYTHONPATH should not win over the one this repository ships.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import biblio_agent as ba

# USD per 1M tokens, (input, output). Update from
# https://platform.claude.com/docs/en/pricing - there is no pricing API to read
# these from, so this table is the one thing here that must be maintained by hand.
PRICING = {
    'claude-fable-5':    (10.00, 50.00),
    'claude-opus-5':      (5.00, 25.00),
    'claude-opus-4-8':    (5.00, 25.00),
    'claude-opus-4-7':    (5.00, 25.00),
    'claude-opus-4-6':    (5.00, 25.00),
    'claude-sonnet-5':    (3.00, 15.00),   # $2/$10 introductory through 2026-08-31
    'claude-sonnet-4-6':  (3.00, 15.00),
    'claude-haiku-4-5':   (1.00,  5.00),
}

# Prompt-cache multipliers against the base input rate.
CACHE_WRITE = {'5m': 1.25, '1h': 2.00}
CACHE_READ = 0.10

# Output lengths can't be measured without actually spending tokens, so these are
# observed averages. A populated BibLaTeX entry runs 400-500 tokens; the grounding
# audit is capped at max_tokens=150 by the caller and returns one short line.
OUTPUT_TOKENS = {'extraction': 450, 'audit': 60, 'merge': 400}

# Fallback only, for --no-api. Measured at 3.38 for this project's context, but it
# varies enough by content that the API count is always preferable.
CHARS_PER_TOKEN = 3.38

# A source excerpt of the size extract_pages.py actually produces (~450 words of
# front matter plus ~150 of back matter), so the dynamic half of the prompt is
# measured at a realistic length rather than guessed.
SAMPLE_SOURCE = (
    "--- BEGINNING (450 words) ---\n"
    + "lorem ipsum dolor sit amet consectetur adipiscing elit sed do " * 64
    + "\n--- END (150 words) ---\n"
    + "concluding remarks bibliography and index material " * 30
)

SAMPLE_ENTRY = """@Book{Author2024,
  Author = {Author, Some~A.},
  Title = {A Representative Title},
  Subtitle = {With the Kind of Subtitle These Entries Carry},
  Publisher = {A University Press},
  Location = {Cambridge},
  Date = {2024},
}"""


class _Source:
    """Minimal stand-in for a SourceContent, enough for build_prompt()."""
    label = 'PDF'
    text = SAMPLE_SOURCE
    metadata = {'Title': 'A Representative Title', 'Author': 'Some A. Author'}
    url = None


def build_counter(agent, use_api):
    """Return a text -> token-count function."""
    if not use_api:
        return lambda text: round(len(text) / CHARS_PER_TOKEN)

    model = agent.config['model']

    def count(text):
        return agent.client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}]
        ).input_tokens

    return count


def measure(agent, count):
    """Token counts for every distinct piece of a run's prompts."""
    context = agent.load_context_files()
    static = agent._static_context_block(context) or ""

    components = [
        ('CLAUDE.md', context['claude_md']),
        ('biblio-template.bib', context['template']),
        ('field reference', context['ref']),
    ] + list(context['examples'])

    return {
        'static': count(static),
        'static_chars': len(static),
        'components': [(label, count(text)) for label, text in components if text],
        'extraction': count(agent.build_prompt(_Source())),
        'audit': count(agent._build_audit_prompt(SAMPLE_ENTRY, _Source())),
        # The enrichment and reconciliation prompts restate the entry plus a short
        # list of candidate fields; they are the smallest of the dynamic halves.
        'merge': count(SAMPLE_ENTRY) + 400,
    }


def price(tokens, model, ttl):
    """Per-call costs in USD for the given model and cache tier."""
    if model not in PRICING:
        raise SystemExit(
            f"No pricing on record for {model!r}.\n"
            f"Known: {', '.join(sorted(PRICING))}\n"
            "Add it to PRICING (see https://platform.claude.com/docs/en/pricing)."
        )
    rate_in, rate_out = (r / 1e6 for r in PRICING[model])
    static = tokens['static']

    write = static * rate_in * CACHE_WRITE[ttl]
    read = static * rate_in * CACHE_READ

    def call(dynamic, output, cached):
        prefix = 0.0 if cached is None else (write if cached == 'write' else read)
        return prefix + dynamic * rate_in + output * rate_out

    return {
        'write': write,
        'read': read,
        # The first cached call in a run pays the write; every later one reads.
        'extraction_first': call(tokens['extraction'], OUTPUT_TOKENS['extraction'], 'write'),
        'extraction_later': call(tokens['extraction'], OUTPUT_TOKENS['extraction'], 'read'),
        # The grounding audit sends a bare prompt - it does not use the cached prefix.
        'audit': call(tokens['audit'], OUTPUT_TOKENS['audit'], None),
        'merge': call(tokens['merge'], OUTPUT_TOKENS['merge'], 'read'),
    }


def per_file(costs, all_calls=False):
    """Cost of the first and of each subsequent file in one run."""
    extra = 2 * costs['merge'] if all_calls else 0.0
    return (
        costs['extraction_first'] + costs['audit'] + extra,
        costs['extraction_later'] + costs['audit'] + extra,
    )


def run_total(costs, files, warm):
    """One run of `files` files, best case. `warm` = prefix already cached."""
    first = costs['extraction_later'] if warm else costs['extraction_first']
    best_first, best_later = first + costs['audit'], costs['extraction_later'] + costs['audit']
    return best_first + (files - 1) * best_later


def report(tokens, model, markdown):
    short, long_ = price(tokens, model, '5m'), price(tokens, model, '1h')

    if markdown:
        print(f"The static prefix measures **{tokens['static']:,} tokens**"
              f" ({tokens['static_chars']:,} chars). Writing it costs"
              f" ${short['write']:.2f}; every later call in the same run reads it"
              f" back for ${short['read']:.3f}.\n")
        print("| Call | Runs when | First file | Later files |")
        print("|---|---|---|---|")
        print(f"| Extraction | always | ${short['extraction_first']:.2f} | ${short['extraction_later']:.2f} |")
        print(f"| Grounding audit | `enrich_missing_fields: true` (default) | ${short['audit']:.3f} | ${short['audit']:.3f} |")
        print(f"| Enrichment merge | required/desired fields missing | ${short['merge']:.2f} | ${short['merge']:.2f} |")
        print(f"| Reconciliation | a CrossRef match strictly completes a value | ${short['merge']:.2f} | ${short['merge']:.2f} |")
        b_first, b_later = per_file(short)
        w_first, w_later = per_file(short, all_calls=True)
        print(f"\nA clean source costs **~${b_first:.2f} for the first file in a run"
              f" and ~${b_later:.2f} for each one after**; if all four calls fire,"
              f" **~${w_first:.2f} and ~${w_later:.2f}**.\n")
        print("| Usage pattern | 5-minute TTL | 1-hour TTL |")
        print("|---|---|---|")
        for label, s, l in _scenarios(short, long_):
            print(f"| {label} | ${s:.2f} | ${l:.2f} |")
        return

    print(f"model: {model}   (${PRICING[model][0]:.2f}/${PRICING[model][1]:.2f} per 1M in/out)\n")
    print(f"STATIC PREFIX  {tokens['static']:>8,} tokens"
          f"   ({tokens['static_chars']:,} chars,"
          f" {tokens['static_chars']/max(tokens['static'],1):.2f} chars/token)")
    for label, n in tokens['components']:
        print(f"    {label[:38]:<40}{n:>8,}")
    print(f"\nDYNAMIC        extraction {tokens['extraction']:>6,} |"
          f" audit {tokens['audit']:>5,} | merge {tokens['merge']:>5,} tokens")

    print(f"\nCACHE          write 5m ${short['write']:.4f}"
          f"   write 1h ${long_['write']:.4f}"
          f"   read ${short['read']:.4f}")
    print(f"\nPER CALL       extraction  first ${short['extraction_first']:.3f}"
          f" / later ${short['extraction_later']:.3f}")
    print(f"               audit       ${short['audit']:.3f}   (uncached)")
    print(f"               merge       ${short['merge']:.3f}   (x2 if enrich + reconcile)")

    b_first, b_later = per_file(short)
    w_first, w_later = per_file(short, all_calls=True)
    print(f"\nPER FILE       best  first ${b_first:.2f}  later ${b_later:.2f}")
    print(f"               worst first ${w_first:.2f}  later ${w_later:.2f}")

    print("\nCACHE TTL      (best case, extraction + audit only)")
    for label, s, l in _scenarios(short, long_):
        cheaper = "1h" if l < s else "5m"
        print(f"    {label:<42} 5m ${s:>6.2f}   1h ${l:>6.2f}   -> {cheaper}")


def _scenarios(short, long_):
    """The batching patterns that decide which cache tier is cheaper."""
    return [
        ("One batch of 10, nothing else that hour",
         run_total(short, 10, warm=False),
         run_total(long_, 10, warm=False)),
        ("Two batches of 5, 20 minutes apart",
         2 * run_total(short, 5, warm=False),
         run_total(long_, 5, warm=False) + run_total(long_, 5, warm=True)),
        ("10 single-file invocations across an hour",
         10 * run_total(short, 1, warm=False),
         run_total(long_, 1, warm=False) + 9 * run_total(long_, 1, warm=True)),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default=str(ba.PROJECT_ROOT / 'config.yaml'))
    ap.add_argument('--model', help='price a model other than the configured one')
    ap.add_argument('--markdown', action='store_true', help='emit README-ready tables')
    ap.add_argument('--no-api', action='store_true',
                    help='estimate from a chars/token ratio instead of counting')
    args = ap.parse_args()

    if args.no_api:
        # Skip BiblioAgent.__init__, which would construct an API client.
        agent = object.__new__(ba.BiblioAgent)
        agent.config = agent.load_config(args.config)
        agent._progress_callback = None
    else:
        agent = ba.BiblioAgent(args.config)

    tokens = measure(agent, build_counter(agent, use_api=not args.no_api))
    report(tokens, args.model or agent.config['model'], args.markdown)

    if args.no_api:
        print(f"\n(estimated at {CHARS_PER_TOKEN} chars/token; run without --no-api"
              " to count exactly)", file=sys.stderr)


if __name__ == '__main__':
    main()
