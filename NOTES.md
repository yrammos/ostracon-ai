# Notes

Reasoning, measurements and the developer tooling. Nothing here is needed in order
to use the agent; see [`README.md`](README.md) for that.

- [Why it exists](#why-it-exists)
- [The cached prefix](#the-cached-prefix)
- [Cost](#cost)
- [Auto-filing: what was measured](#auto-filing-what-was-measured)
- [Developer tooling](#developer-tooling)
- [Third-party material](#third-party-material)

## Why it exists

Chicago is the bibliographic style of the humanities, valued for its attention to
source and transmission. The corresponding LaTeX implementation, biblatex-chicago,
offers some forty entry types and a correspondingly large field vocabulary, which is
precisely what makes Zotero-style automatic entry creation hard to reproduce by
hand: the difficulty lies less in filling fields than in deciding which type a
source belongs to.

That decision is what the cached prefix is for. Adapting the agent to another style
would require changes to the prompt and the context files, and to nothing else.

## The cached prefix

Five files are assembled into a static prefix and cached with the provider, so that
a run pays for them once and reads them back thereafter.

| component | tokens |
| --- | ---: |
| `notes-test.bib` — the package's annotated test suite | 46,302 |
| `CLAUDE.md` — house style | 10,393 |
| `biblatex-chicago-notes-ref.md` — condensed field reference | 4,959 |
| `cms-notes-intro-guide.md` — entry types by kind of source | 3,208 |
| `biblio-template.bib` — one worked example per type | 2,242 |

**67,813 tokens in total** (229,207 characters), measured by
`dev/estimate_cost.py --no-api`, which applies the project's own ratio of 3.38
characters per token. Dropping `--no-api` counts exactly through the API's
`count_tokens`, which requires credit.

The two corpora loaded by `example_files` do different work from the field
reference. Where the reference teaches *vocabulary* — which fields a given type
takes — they teach *classification*: what kind of thing a source is, and which of
the forty types therefore fits. `notes-test.bib` carries an `annote` on nearly every
entry explaining why that type and that field set follow from the source;
`cms-notes-intro-guide.md` is organised by kind of source rather than by type, which
is what makes it usable before the type is known.

**Precedence divides by question, not by file.** The package's own files are
authoritative on what biblatex-chicago supports — which types exist, what each
field does, what reaches the printed page. `CLAUDE.md` and `biblio-template.bib` are
authoritative on this project's presentation choices, where the package permits more
than one form. Neither local file can overrule the package on a question of
capability, and the operational test when the two are confused is to encode both
alternatives and compare the rendered output. `CLAUDE.md` sets this out at length.

To shorten the prefix, `notes-test.bib` is the obvious candidate at 46,302 of the
67,813 tokens: dropping it from `example_files` while retaining
`cms-notes-intro-guide.md` leaves roughly 21,500 and a much cheaper first file,
preserving the taxonomy while shedding the annotated examples. Weigh that against
what the suite is for — it is the authority on what each type and field actually
does.

`biblatex-chicago-fields.md` reproduces §4.2 of the manual and is deliberately *not*
loaded: an A/B run found no benefit for some 45,000 additional tokens. It remains
for consultation.

## Cost

At `claude-sonnet-4-6` rates ($3/$15 per million input/output tokens), a cache write
costs 1.25× input at the five-minute TTL and 2× at one hour; a cache read costs
0.1×. Writing the prefix therefore costs $0.25 at five minutes and $0.41 at an hour;
every later call in the same run reads it for $0.020.

| Call | Runs when | First file | Later files |
| --- | --- | --- | --- |
| Extraction | always | $0.272 | $0.038 |
| Grounding audit | `enrich_missing_fields: true` | $0.007 | $0.007 |
| Enrichment merge | required or desired fields missing | $0.028 | $0.028 |
| Reconciliation | a CrossRef match strictly completes a value | $0.028 | $0.028 |

A clean source costs **$0.28 for the first file in a run and $0.05 for each
subsequent one**; with all four calls firing, **$0.33 and $0.10**. Reconciliation is
conservative — a Scholar-sourced conflict, or a CrossRef value that contradicts
rather than completes, is left for review without reaching the fourth call — so the
upper figure is rare.

External services are negligible: CrossRef is free, and the ScrapingDog fallback
runs about $0.0004 per credit at a couple of credits per lookup.

### Batching and cache lifetime

The prefix is written once per run and read thereafter, so cost turns on how often a
run begins without a warm cache. At the five-minute default the cache does not
survive between separate quick-action invocations.

| Pattern | 5-minute TTL | 1-hour TTL |
| --- | --- | --- |
| One batch of ten, nothing else that hour | $0.69 | $0.84 |
| Two batches of five, twenty minutes apart | $0.92 | $0.84 |
| Ten single-file invocations across an hour | $2.79 | $0.84 |

The hour costs a flat **$0.15 more per cache write** and nothing more per file, so
it loses only when a run is genuinely isolated; any second run within the hour
repays the difference at once.

`dev/estimate_cost.py` measures the assembled prompt rather than restating these
figures, so re-run it after changing the prefix or the model.

## Auto-filing: what was measured

With `autofile_bibdesk: true`, `_save_via_bibdesk` calls BibDesk's `auto file` with
no destination, leaving BibDesk to generate the filename from its Local File format.
Its TeX stripping is what produces the damage rather than what prevents it: the
backslash, the command name and the braces are deleted and every brace group's
contents kept, which is right for a one-argument formatting command and wrong for
the two shapes a Chicago library uses constantly.

| in the entry | filed as | |
| --- | --- | --- |
| `\mkbibemph{X}` | `X` | correct |
| `\bibstring{reviewof}` | `reviewof` | the argument is a keyword |
| `\foreignlanguage{russian}{X}` | `russianX` | the first argument is a parameter |

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
| `test_setup.py` | Dependencies, configuration, context files, OCR, and documentation drift. `--preflight` is the fast subset the quick action runs. |
| `install_service.py` | Builds the Automator workflow from `automator/script.sh` and installs it. |
| `estimate_cost.py` | Measures the current prefix and prices it, per model. |
| `ab_compare.py` | Scores two extraction runs against curated ground truth. |

Context generators, to be re-run after a package update and then diffed:

| | |
| --- | --- |
| `extract_intro.py` | Regenerates `cms-notes-intro-guide.md` from the package's `cms-notes-intro.tex`. |
| `extract_manual.py` | Regenerates `biblatex-chicago-fields.md` from `biblatex-chicago.tex`. |
| `build_template.py` | Regenerates `biblio-template.bib` from real entries, stripping BibDesk bookkeeping. |

Normalization of an existing library, documented in
[`dev/normalization-plan.md`](dev/normalization-plan.md):

| tool | acts by | for |
| --- | --- | --- |
| `bib_audit.py` | — (read-only) | Counting. Parses by byte span and proves round-trip equality before reporting anything. Also holds the predicates the other two import. |
| `bib_normalize.py` | rule | Edits derived from a predicate that fires across the corpus. |
| `bib_apply.py` | name | A JSON list of “in entry X, set field Y to Z”, for what judgement rather than rule must settle. |
| `bib_bisect.py` | subsetting | Isolates the entry that breaks a build or crashes BibDesk. |

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
| `notes-test.bib` | the package's `doc/` directory | yes — LaTeX accent macros converted to Unicode, double-hyphen ranges to single hyphens; no bibliographic content altered |
| `cms-notes-intro-guide.md` | derived from `cms-notes-intro.tex` by `dev/extract_intro.py` | yes — LaTeX scaffolding stripped, cross-references rendered as plain text; wording unchanged |
| `biblatex-chicago-fields.md` | derived from §4.2 of `biblatex-chicago.tex` by `dev/extract_manual.py` | yes — markup stripped, paragraphs reflowed, one heading added per field; wording unchanged |

The two `.tex` sources are not vendored: they ship with the package, and what this
project consumes is the generated file. Upstream drift therefore surfaces as a diff
in the generated file when the script is re-run, which is the artefact that matters.
