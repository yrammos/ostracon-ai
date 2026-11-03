# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This repository is a pipeline that extracts bibliographic data from academic sources — PDFs, and `.webloc` bookmarks to online-only publications — into BibLaTeX entries in the **biblatex-chicago** style (notes and bibliography, not author-date).

## Where the extraction rules live

This file is for developing the repository; the extracting model never sees it.

- **`prompt-context/extraction-rules.md`** — the rules the extracting model follows: generic biblatex-chicago guidance and this project's default presentation, including the tier table for settling what the package does versus what the project prefers. Read it before any work on extraction, scoring or the `.bib` tools. It is the first file in the cached prompt prefix.
- **A private house-style file**, `~/.config/ostracon-ai/house-style.md` (or `house_style_file` in `config.yaml`), loaded only if present and sent after the rules with precedence over them. It holds one user's own conventions: their preamble, the languages they load, fields they require. It lives outside the repository and is never committed.

**This repository is public. Never commit personal conventions, private paths or library data.** A rule specific to one user belongs in their house-style file, or in their `config.yaml` where code needs it (`require_langid`); a rule specific to one user's library belongs in their own tooling, layered on `bib_validate.validate()`. Files in this repository name no one's machine: no home-directory paths, no DEVONthink or BibDesk attachment data (`dev/eval/select_sample.py` strips them from `expected.bib`).

### The rules that are executable rather than written down

Much of what this project has learned about how entries go wrong is not prose in
this file but **rules in `dev/bib_audit.py`**, which is the right home for it:
they are checkable, they run in a second over the whole library, and they cannot
drift out of step with themselves. Consult them the way you would consult a
checklist — `python3 dev/bib_audit.py <file>` prints a count per rule.

They cover, among others: a value sitting in the wrong field (a page range in
`Volume`, a bare number in `Issuetitle`); text doubled by a botched import;
values truncated at an unclosed parenthesis; bibstring names written as commands
(`\reviewof{}`); typographic ligatures (U+FB03 for "ffi"); and `Shorttitle`
verdicts. Three programs, split by how they act:

- **`dev/bib_audit.py`** — read-only counting, and the home of the **shared
  predicates**. `bib_normalize.py` imports them; never reimplement one in the
  audit, or the two will disagree and the audit will be the wrong one.
- **`dev/bib_normalize.py`** — edits derived from a rule that fires across the
  corpus.
- **`dev/bib_apply.py`** — a named JSON edit list, for what judgement rather
  than rule has to settle.

All three write nothing without ten gates passing. `dev/normalization-plan.md`
records the design; the entry-level history lives outside the repository.


### Operator note: model choice for reference works

*This is guidance for whoever runs the pipeline, not an instruction to the extracting model.*

Reference works — Grove Music Online, the Stanford Encyclopedia, Wikipedia and the like — are the hardest sources here, because a correct entry has to put the *work* in `Title` and the *article* in `Lista`, add `Entrysubtype = {online}` where there is no print counterpart, and prefer a stated revision date in `Urldate` qualified by `Userd` over a bare access date. Comparison runs on `plato.stanford.edu` and Grove showed `claude-sonnet-4-6` getting parts of this wrong (article name in `Title`) and `claude-sonnet-5` fabricating a publisher and a date, while `claude-opus-5` produced the full correct form including the `Urldate`/`Userd` revision-date pairing.

For a batch of reference works, run with `--model claude-opus-5`; the flag overrides `config.yaml` for that invocation only. It roughly doubles the cost — Opus prices at $5/$25 per 1M tokens against Sonnet's $3/$15, and its tokenizer makes the cached prefix about 27% larger — so it is worth reaching for on awkward sources rather than by default. `dev/estimate_cost.py --model claude-opus-5` prices it before you commit.

## Autonomous sessions

These rules govern any session where the maintainer is not watching. They exist
because this project has produced, in a single week, four separate faults of one
shape: a failure that reported success. An empty tab list read as an empty
browser; a refused permission read as no matching tab; an unparsable line skipped
by a bare `continue`; a marker regex that never matched and silently disabled
every regex behind it. In each case the tests passed and the run said Complete.

Treat a confident completion report as the thing most likely to be wrong.

### Never, without asking

- **Merge anything.** Open PRs; the maintainer merges.
- **Edit `dev/eval/expected.bib`.** It is ground truth. Everything else in the
  repository is measured against it, which makes a plausible-looking error here
  both the most costly and the least visible kind. If an entry appears wrong,
  report it with the citekey and the evidence, and stop.
- **Edit anything under `prompt-context/`, or a house-style file.** These form the cached
  prefix; a change silently alters extraction behaviour for every subsequent run
  and invalidates comparison against the current baseline. Propose the wording
  and wait.
- **Spend more than 10 live API calls in one session.** Measure offline where
  the change allows it (saved outputs, fixtures). A full 61-entry run costs real
  money and needs asking for.
- **Claim a GUI-dependent thing works.** BibDesk colouring, Safari and Chrome tab
  capture, the Quick Action, Apple Events permissions: none is visible from a
  terminal. Say what you changed, name the check the maintainer should run, and
  stop there.

### Always

- **Branch per issue**, named `issue-N-slug`, off `main`. Small commits.
  `Closes #N` in the PR body, not in a commit message on a branch that may be
  rebased.
- **Run `python3 dev/eval/test_eval.py` and `python dev/test_setup.py` before
  every commit.** Plus the web-source suite when `src/web_source.py` is touched.
- **`--rescore` measures the harness, not the pipeline.** It re-scores the
  saved output in `dev/eval/last-run/` against `expected.bib`, so it reflects
  changes to `scorer.py`, `run.py`'s parsing or `expected.bib`, and nothing
  else: not a prompt change, and not post-processing such as `clean_bibtex()`
  or `missing_fields()`. Run it after touching the harness or ground truth. For
  a change to extraction, say in the PR body that `--rescore` cannot see it;
  only a live run can, and that needs asking for. A live run that moves fewer
  than about two entries in 61 is within noise: report it as unchanged rather
  than as an improvement. One entry flipped between the first two baseline
  runs with no attributable cause.
- **Quote values from the file, not from memory.** A citekey, field value,
  count, score or behaviour stated in a PR, issue or commit is read from its
  source in the same session; anything stated from recall is marked as
  recalled. A confidently wrong value looks exactly like a correct one.
- **Distinguish what was tested from what was assumed.** A stub encodes the
  format you expected, not the format the system emits — which is exactly how
  the AppleScript terminology collision survived a round of testing. When a test
  passes against the broken code too, say so.
- **State provenance for fixtures.** Recorded from the maintainer's real data, or
  constructed from documented grammar? Both are legitimate; conflating them is
  not.
- **Correct your own earlier claims when they turn out to be wrong**, in a commit
  and in the issue or PR that carries them. Two commit messages this week
  overstated a fix's scope and would have sent the maintainer re-checking a
  library that was never affected.

### When something fails

Trace before proposing. Report where control actually goes, then stop. Do not
change a mechanism on the assumption that it ran: three of this week's faults
looked like the wrong logic and were in fact code that never executed.

If a diagnosis rests on something unobservable from here — a permission, a window,
a process start time — say so and name what would settle it.

### On the queue

Implementing what is in front of you is the easy part. If the issue seems wrong —
badly scoped, already obsolete, or resting on a premise the baseline contradicts
— say so instead of building it. #7 was correctly closed unbuilt, on the grounds
that the measurement it proposed was below the noise floor of the instrument.
That judgement was worth more than the implementation would have been.
