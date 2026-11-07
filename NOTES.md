# Notes

Reasoning, measurements and the developer tooling. Nothing here is needed in order
to use the agent; see [`README.md`](README.md) for that.

- [The cached prefix](#the-cached-prefix)
- [How the cost arises](#how-the-cost-arises)
- [The orchestration](#the-orchestration)
- [Auto-filing: what was measured](#auto-filing-what-was-measured)
- [Developer tooling](#developer-tooling)
- [Third-party material](#third-party-material)

## The cached prefix

The extraction rules, the house style, the template and the field reference form a
static prefix, cached with the provider: a run pays to write it once and reads it
back thereafter.

| component | tokens |
| --- | ---: |
| `biblatex-chicago-notes-ref.md`: condensed field reference | 4,966 |
| `biblio-template.bib`: one worked example per type | 2,590 |
| `extraction-rules.md`: the extraction rules | 11,796 |
| house-style file (`house_style_file`; private, optional) | varies (1,070 when measured) |

**20,540 tokens in total** (69,426 characters) on 2026-11-05, measured by
`dev/estimate_cost.py --no-api` at the project's ratio of 3.38 characters per
token. Without `--no-api` it counts exactly through the API's `count_tokens`,
which requires credit. `extraction-rules.md` and the house-style file are the ones
that change in ordinary use.

Not loaded, but kept for consultation or for `example_files`:

- `notes-test.bib` (the package's annotated test suite, about 46,000 tokens) and
  `cms-notes-intro-guide.md` (entry types by kind of source, about 3,200). They
  taught classification; the closed type list in the extraction prompt replaced
  them on 2026-11-04.
- `biblatex-chicago-fields.md`, §4.2 of the manual: an A/B run found no benefit
  for some 45,000 tokens.

Precedence divides by question, not by file: the package's files decide what
biblatex-chicago supports, while `extraction-rules.md`, the house-style file and
`biblio-template.bib` decide this project's presentation where the package allows
more than one form. `extraction-rules.md` sets out the tiers.

## How the cost arises

The figures are in [`README.md`](README.md#cost). A cache write costs 1.25× the
input rate at the five-minute TTL and 2× at one hour; a read costs 0.1×. The prefix
is written once per run and read by every later call, so cost turns on the number
of cold starts rather than of files: by the 2026-11-05 estimate (best case), ten
single-file runs across an hour cost $1.08 at the five-minute TTL and $0.49 at one
hour.

`dev/estimate_cost.py` holds the per-model rate table, the one thing here
maintained by hand: no pricing API exists to read it from.

## The orchestration

The pipeline, step by step, is in [`README.md`](README.md#how-it-works); the code
is `extract_bibtex()` in `src/biblio_agent.py`. The branching belongs to the
harness around the model calls: the bibliographic judgment happens inside the
Claude calls (identity, extraction, merge, audit, reconciliation, repair) and
Gemini's, and the code only decides which of them run and which value wins.

## Auto-filing: what was measured

With `autofile_bibdesk: true`, `_save_via_bibdesk` calls BibDesk's `auto file` with
no destination, leaving BibDesk to generate the filename from its Local File format.
Its TeX stripping is what produces the damage rather than what prevents it: the
backslash, the command name and the braces are deleted and every brace group's
contents kept, which is right for a one-argument formatting command and wrong for
the two shapes a Chicago library uses constantly.

| in the entry | filed as | |
| --- | --- | --- |
| `\bibstring{reviewof}` | `reviewof` | the argument is a keyword |
| `\foreignlanguage{russian}{X}` | `russianX` | the first argument is a parameter |
| `\mkbibemph{X}` | `X` | correct |

`~`, `\,`, `---` and `--` are not `\word`-shaped, so nothing touches them at all. In
one library roughly one attachment in seven arrived damaged.

All sixteen values of `BDSKLocalFileCleanOptionKey` were measured: none removes the
debris, and value 4 transliterates Cyrillic. The format string makes no difference
either — `%T10`, `%t0` and `%f{Title}` render identically. The only remedy is a
`Did Auto File` script hook rewriting the name after BibDesk has chosen it, which is
configuration of BibDesk rather than of this agent, and so lives outside this
repository.

## Developer tooling

Nothing in `dev/` runs during extraction.

| | |
| --- | --- |
| `ab_compare.py` | Scores two extraction runs against curated ground truth. |
| `ai_mode_probe.py` | Measures `type_search: "ai-mode"` alone: asks Google's AI Mode (`src/ai_mode.py`), in Chrome, for an entry per item with a known entry and saves the answers, cleaned, for `run.py --rescore`. |
| `estimate_cost.py` | Measures the current prefix and prices it, per model. |
| `install_service.py` | Builds the Automator workflow from `automator/script.sh` and installs it. |
| `library_match_coverage.py` | Read-only: how often the library holds a chapter's book, and how often its fields are right, on a `.bib` of known entries. |
| `render_entries.py` | `bib_render` over a `.bib` or saved run outputs. |
| `test_setup.py` | Dependencies, configuration, context files, OCR, and documentation drift. `--preflight` is the fast subset the quick action runs. |
| `validate_entries.py` | `bib_validate` over a `.bib` or saved run outputs. |

Self-tests, each run as `python3 dev/<name>`; fixtures are canned or constructed,
and Claude, Gemini and the network are stubbed:

| | |
| --- | --- |
| `test_ai_mode_probe.py` | The probe's prompt parts from an expected entry, and its cleanup before scoring. |
| `test_api_keys.py` | Key resolution: plaintext, environment, command; a failing command's output never leaks. |
| `test_batch.py` | Parallel batches: every source saved, one save at a time, a failure not stopping the rest. |
| `test_biblio_agent_markers.py` | What `save_entry()` writes: `% AMBER` comments, colour, `failed_bib_file`. |
| `test_bib_validate.py`, `test_bib_render.py` | The biber check and the render check, with TeX. |
| `test_extract_pages.py` | The thin-yield marking of a PDF that yields almost no text. |
| `test_ai_mode.py` | The type search through Google's AI Mode: the prompt, the entry read from the page, its fields filling gaps only. |
| `test_claude_search.py` | The type search through Claude's web search: the per-item cap, no answer without a search, searches recorded and priced. |
| `test_gemini_search.py`, `test_gemini_step.py` | Gemini's type and questions; nothing reaches Gemini when it is off. |
| `test_house_style.py` | The per-entry house cleanup. |
| `test_library_match.py` | Library matching, and its place in the pipeline. |
| `test_recollection.py` | CrossRef replacing a recollected value. |
| `test_repairs.py` | The cleanup spec's repairs, the repair call and the render check. |
| `test_search_choice.py` | The window's Search choice: the options offered, read from config and applied before each file. The popup itself is not exercised. |
| `test_web_source.py` | The `.webloc` routes and the plausibility check, against canned pages. |

The accuracy harness, `dev/eval/` (see its `README.md`):

| | |
| --- | --- |
| `baselines/` | One dated report per full run; the comparison point. |
| `expected.bib` | Ground-truth entries; empty until populated. |
| `gemini_type_eval.py` | Scores Gemini's type on saved outputs. |
| `populate_sample.py` | The BibDesk attachment resolver `select_sample.py` imports. |
| `run.py` | Scores `sample/` against `expected.bib`; `--rescore` re-scores saved output. |
| `sample/manifest.json`, `sample/selection.json` | The sample's citekeys, sources and hashes; provenance of the last selection. |
| `scorer.py` | Field-level comparison. |
| `select_sample.py` | Builds `sample/` and `expected.bib` from a library. |
| `test_eval.py` | The harness's self-test; synthetic fixtures, no API call. |

Context generators, to be re-run after a package update and then diffed:

| | |
| --- | --- |
| `build_template.py` | Regenerates `biblio-template.bib` from real entries, stripping BibDesk bookkeeping. |
| `extract_intro.py` | Regenerates `cms-notes-intro-guide.md` from the package's `cms-notes-intro.tex`. |
| `extract_manual.py` | Regenerates `biblatex-chicago-fields.md` from `biblatex-chicago.tex`. |

Normalization of an existing library, documented in
[`dev/normalization-plan.md`](dev/normalization-plan.md):

| tool | acts by | for |
| --- | --- | --- |
| `bib_apply.py` | name | A JSON list of “in entry X, set field Y to Z”, for what judgement rather than rule must settle. |
| `bib_audit.py` | — (read-only) | Counting. Parses by byte span and proves round-trip equality before reporting anything. Also holds the predicates the other two import. |
| `bib_bisect.py` | subsetting | Isolates the entry that breaks a build or crashes BibDesk. |
| `bib_normalize.py` | rule | Edits derived from a predicate that fires across the corpus. |

Three properties make these safe against an irreplaceable file. **Nothing is
re-serialized**: every change is a byte-span rewrite, so tab indentation,
alphabetical field order, the multi-kilobyte base64 in `bdsk-file-*` and the
`@comment` blocks holding the group hierarchy all pass through untouched — and,
decisively, the diff remains reviewable. **Ten gates** run before any write:
round-trip, entry count, citekey order, `@comment` byte-identity, protected fields,
swallowed fields, doubled commas, length arithmetic computed independently of the
scanner, utf-8 identity, and an independent `bibtool` parse. **Anything doubtful is
left alone and reported** rather than transformed.

The defect class worth knowing about is the one nobody plans for: values that are
well-formed and wrong — a page range sitting in `Volume`, a bare number in
`Issuetitle`, a title stored twice. They parse, they compile, and they read as
merely odd records.

## Third-party material

| File | Origin | Modified |
| --- | --- | --- |
| `biblatex-chicago-fields.md` | derived from §4.2 of `biblatex-chicago.tex` by `dev/extract_manual.py` | yes — markup stripped, paragraphs reflowed, one heading added per field; wording unchanged |
| `cms-notes-intro-guide.md` | derived from `cms-notes-intro.tex` by `dev/extract_intro.py` | yes — LaTeX scaffolding stripped, cross-references rendered as plain text; wording unchanged |
| `notes-test.bib` | the package's `doc/` directory | yes — LaTeX accent macros converted to Unicode, double-hyphen ranges to single hyphens; no bibliographic content altered |

The two `.tex` sources are not vendored: they ship with the package, and what this
project consumes is the generated file. Upstream drift therefore surfaces as a diff
in the generated file when the script is re-run, which is the artefact that matters.
