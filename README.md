# Ostracon

A macOS agent that reads a PDF or a `.webloc` bookmark and writes a
BibLaTeX-Chicago entry (notes and bibliography), optionally filing it straight into
BibDesk.

Design notes, measurements and cost analysis live in [`NOTES.md`](NOTES.md).

- [Rationale](#rationale)
- [What it does](#what-it-does)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Cost](#cost)
- [BibDesk integration](#bibdesk-integration)
- [Normalizing an existing library](#normalizing-an-existing-library)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [License](#license)
- [Why “Ostracon”?](#why-ostracon)

## Rationale

[Chicago](https://www.chicagomanualofstyle.org/tools_citationguide/citation-guide-1.html) is the bibliography style typically used in the humanities, cherished for its attention to source and transmission history, to various types of authorship, and to detail in general. Its "notes and bibliography" variant relies on footnotes or endnotes rather than inline ("author-date") references, and is the more common one in music theory and musicology.

The immense number of types and fields in the [BibLaTeX-Chicago](https://ch.mirrors.cicku.me/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf) package makes Zotero unsustainable as a bibliography manager, with the otherwise excellent [Better BibTeX](https://retorque.re/zotero-better-bibtex/) extension only alleviating a painful experience. For many writers, [BibDesk](https://bibdesk.sourceforge.io) is the only macOS manager that elegantly navigates the style's ontological complexity. Others avoid managers altogether and prefer to edit `.bib` files directly within a text editor.

With or without BibDesk, this agent enhances BibLaTeX-Chicago writing workflows by providing Zotero-like auto-creation and auto-fill capabilities for new bibliographic materials, whether in the form of PDF files or `.webloc` links. Thanks to its reliance on AI and elaborate prompting, the agent should not only match Zotero but actually outperform it in most cases.

Using alternative styles (e.g., APA) would involve only minor modifications to the prompts and context; it is left as a trivial exercise for the reader.

## What it does

For each source in turn: extracts text (OCR if scanned; for a `.webloc`,
fetches the bookmarked page),
asks Claude for a matching entry in accordance with BibLaTeX specifications,
searches CrossRef and Google Scholar for any missing fields,
audits the result for fields drawn from Claude's recollection rather than from the source,
and saves.

A malformed entry is stashed away in `failed_bib_file`; a field that could not be confirmed
leaves the entry amber in BibDesk.

Some publishers — Oxford Academic and other Silverchair platforms among them — front every page
with a Cloudflare bot challenge that no HTTP client can pass, so a `.webloc` pointing at one
cannot be fetched at all. Where the bookmarked URL carries a DOI in its path, the entry is built
from the CrossRef record instead. Any other fetch failure is still reported rather than papered
over, so a dead bookmark stays visible as one.

![Progress window](screenshot.png)

## Installation

```bash
brew install ocrmypdf                                # optional; for scanned PDFs

conda create -n biblio-ai python=3.11                # or venv
conda activate biblio-ai
pip install -r requirements.txt                      # anthropic, pypdf, pyyaml,
                                                     # requests, beautifulsoup4,
                                                     # pyobjc-framework-Cocoa

cp config.yaml.example config.yaml                   # then set anthropic_api_key
cp automator/script.sh.example automator/script.sh   # then set PYTHON and WORKDIR

python3 dev/install_service.py                       # the Finder quick action
python3 dev/test_setup.py                            # verify
```

`config.yaml` and `automator/script.sh` are excluded from version control. Re-run
`dev/install_service.py` after any change to `script.sh`.

`dev/test_setup.py` checks dependencies, configuration, the context files and OCR
availability, and confirms the documentation still names every configuration key,
every command-line flag and every tracked file. The quick action runs
`dev/test_setup.py --preflight` before each batch: silent when all is well, and
abandoning the run with a message when it is not.

## Configuration

Three keys need a value; the rest have defaults.

```yaml
anthropic_api_key: "sk-ant-..."
main_bib_file: "~/Desktop/biblio-staging.bib" # where entries are appended
failed_bib_file: "~/Desktop/biblio-failed.bib" # where invalid entries go
```

Relative paths resolve against the repository rather than the working directory, so
the commands below work from anywhere. A context file named in `config.yaml` but
absent from disk is an error at startup, not a warning.

| Key                     | Default             | Effect                                                                                                            |
| ----------------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `model`                 | `claude-sonnet-4-6` | Price alternatives with `dev/estimate_cost.py --model <id>` first.                                                |
| `careful_model`         | `claude-opus-5`     | Used automatically for sources in `pdf-in/careful/` during `--all`. See Usage.                                    |
| `max_tokens`            | `4000`              | Ceiling per response; entries run 400–500 tokens.                                                                 |
| `cache_ttl`             | `"1h"`              | Prompt-cache lifetime. Cheaper unless runs are genuinely isolated.                                                |
| `enrich_missing_fields` | `true`              | The CrossRef/Scholar lookups, the grounding audit and reconciliation. `false` leaves only the initial extraction. |
| `verbose`               | `true`              | Progress on stderr.                                                                                               |
| `ocr_threshold`         | `100`               | Words below which a PDF is treated as scanned.                                                                    |
| `ocr_timeout`           | `180`               | Seconds allowed to `ocrmypdf`.                                                                                    |
| `default_ocr_language`  | `eng`               | Tesseract language when OCR runs unattended.                                                                      |
| `autofile_bibdesk`      | `false`             | Import into BibDesk directly rather than to the staging file.                                                     |
| `crossref_email`        | —                   | Opts into CrossRef's faster polite pool. Free, no account.                                                        |
| `scrapingdog_api_key`   | —                   | Enables the Google Scholar fallback. Paid, roughly a fifth of a cent per lookup.                                  |

Presentation settings — nothing below affects extraction — live under one
`interface:` block:

```yaml
interface:
  show_window: false     # The floating progress window; --window/--no-window override.
  window_models: [...]   # Models offered in the window's dropdown. Unset hides it.
  notifications: false   # macOS notifications for batch progress and failures.
```

A bare top-level `show_window`, `window_models` or `notifications` key (the pre-9
layout) is still read for one release if `interface:` is absent or omits it, with a
one-time deprecation warning on stderr; move it under `interface:` when convenient.

The remaining paths — `pdf_in_folder`, `pdf_out_folder`, `template_file`,
`claude_md_file`, `ref_file`, `example_files` — name the files constituting the
cached prefix and may be left as they are. [`NOTES.md`](NOTES.md) sets out what each
contributes and what dropping it would save.

Edit `CLAUDE.md` to match your own conventions: field exclusions, title-case rules
for the languages you work in, the entry types you rely on. It is the first file in
the prefix, and the agent is only as exacting as it is.

## Usage

Right-click a PDF or `.webloc` file in Finder — or a mixed selection — and choose
**Extract BibLaTeX-Chicago Bibliography (via Claude)**.

The progress window offers a **Model** dropdown listing whatever `window_models`
names; a change there applies from the next file onward. Reference works (Grove,
the Stanford Encyclopedia, Wikipedia) are where the stronger model repays its
cost — rather than reaching for the dropdown in time, drop such sources into
`pdf-in/careful/` before an `--all` run and they process with `careful_model`
automatically, in either windowed or headless mode. See the operator note in
`CLAUDE.md`.

From the command line:

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

> **Measured 2026-08-07** against `claude-sonnet-4-6` at $3/$15 per million
> input/output tokens. Both the prefix size and the published rates drift — editing
> `CLAUDE.md` alone moved these figures between 3 and 7 August. Re-run
> `python3 dev/estimate_cost.py`, which measures the assembled prompt rather than
> restating these numbers, before relying on them.

The static prefix is **68,928 tokens**. Writing it costs $0.26 at the five-minute
cache TTL and $0.41 at one hour; every later call in the same run reads it back for
$0.021.

| Call             | Runs when                                   | First file | Later files |
| ---------------- | ------------------------------------------- | ---------- | ----------- |
| Extraction       | always                                      | $0.276     | $0.039      |
| Grounding audit  | `enrich_missing_fields: true`               | $0.007     | $0.007      |
| Enrichment merge | required or desired fields missing          | $0.028     | $0.028      |
| Reconciliation   | a CrossRef match strictly completes a value | $0.028     | $0.028      |

A clean source costs about **$0.28 for the first file in a run and $0.05 for each
one after**; with all four calls firing, **$0.34 and $0.10**. Reconciliation is
conservative, so the upper figure is rare.

Because the prefix is written once per run and read thereafter, cost turns on how
often a run starts cold. At the five-minute default the cache does not survive
between separate quick-action invocations.

| Pattern                                    | 5-minute TTL | 1-hour TTL |
| ------------------------------------------ | ------------ | ---------- |
| One batch of ten, nothing else that hour   | $0.69        | $0.85      |
| Two batches of five, twenty minutes apart  | $0.93        | $0.85      |
| Ten single-file invocations across an hour | $2.83        | $0.85      |

The hour costs a flat $0.15 more per cache write and nothing more per file, so it
loses only when a run is genuinely isolated. External services are negligible:
CrossRef is free, and the ScrapingDog fallback runs about $0.0004 per credit at a
couple of credits per lookup.

## BibDesk integration

By default entries accumulate in `main_bib_file` for you to import. Each carries a
`bdsk-file-1` bookmark to its source, so links survive the import.

Set `autofile_bibdesk: true` to skip the staging file: entries are imported through
AppleScript as they are written, and BibDesk files the source document itself.

One caveat before enabling it. BibDesk builds the attachment's filename from `Title`
and the author fields, and flattens any LaTeX in them: `\bibstring{reviewof}`
becomes `reviewof`, `\foreignlanguage{russian}{X}` becomes `russianX`, and `~`,
`\,`, `---` survive verbatim. No BibDesk setting prevents this. Plain ASCII titles
are unaffected; if yours are not, a `Did Auto File` script hook can correct the name
after BibDesk has chosen it. [`NOTES.md`](NOTES.md) records what was measured.

## Normalizing an existing library

The agent writes new entries; a separate set of tools brings an _existing_ `.bib`
into the same shape. Nothing in `dev/` runs during extraction.

```bash
python3 dev/bib_audit.py library.bib                        # count, change nothing
python3 dev/bib_normalize.py library.bib                    # dry run, grouped by rule
python3 dev/bib_normalize.py library.bib --apply --label a  # snapshot, then write
python3 dev/bib_apply.py library.bib edits.json             # named edits
python3 dev/bib_bisect.py library.bib                       # find the entry that breaks a build
```

Every change is a byte-span rewrite rather than a re-serialization, and ten gates
must pass before anything is written. Method, tier taxonomy and outstanding items
are in [`dev/normalization-plan.md`](dev/normalization-plan.md), which also explains
why the audit's steady state is not zero and why a compile against your own preamble
is the check that cannot be skipped.

## Troubleshooting

**Entry saved to `failed_bib_file`.** Unbalanced braces. Repair it by hand and move
it across.

**Entry amber in BibDesk.** A field could not be confirmed: the grounding audit
suspected recollection and no catalogue resolved it, or a match introduced a
conflict left deliberately unresolved.

**`bdsk-file-1` bookmark inert after import.** `pyobjc-framework-Cocoa` is missing
from the environment the quick action uses. Check `PYTHON` in
`automator/script.sh`.

**Quick action absent from Finder.** Look under the contextual menu's **Services**
submenu; on macOS 26 that is where it appears, not under Quick Actions. Re-run
`python3 dev/install_service.py`, which rebuilds the workflow, refreshes the
Services cache and relaunches Finder; the menu is built at Finder launch, so a
stale menu survives until Finder restarts. The action is confined to Finder and
to PDF and `.webloc` files, so it appears in no other application and against no
other file type. If it is still absent, check that it is ticked under System
Settings ▸ Keyboard ▸ Keyboard Shortcuts ▸ Services ▸ Files and Folders.

**“Context files configured in config.yaml are missing.”** A file named in
`claude_md_file`, `template_file`, `ref_file` or `example_files` is not where the
configuration says. These constitute the cached prefix, so the agent refuses to
start rather than proceed without them.

**OCR not running.** Install `ocrmypdf`. Without it the agent falls back to direct
extraction.

**A scanned PDF yields little but a date.** Some scans carry a text layer holding
only whitespace; `ocrmypdf --skip-text` treats those pages as done and exits
successfully. Strip the layer, or OCR the file externally, and re-run.

## Repository layout

```
ostracon-ai/
├── CLAUDE.md               # House style; first file in the cached prefix
├── config.yaml.example     # Copy to config.yaml
├── requirements.txt
├── src/
│   ├── biblio_agent.py     # Orchestrator; run this
│   ├── extract_pages.py    # PDF text and OCR
│   ├── web_source.py       # .webloc page fetching
│   ├── enrich.py           # CrossRef/Scholar lookups and reconciliation
│   └── progress_window.py  # The floating window
├── prompt-context/         # The cached prefix
│   ├── biblio-template.bib            # One worked example per entry type
│   ├── biblatex-chicago-notes-ref.md  # Condensed field reference
│   ├── notes-test.bib                 # The package's annotated test suite
│   ├── cms-notes-intro-guide.md       # Entry types by kind of source
│   └── biblatex-chicago-fields.md     # Manual §4.2; consulted, not loaded
├── dev/                    # Tooling; see NOTES.md. Nothing here runs during extraction
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
