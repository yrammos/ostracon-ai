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
- [How it works](#how-it-works)
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

## How it works

For each source, the agent sequentially:

1. **Extracts text**: from the PDF, with OCR for scanned pages, or from the bookmarked page.
2. **Estimates the entry type**: using Gemini [`gemini_type_check`]; otherwise using Claude in step 3.
3. **Estimates the entry fields**: with Claude, given the previously estimated type, per the extraction rules and your
   house style.
4. **Retrieves missing locators**: volume, number, pages, chapter, book title and, for books, publisher, from the work's CrossRef record, with Google Scholar as a fallback [`enrich_missing_fields`].
5. **Verifies recollected field values**: fields that Claude wrote from memory rather than the PDF or web page are looked up in CrossRef, with Google Scholar as a fallback. Fields that neither engine confirms are reported as inferred [`enrich_missing_fields`].
6. **Reconciles the new item with related library items**: duplicate items are reported; an item (e.g., chapter) and its container (e.g., book) inherit each other's fields from the library for consistency, whichever was entered first; supplements (e.g., an introduction) and their books likewise [`library_match`].
7. **Retrieves the remaining fields**: any other field the type takes (editor, series, edition, translator, and so on), from Gemini's search-grounded answers [`gemini_type_check`].
8. **Enforces house style compliance**: i.e., with the library's normalization rules and spellings
   [`house_cleanup`].
9. **Validates the new entry**: using biber against biblatex-chicago's data model, with one repair
   by Claude [`validate_entries`, `repair_entries`]; a test compile shows that
   every field prints [`render_check`].
10. **Saves the output**: in the staging file, or in BibDesk [`autofile_bibdesk`].

**Precedence.** Where two sources offer different values for the same field, a
value read from the PDF or web page prevails, as does one from a CrossRef record
confirmed to describe the same work. Below these come, in order, the library's
value, Gemini's, and last a value from Google Scholar or one that Claude recalled
without confirmation. The comments written above each entry name the origin of
every value not read from the source (`% Sources`) and list the recalled values
that nothing confirmed (`% INFERRED`).

**Review colour.** An entry is coloured amber in BibDesk only when something has
failed: biber still rejects it after the repair attempt, one of its fields does
not appear in the printed citation, the source is too thin to trust or is not the
work at all (a login page, for instance), or, with `require_langid`, it has no
`Langid`. Doubts that could not be settled are logged, not coloured.

**Blocked pages.** When a bookmarked page cannot be fetched directly, because a
bot challenge or a login wall stands in the way, it is read from a Safari or
Chrome tab already showing it, which carries your session. Failing that, with
`open_blocked_in_browser`, it is read from a Chrome tab opened and closed for the
purpose; and failing that, if the URL contains a DOI, the entry is built from the
CrossRef record.

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

`anthropic_api_key_command` is a command whose first line of output is the key,
such as `security find-generic-password -s NAME -w` or `pass show NAME`; give its
full path if it lies outside `/usr/bin`. A plaintext `anthropic_api_key`, then
`ANTHROPIC_API_KEY`, take precedence. Relative paths resolve against the
repository.

Gemini is optional. To enable it, set `gemini_type_check: true` and give a key
from a billed Google AI Studio project (aistudio.google.com/apikey), the same way
as the Anthropic key: `gemini_api_key_command`, or a plaintext `gemini_api_key`,
which takes precedence. No environment variable is read. Claude calls are billed
to the Anthropic API account, Gemini calls to the Google project; a Claude
subscription covers neither (see [Cost](#cost)).

```yaml
gemini_type_check: true
gemini_api_key_command: "security find-generic-password -s ostracon-gemini-api-key -w"
```

| Key | Default | Effect |
| --- | --- | --- |
| `autofile_bibdesk` | `false` | Import each entry into BibDesk rather than the staging file. |
| `batch_workers` | `3` | Sources processed at once. |
| `cache_ttl` | `"1h"` | Prompt-cache lifetime. See Cost. |
| `careful_model` | `claude-opus-5` | Model for sources in `pdf-in/careful/`. See Usage. |
| `crossref_email` | — | CrossRef's faster polite pool. Free. |
| `enrich_missing_fields` | `true` | Completion from CrossRef and Google Scholar, and the check of unconfirmed fields. |
| `gemini_api_key` | — | For `gemini_type_check`; a billed Google AI project. Or `gemini_api_key_command`. |
| `gemini_type_check` | `false` | Gemini decides the type and fills missing fields. |
| `house_cleanup` | `true` | The library's normalization rules and spellings. |
| `interactive_ocr` | `true` | Ask for the OCR language; `false` uses `default_ocr_language` (`eng`). |
| `library_bib_file` | — | Your library, for `library_match`, `house_cleanup` and `library_exemplars`. |
| `library_exemplars` | `false` | Show Claude up to three library entries as models of form. |
| `library_match` | `true` | Report duplicates; an item and its container inherit each other's fields from the library. |
| `max_tokens` | `4000` | Ceiling per response. |
| `model` | `claude-sonnet-4-6` | Claude model. |
| `ocr_threshold` | `100` | Words below which a page is OCR'd. |
| `ocr_timeout` | `180` | Seconds allowed to `ocrmypdf`. |
| `open_blocked_in_browser` | `false` | Open a blocked page in Chrome. Needs Chrome's "Allow JavaScript from Apple Events". |
| `render_check` | `false` | Test-compile each entry. About 3 s a source. |
| `repair_entries` | `true` | One Claude call repairs an entry biber rejects. |
| `require_langid` | `false` | Colour an entry without `Langid`. |
| `scrapingdog_api_key` | — | Enables Google Scholar. Or `scrapingdog_api_key_command`. |
| `thin_yield_threshold` | `400` | Words a PDF must yield; fewer colours the entry. `0` disables. |
| `validate_entries` | `true` | biber's data-model check. |
| `verbose` | `true` | Progress on stderr. |

Presentation settings live under `interface:`; none affects extraction.

```yaml
interface:
  show_window: false     # The floating progress window; --window/--no-window override.
  window_models: [...]   # Models offered in the window's dropdown. Unset hides it.
  notifications: false   # macOS notifications for batch progress and failures.
```

At the top level they are still read, with a warning, until the next release.

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
| `--config FILE`            | Use an alternate configuration file.                        |
| `--model ID`               | Override `model` for this run.                              |
| `--no-move`                | Leave processed files in place. The quick action uses this. |
| `--no-save`                | Print to stdout; no staging file, no BibDesk import.        |
| `--output FILE`            | Write to `FILE` instead of `main_bib_file`.                 |
| `-q`, `--quiet`            | Suppress status messages.                                   |
| `--window` / `--no-window` | Force the progress window on or off.                        |

## Cost

Measured on 168 sources on 2026-11-04 (`claude-sonnet-4-6`, `gemini_type_check`
on); the pipeline has changed since. `python3 dev/estimate_cost.py` prices the
current prompt per model.

| Service                       | First source in a run | Each later source |
| ----------------------------- | --------------------- | ----------------- |
| Claude                        | $0.162                | $0.057            |
| CrossRef                      | free                  | free              |
| Gemini (`gemini_type_check`)  | $0.01                 | $0.01             |
| Google Scholar (ScrapingDog)  | about $0.001 a lookup | the same          |
| Google Search, through Gemini | free, 5,000 a month   | free              |

The first source pays to write the cached prompt; later ones read it back. At the
default `cache_ttl: "1h"` the cache survives between runs within the hour.

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
│   ├── extract_pages.py    # PDF text, OCR, the thin-yield check
│   ├── web_source.py       # .webloc pages
│   ├── gemini_search.py    # Gemini's type and answers
│   ├── type_fields.py      # The fields each type takes, from the biblatex manual
│   ├── enrich.py           # CrossRef/Scholar lookups and reconciliation
│   ├── library_match.py    # Duplicates, a chapter's book fields, exemplars
│   ├── house_style.py      # Normalizer rules and library spellings
│   ├── repairs.py          # The cleanup spec's mechanical repairs
│   ├── bib_audit.py        # Shared .bib scanner and rule predicates (dev/ links here)
│   ├── bib_normalize.py    # The library's normalizer rules (dev/ links here)
│   ├── bib_validate.py     # biber's data-model check; published by ./install
│   ├── bib_render.py       # Fields that never reach the page
│   ├── tex/previewtemplate.tex # Neutral template for the test compile
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
