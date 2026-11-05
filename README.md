# Ostracon

A macOS agent that reads a PDF or a `.webloc` bookmark and writes a
BibLaTeX-Chicago entry (notes and bibliography), optionally filing it straight into
BibDesk.

**NOTE: Early-stage software, developed against one ~5,500-item library.** The prompt rules,
entry-type quotas, and normalization conventions follow one scholar's
biblatex-chicago practice. Measured accuracy is approximately 83% on entry-type identification against
hand-verified ground truth; see `dev/eval/baselines/` for the field-level tables
and `dev/eval/README.md` for what the evaluation suite cannot measure. Assume entries need
checking before you cite from them. macOS only; BibDesk is optional.

Design notes, measurements and cost analysis live in [`NOTES.md`](NOTES.md).

- [Rationale](#rationale)
- [What it does](#what-it-does)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Cost](#cost)
- [BibDesk integration](#bibdesk-integration)
- [Normalizing an existing library](#normalizing-an-existing-library)
- [Evaluating extraction accuracy](#evaluating-extraction-accuracy)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [License](#license)
- [Why “Ostracon”?](#why-ostracon)

## Rationale

[Chicago](https://www.chicagomanualofstyle.org/tools_citationguide/citation-guide-1.html) is the bibliography style typically used in the humanities, cherished for its attention to source and transmission history, to various types of authorship, and to detail in general. Its “notes and bibliography” variant relies on footnotes or endnotes rather than inline “author-date” references, and is the more common one in music theory and musicology. This Claude agent adds Zotero-like auto-fill capabilities to such BibLaTeX-Chicago writing workflows, generating `.bib` entries for new sources supplied as PDF files or `.webloc` bookmarks. Thanks to its reliance on AI models, multiple metadata sources, and elaborate prompting, the agent aims at outperforming Zotero, not merely matching it.

The immense number of types and fields in the [BibLaTeX-Chicago](https://ch.mirrors.cicku.me/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf) package makes Zotero unsustainable as a bibliography manager, with the otherwise excellent [Better BibTeX](https://retorque.re/zotero-better-bibtex/) extension only alleviating a painful experience. For many writers, [BibDesk](https://bibdesk.sourceforge.io) is the only macOS manager that elegantly navigates the style’s ontological complexity. They will be relieved to know that the agent optionally imports the new references into BibDesk.

The agent assumes the Chicago Manual of Style throughout. Adapting it to another style, such as APA, is left as a trivial exercise for the reader.

## What it does

For each source, in this order (`extract_bibtex()` in `src/biblio_agent.py`). The
switch that turns a step on or off is in brackets.

1. **Read the source.** A PDF yields the text of its key pages, OCR'd when they
   hold fewer than `ocr_threshold` words. A `.webloc` yields the bookmarked page
   (see below).
2. **Decide the type** [`gemini_type_check`]. A light Claude call reads the item's
   identity (title, authors, container, year); Gemini, grounded in Google Search,
   picks the entry type from a closed list. Without it, or when Gemini finds
   nothing, Claude picks the type in step 3.
3. **Extract.** One Claude call writes the entry, under the decided type. The
   prompt is the cached prefix (the rules, your house-style file, the template and
   references in `prompt-context/`) plus the source, and, with
   `library_exemplars`, up to three entries from your library as models of form.
4. **Ask Gemini** [`gemini_type_check`]. For the entry's final type, the question
   bank (`prompt-context/gemini-questions.yaml`) and every other field the type
   takes. The answers are applied in step 8.
5. **Fill missing fields** [`enrich_missing_fields`]. CrossRef and Google Scholar
   are searched for required or desired fields the entry lacks; one Claude call merges what
   they return.
6. **Audit the grounding** [`enrich_missing_fields`]. One Claude call lists the
   fields not read from the source. Each is re-checked: a CrossRef record
   confirmed to be this work (by DOI, or by an author the source gives) replaces
   a recollected value; what stays unconfirmed is reported as inferred.
7. **Match the library** [`library_match`, needs `library_bib_file`]. A work
   already in the library is reported. A chapter whose book the library holds
   (other chapters of it, or the book's own entry, same title and year) takes the
   book's fields in the library's form.
8. **Apply Gemini's answers.** They fill fields still missing and replace values
   that came only from Scholar; a disagreement with the entry is logged.
9. **Clean up**, in code. Forbidden fields stripped; an English `Langid` becomes
   `american`. With `house_cleanup`: the library's normalizer rules, the cleanup
   spec's repairs (`src/repairs.py`), and the library's spelling of Type,
   Location and Publisher.
10. **Validate** [`validate_entries`]. biber checks the entry against
    biblatex-chicago's data model. If it rejects the entry, one Claude call
    repairs it [`repair_entries`] and biber checks again.
11. **Check the rendering** [`render_check`]. The entry is compiled; a field that
    never reaches the page is moved into `Note`.
12. **Save** to `main_bib_file`, or straight into BibDesk [`autofile_bibdesk`]. A
    reply holding no well-formed entry goes to `failed_bib_file`.

**Which value wins.** A value read from the source, or from a CrossRef record
confirmed to be this work, stands. Below it, in order: the library's (same book),
Gemini's, and Google Scholar's or Claude's unconfirmed recollection. Each value
taken from elsewhere is named in the entry's `% Sources` comment; one nothing
confirmed, in `% INFERRED`.

**What colours an entry** (amber in BibDesk, `% AMBER` in the file): only a real
failure. biber still rejects it, the render check fails, the source is thin or
not the work, or, with `require_langid`, it has no `Langid`. Doubts the pipeline
could not settle are logged, not coloured.

**`.webloc` sources.** A bookmarked page is not always reachable (Cloudflare
challenges on Oxford Academic and other Silverchair platforms, login walls,
consent gates), so three routes are tried in turn:

1. **Fetched** directly.
2. **Read from an open Safari or Chrome tab** showing the same URL, which already
   holds your session (see Troubleshooting). With `open_blocked_in_browser`, a
   page no tab holds is opened in a new Chrome tab, read, and closed.
3. **Built from CrossRef**, when the URL contains a DOI.

A page that is not the one requested is rejected and the next route tried; one
that is merely thin (no author, DOI or date; little text) yields an entry,
coloured for review.

![Progress window](screenshot.png)

## Installation

```bash
brew install ocrmypdf                                # optional; for scanned PDFs

conda create -n biblio-ai python=3.11                # or venv
conda activate biblio-ai
pip install -r requirements.txt                      # anthropic, pypdf, pyyaml,
                                                     # requests, beautifulsoup4,
                                                     # pyobjc-framework-Cocoa

cp config.yaml.example config.yaml                   # then set anthropic_api_key_command
cp automator/script.sh.example automator/script.sh   # then set PYTHON and WORKDIR

./install                                            # bib-audit link; the Finder quick action
python3 dev/test_setup.py                            # verify
```

`config.yaml` and `automator/script.sh` are not under version control. Re-run
`./install` after changing `script.sh`: it rebuilds the quick action when it is
stale.

`dev/test_setup.py` checks dependencies, configuration, the context files, OCR,
and that the documentation names every configuration key, command-line flag and
tracked file. The quick action runs `dev/test_setup.py --preflight` before each
batch. The self-tests (`dev/test_*.py`, `dev/eval/test_eval.py`) are listed in
[`NOTES.md`](NOTES.md#developer-tooling).

## Configuration

Three keys need a value; the rest have defaults.

```yaml
anthropic_api_key_command: "~/.local/bin/secret biblio-ai"
main_bib_file: "~/Desktop/biblio-staging.bib" # where entries are appended
failed_bib_file: "~/Desktop/biblio-failed.bib" # where invalid entries go
```

`anthropic_api_key_command` names a command, run without a shell, whose first line
of output is the key: `security find-generic-password -s NAME -w` (macOS Keychain),
`pass show NAME`, or anything else that prints it. The quick action's `PATH` is
minimal, so name a program outside `/usr/bin` by its full path. A plaintext
`anthropic_api_key` takes precedence, then `ANTHROPIC_API_KEY`, then the command.
A command that fails, prints nothing or hangs for ten seconds stops the run.

Relative paths resolve against the repository. A context file named in
`config.yaml` but missing is an error at startup.

| Key                     | Default             | Effect |
| ----------------------- | ------------------- | ------ |
| `model`                 | `claude-sonnet-4-6` | Price alternatives with `dev/estimate_cost.py --model <id>` first. |
| `careful_model`         | `claude-opus-5`     | For sources in `pdf-in/careful/` during `--all`. See Usage. |
| `batch_workers`         | `3`                 | Sources processed at once; `1` processes them one at a time. |
| `max_tokens`            | `4000`              | Ceiling per response; entries run 400–500 tokens. |
| `cache_ttl`             | `"1h"`              | Prompt-cache lifetime. See Cost. |
| `enrich_missing_fields` | `true`              | Steps 5–6. `false` leaves the extraction as written. |
| `gemini_type_check`     | `false`             | Steps 2, 4 and 8. Needs `gemini_api_key`. About two cents a source. |
| `gemini_api_key`        | —                   | A billed Google AI project (the free tier returns HTTP 429). `gemini_api_key_command` works as for Anthropic. |
| `library_bib_file`      | —                   | Your library, read once per run. Steps 7 and 9 use it; without it they skip what needs it. |
| `library_match`         | `true`              | Step 7. |
| `library_exemplars`     | `false`             | Library entries as models of form in the extraction prompt (step 3). |
| `house_cleanup`         | `true`              | Step 9's normalizer rules, repairs and library spellings. |
| `validate_entries`      | `true`              | Step 10's biber check. Skipped, and said so, without TeX. |
| `repair_entries`        | `true`              | Step 10's repair call. Re-types the entry only if Gemini did not decide the type. |
| `render_check`          | `false`             | Step 11. About 3 s a source. |
| `require_langid`        | `false`             | Colour an entry without `Langid`. |
| `open_blocked_in_browser` | `false`           | Open a blocked `.webloc` page in Chrome. Needs Chrome's "Allow JavaScript from Apple Events". |
| `crossref_email`        | —                   | CrossRef's faster polite pool. Free. |
| `scrapingdog_api_key`   | —                   | Enables Google Scholar. Paid, about a fifth of a cent a lookup. `scrapingdog_api_key_command` works as for Anthropic. |
| `ocr_threshold`         | `100`               | Words below which a page is OCR'd. |
| `thin_yield_threshold`  | `400`               | Words a PDF must yield after OCR; fewer colours the entry. `0` disables. |
| `ocr_timeout`           | `180`               | Seconds allowed to `ocrmypdf`. |
| `interactive_ocr`       | `true`              | `false`: never ask for the OCR language; use `default_ocr_language` (`eng`). |
| `autofile_bibdesk`      | `false`             | Import into BibDesk rather than the staging file. |
| `verbose`               | `true`              | Progress on stderr. |

Presentation settings live under `interface:`; none affects extraction.

```yaml
interface:
  show_window: false     # The floating progress window; --window/--no-window override.
  window_models: [...]   # Models offered in the window's dropdown. Unset hides it.
  notifications: false   # macOS notifications for batch progress and failures.
```

The same keys at the top level are still read, with a deprecation warning, until
the next release.

`pdf_in_folder`, `pdf_out_folder`, `template_file`, `rules_file`, `ref_file` and
`example_files` name the cached prefix and may be left as they are;
[`NOTES.md`](NOTES.md) says what each contributes. Put your own conventions (your
preamble, your languages, fields to require or omit) in a private **house-style
file**, not in `rules_file`: `~/.config/ostracon-ai/house-style.md` (or
`house_style_file`) is read when present and overrides the rules.

## Usage

Right-click a PDF or `.webloc` file in Finder, or a mixed selection, and choose
**Extract BibLaTeX-Chicago Bibliography (via Claude)**.

The progress window's **Model** dropdown lists `window_models`; a change applies
from the next file. Reference works (Grove, the Stanford Encyclopedia, Wikipedia)
repay the stronger model: put them in `pdf-in/careful/` before an `--all` run and
they process with `careful_model`. See the operator note in `CLAUDE.md`.

```bash
python3 src/biblio_agent.py paper.pdf bookmark.webloc  # one or more sources
python3 src/biblio_agent.py --all                      # everything in pdf-in/
python3 src/biblio_agent.py paper.pdf --no-save        # print, write nothing
python3 src/biblio_agent.py paper.pdf --output out.bib
python3 src/biblio_agent.py entry.webloc --model claude-opus-5
```

| Flag                       | Effect                                                      |
| -------------------------- | ----------------------------------------------------------- |
| `--all`                    | Process `pdf_in_folder` rather than named files.            |
| `--no-save`                | Print to stdout; no staging file, no BibDesk import.        |
| `--no-move`                | Leave processed files in place. The quick action uses this. |
| `--output FILE`            | Write to `FILE` instead of `main_bib_file`.                 |
| `--model ID`               | Override `model` for this run.                              |
| `--config FILE`            | Use an alternate configuration file.                        |
| `--window` / `--no-window` | Force the progress window on or off.                        |
| `-q`, `--quiet`            | Suppress status messages.                                   |

## Cost

The last full evaluation run (2026-11-04: 168 sources, `claude-sonnet-4-6`,
`gemini_type_check` on) cost $9.77 in Claude tokens and $1.60 in Gemini tokens,
about seven cents a source; its Google searches fell within the 5,000 free a month.
The pipeline has changed since; `python3 dev/estimate_cost.py` prices the current
prompt per model.

Most of the cost is the cached prefix: written once per run, read back cheaply by
every later call. At the default `cache_ttl: "1h"` it survives between quick-action
runs within the hour; at `"5m"` each separate run pays to write it again. CrossRef
is free; ScrapingDog runs about $0.0004 a credit, a couple of credits a lookup.

## BibDesk integration

Entries accumulate in `main_bib_file` for you to import. Each carries a
`bdsk-file-1` link to its source, which survives the import.

With `autofile_bibdesk: true`, entries are imported through AppleScript as they are
written, and BibDesk files the source itself. BibDesk builds the attachment's
filename from `Title` and the authors and flattens any LaTeX in them:
`\bibstring{reviewof}` becomes `reviewof`, `\foreignlanguage{russian}{X}` becomes
`russianX`, and `~`, `\,`, `---` survive verbatim. No BibDesk setting prevents
this; a `Did Auto File` script hook can rename the file afterwards.
[`NOTES.md`](NOTES.md) records what was measured.

## Normalizing an existing library

Separate tools bring an _existing_ `.bib` into the same shape. None runs during
extraction.

```bash
python3 dev/bib_audit.py library.bib                        # count, change nothing
python3 dev/bib_normalize.py library.bib                    # dry run, grouped by rule
python3 dev/bib_normalize.py library.bib --apply --label a  # snapshot, then write
python3 dev/bib_apply.py library.bib edits.json             # named edits
python3 dev/bib_bisect.py library.bib                       # find the entry that breaks a build
```

Every change is a byte-span rewrite, and ten gates must pass before anything is
written. See [`dev/normalization-plan.md`](dev/normalization-plan.md).

## Evaluating extraction accuracy

```bash
python3 dev/eval/run.py           # score dev/eval/sample/ against dev/eval/expected.bib
python3 dev/eval/test_eval.py     # the harness itself; no sample, no API call
```

`run.py` runs the pipeline over real sources and compares each result, field by
field, with a hand-verified entry; entry type is scored separately. The sample and
its ground truth ship empty: build them from your library with
`dev/eval/select_sample.py`. See [`dev/eval/README.md`](dev/eval/README.md).

## Troubleshooting

**Entry saved to `failed_bib_file`.** The reply held no well-formed entry
(unbalanced braces, prose only). Repair it by hand and move it across.

**Entry amber in BibDesk.** See "What colours an entry" above; the log and the
entry's `% AMBER` comment say which failure.

**`bdsk-file-1` link inert after import.** `pyobjc-framework-Cocoa` is missing from
the environment the quick action uses. Check `PYTHON` in `automator/script.sh`.

**Quick action absent from Finder.** On macOS 26 it sits under the contextual menu's
**Services** submenu, for PDF and `.webloc` files only. Re-run
`python3 dev/install_service.py`, which rebuilds it and relaunches Finder. Failing
that, tick it under System Settings ▸ Keyboard ▸ Keyboard Shortcuts ▸ Services ▸
Files and Folders.

**“Context files configured in config.yaml are missing.”** A file named in
`rules_file`, `house_style_file`, `template_file`, `ref_file` or `example_files` is
not where the configuration says. They form the cached prefix, so the agent will
not start without them.

**A bot-challenged `.webloc` fails with the page open in a tab.** Reading a tab
needs a one-time permission. Safari: Develop ▸ Allow JavaScript from Apple Events
(enable the Develop menu under Settings ▸ Advanced). Chrome: View ▸ Developer ▸
Allow JavaScript from Apple Events. Without it the log reports the refusal and the
agent falls back to CrossRef.

**OCR not running.** Install `ocrmypdf`; without it the agent extracts the text
layer only.

**A scanned PDF yields little but a date.** Its text layer holds only whitespace,
which `ocrmypdf --skip-text` treats as done. Strip the layer, or OCR the file
externally, and re-run.

**biber fails on entries with `Volume` or `Number`** (“failed to find
unicore/version”), or rejects plain dates. macOS has pruned biber's unpacked copy
of itself. `rm -rf "$TMPDIR"/par-*` and re-run.

## Repository layout

```
ostracon-ai/
├── CLAUDE.md               # Instructions for agents developing this repository
├── NOTES.md                # Design notes, measurements, developer tooling
├── config.yaml.example     # Copy to config.yaml
├── install                 # Links bib-audit; rebuilds the quick action when stale
├── requirements.txt
├── src/
│   ├── biblio_agent.py     # The pipeline; run this
│   ├── extraction_result.py # What extract_bibtex() hands save_entry()
│   ├── extract_pages.py    # Step 1: PDF text, OCR, the thin-yield check
│   ├── web_source.py       # Step 1: .webloc pages
│   ├── gemini_search.py    # Steps 2, 4, 8: Gemini's type and answers
│   ├── type_fields.py      # The fields each type takes, from the biblatex manual
│   ├── enrich.py           # Steps 5–6: CrossRef/Scholar lookups and reconciliation
│   ├── library_match.py    # Step 7: duplicates, a chapter's book fields, exemplars
│   ├── house_style.py      # Step 9: normalizer rules and library spellings
│   ├── repairs.py          # Step 9: the cleanup spec's mechanical repairs
│   ├── bib_audit.py        # Shared .bib scanner and rule predicates (dev/ links here)
│   ├── bib_normalize.py    # The library's normalizer rules (dev/ links here)
│   ├── bib_validate.py     # Step 10: biber's data-model check; published by ./install
│   ├── bib_render.py       # Step 11: fields that never reach the page
│   ├── tex/previewtemplate.tex # Neutral template for step 11
│   ├── api_keys.py         # API keys from config, environment or a command
│   └── progress_window.py  # The floating window
├── prompt-context/         # The cached prefix
│   ├── extraction-rules.md            # The extraction rules; first in the prefix
│   ├── gemini-questions.yaml          # Gemini's question bank; not in the prefix
│   ├── biblio-template.bib            # One worked example per entry type
│   ├── biblatex-chicago-notes-ref.md  # Condensed field reference
│   ├── notes-test.bib                 # The package's annotated test suite
│   ├── cms-notes-intro-guide.md       # Entry types by kind of source
│   └── biblatex-chicago-fields.md     # Manual §4.2; consulted, not loaded
├── dev/                    # Tooling and self-tests; see NOTES.md
│   └── eval/               # Accuracy harness; see dev/eval/README.md
├── automator/
│   └── script.sh.example   # Copy to script.sh; set PYTHON and WORKDIR
├── pdf-in/                 # Sources for --all
│   └── careful/            # Same, but processed with careful_model
└── pdf-out/                # Where they go afterwards
```

## License

Copyright (c) 2026 [yrammos](https://github.com/yrammos). Licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Free for personal
use; attribution requested for forks and modifications.

Three files in `prompt-context/` derive from the
[biblatex-chicago](https://ctan.org/pkg/biblatex-chicago) package (v2.3b, 2024-04-15),
© 2008–2024 David Fussner, under the
[LPPL](https://www.latex-project.org/lppl/): `notes-test.bib`,
`cms-notes-intro-guide.md` and `biblatex-chicago-fields.md`. Each records its own
provenance and modifications in a header, as the licence requires; the derivations
are reproducible with `dev/extract_intro.py` and `dev/extract_manual.py`. See
[`NOTES.md`](NOTES.md) for what was altered and why the `.tex` sources are not
vendored.

## Why “Ostracon”?

> An ostracon (Greek: ὄστρακον /ós.tra.kon/, plural ὄστρακα /ós.tra.ka/) is a piece
> of pottery (or stone), usually broken off from a vase or other earthenware vessel.
> In archaeology and history, the term refers either to the fragment itself or to a
> potsherd used for writing or drawing.
