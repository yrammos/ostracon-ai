# Ostracon

A Claude-powered macOS agent for generating BibLaTeX-Chicago entries from PDF files and `.webloc` bookmarks to online-only publications.

- [Quick Start](#quick-start)
- [Rationale](#rationale)
- [Functionality](#functionality)
  - [Extraction and Reconciliation Flow](#extraction-and-reconciliation-flow)
- [Setup](#setup)
  - [1. Install System Dependencies](#1-install-system-dependencies)
  - [2. Create a Python Environment and Install Dependencies](#2-create-a-python-environment-and-install-dependencies)
  - [3. Configure](#3-configure)
  - [4. Customize the Extraction Prompt](#4-customize-the-extraction-prompt)
  - [5. Configure the Automator Script](#5-configure-the-automator-script)
  - [6. Install the macOS Quick Action](#6-install-the-macos-quick-action)
  - [7. Verify the setup](#7-verify-the-setup)
- [Usage](#usage)
  - [macOS Quick Action (Recommended)](#macos-quick-action-recommended)
  - [Command Line](#command-line)
- [Project Structure](#project-structure)
- [BibDesk Integration](#bibdesk-integration)
- [Troubleshooting](#troubleshooting)
- [Cost Estimate](#cost-estimate)
  - [Batching and cache TTL](#batching-and-cache-ttl)
- [Why "Ostracon"?](#why-ostracon)
- [License](#license)
  - [Third-party material](#third-party-material)

## Quick Start

```bash
brew install ocrmypdf                   # OCR support (optional)
pip install -r requirements.txt         # Python dependencies
cp config.yaml.example config.yaml     # then set anthropic_api_key within
cp automator/script.sh.example automator/script.sh  # then edit PYTHON and WORKDIR within
python3 dev/install_service.py          # install the Finder quick action (optional, recommended)
```

Then right-click any PDF or `.webloc` file in Finder and choose **Extract BibLaTeX-Chicago Bibliography (via Claude)**. See [Setup](#setup) for full configuration details.

## Rationale

[Chicago](https://www.chicagomanualofstyle.org/tools_citationguide/citation-guide-1.html) is the bibliography style typically used in the humanities, cherished for its attention to source and transmission details.

The immense number of types and fields in the [BibLaTeX-Chicago](https://ch.mirrors.cicku.me/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf) package makes Zotero-like auto-creation and auto-fill harder to reproduce reliably by hand.

With or without BibDesk, this agent enhances BibLaTeX-Chicago writing workflows by providing Zotero-like auto-creation and auto-fill capabilities for new bibliographic materials, whether in the form of PDFs or online-only sources.

Using alternative styles (e.g., APA) would involve only minor modifications to the prompts and context; it is left as a trivial exercise for the reader.

## Functionality

1. Accepts one or more PDF and/or `.webloc` files as input.
2. For a PDF: runs OCR if necessary, after prompting the user to select the text language, then extracts text from the first page (~450 words), the last page (~150 words), running headers/footers from each page, and embedded metadata.
3. Sends all of the above to the Claude API together with the cached context prefix — the house-style guidelines, the entry-type template, the field reference, and the worked-example corpora — and asks Claude to generate a BibLaTeX-Chicago entry.
4. Strips any field the guidelines forbid (ISSN, keywords, or `Url`/`Urldate` on an entry that is neither `@Online` nor undated) that Claude included anyway. A structural safeguard, because the prompt can be overgenerous.
5. If required or desired fields for the entry type are still missing, searches CrossRef and, as a fallback, Google Scholar (via ScrapingDog) for the work, then merges any fields found via a second Claude pass.
6. Audits the entry for fields filled from Claude's training-data recollection rather than the source text—a genuine failure mode with academic works Claude may recognize.
7. Re-checks CrossRef/Scholar for recollection-based or missing container-level fields (editor, publisher, date). A conflicting value is applied automatically only when it comes from CrossRef and strictly completes the entry.
8. Validates brace balance before saving.
9. Saves the entry—with a BibDesk `bdsk-file` bookmark to the source PDF or `.webloc` file—either directly into BibDesk (if `autofile_bibdesk` is enabled) or to the staging file (`main_bib_file`) for later import.
10. On validation failure, saves the raw entry to `failed_bib_file` and sends a macOS notification.

With `autofile_bibdesk` disabled, the staging file can be periodically imported into BibDesk; source links will already be intact thanks to the embedded bookmark.

### Extraction and Reconciliation Flow

```mermaid
flowchart TD
    A[PDF or .webloc file] --> B{Source type}
    B -- PDF --> C1[Extract text: first/last page,<br/>headers/footers, embedded metadata]
    B -- .webloc --> C2[Fetch page: body text,<br/>citation_/og/JSON-LD metadata]
    C1 --> D[Claude: initial extraction]
    C2 --> D
    D --> E[Strip forbidden fields]
    E --> F{Required/desired<br/>fields missing?}
    F -- yes --> G[CrossRef / Google Scholar search]
    G --> H[Claude: merge enrichment fields]
    F -- no --> I[Claude: grounding audit]
    H --> I
    I --> J{Recollection-based or<br/>missing container fields?}
    J -- yes --> K[CrossRef / Google Scholar re-check]
    K --> L{CrossRef match that is a<br/>strict completion?}
    L -- yes --> M[Claude: merge completion]
    L -- no --> N[Leave as-is, flag unresolved]
    M --> O[Validate brace balance]
    N --> O
    J -- no --> O
    O --> P{Unresolved fields<br/>remain?}
    P -- yes --> Q[Save + flag amber in BibDesk]
    P -- no --> R[Save entry]
```

![Progress window](screenshot.png)

## Setup

### 1. Install System Dependencies

```bash
# OCR support (optional but recommended for scanned PDFs)
brew install ocrmypdf
```

### 2. Create a Python Environment and Install Dependencies

```bash
conda create -n biblio-ai python=3.11   # or use venv
conda activate biblio-ai
pip install -r requirements.txt
```

`requirements.txt` includes:

- `anthropic`—Claude API client.
- `pypdf`—PDF text extraction.
- `pyyaml`—configuration.
- `pyobjc-framework-Cocoa`—macOS file bookmarks for BibDesk integration.
- `requests`—fetching `.webloc`-bookmarked pages.
- `beautifulsoup4`—parsing fetched pages for text and metadata.

### 3. Configure

Edit `config.yaml`:

```yaml
anthropic_api_key: "sk-ant-..." # your Anthropic API key
main_bib_file: "~/Desktop/biblio-staging.bib" # output file ("staging output")
failed_bib_file: "~/Desktop/biblio-failed.bib" # error file
```

The other paths (`pdf_in_folder`, `pdf_out_folder`, `template_file`, `claude_md_file`) can be left untouched or adjusted to your setup. The optional `ref_file` key (set to `prompt-context/biblatex-chicago-notes-ref.md`) loads the condensed reference.

The optional `example_files` key loads two worked-example corpora from the biblatex-chicago package's own documentation:

- `notes-test.bib` — the package's annotated test suite (203 entries, 32 of the ~40 entry types), where nearly every entry's `annote` explains _why_ that type and those fields were chosen. Converted to Unicode and normalized for this project.
- `cms-notes-intro-guide.md` — prose grouping entry types by kind of source, derived by `dev/extract_intro.py` from `cms-notes-intro.tex`, which ships with the package rather than being vendored here. Bracketed names point at examples in `notes-test.bib`.

Where the field reference teaches _vocabulary_ (which fields a type takes), these teach _classification_ — what kind of thing a source is, and which type therefore fits. They add ~49,000 tokens to the prompt prefix.

Precedence splits by question, not by file: the corpora are authoritative on what biblatex-chicago supports, `CLAUDE.md` and `biblio-template.bib` on this project's presentation choices. Neither local file can override the package's supported types or fields.

#### External lookup services (optional)

When a source doesn't yield every field the entry type needs, the agent queries external catalogues. Both keys are optional and the pipeline runs without them — it simply flags what it couldn't fill.

```yaml
crossref_email: "you@example.com" # optional; enables CrossRef's faster "polite pool"
scrapingdog_api_key: "" # optional; enables the Google Scholar fallback
```

- **CrossRef** is free and needs no account. Supplying `crossref_email` opts you into the [polite pool](https://api.crossref.org), which is faster and more reliable than the anonymous one; the address should be one monitored by the maintainer.
- **Google Scholar** has no public API, so the fallback goes through [ScrapingDog](https://scrapingdog.com), which is paid — roughly $0.0004/credit on pay-as-you-go ($10 for 25,000 credits), a couple of credits per lookup.

#### Other options

| Key                     | Default             | Effect                                                                                                                                                                                                               |
| ----------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model`                 | `claude-sonnet-4-6` | Claude model. Run `dev/estimate_cost.py --model <id>` to price alternatives first.                                                                                                                                   |
| `max_tokens`            | `4000`              | Ceiling on each response. Entries run 400–500 tokens; the headroom absorbs longer ones.                                                                                                                              |
| `cache_ttl`             | `"1h"`              | Prompt-cache lifetime. See [Cost Estimate](#cost-estimate).                                                                                                                                                          |
| `enrich_missing_fields` | `true`              | Enables the CrossRef/Scholar lookups, the grounding audit, and reconciliation. Setting it `false` leaves only the initial extraction — cheaper and faster, but no enrichment or review of recollection-based fields. |
| `verbose`               | `true`              | Progress messages on stderr.                                                                                                                                                                                         |
| `show_window`           | `false`             | Show the floating progress window by default (`--window`/`--no-window` override it).                                                                                                                                 |
| `window_models`         | sonnet-4-6, opus-5  | Models offered in the progress window's dropdown — see [Quick Action](#macos-quick-action-recommended). Unset hides the dropdown.                                                                                    |
| `window_start_delay`    | `4`                 | Seconds the window waits before the first file so the model can be changed; `0` starts immediately.                                                                                                                  |
| `notifications`         | `false`             | macOS notifications for batch progress and validation failures.                                                                                                                                                      |
| `ocr_threshold`         | `100`               | Words below which a PDF is treated as scanned and sent to OCR.                                                                                                                                                       |
| `ocr_timeout`           | `180`               | Seconds allowed for `ocrmypdf` before giving up.                                                                                                                                                                     |
| `default_ocr_language`  | `eng`               | Tesseract language used when OCR runs non-interactively.                                                                                                                                                             |

### 4. Customize the Extraction Prompt

Edit `CLAUDE.md` to match your bibliographic conventions. At minimum, review:

- The output format and field exclusions (e.g. which fields to omit), including the `@Online`/undated-entry exception that allows `Url`/`Urldate`.
- Title-case rules for any languages you work with.
- Any domain-specific entry types or fields you rely on.

The richer and more specific your `CLAUDE.md`, the more accurately Claude will format entries to your standards.

### 5. Configure the Automator Script

```bash
cp automator/script.sh.example automator/script.sh
```

Edit `automator/script.sh` and set `PYTHON` to the path of your Python executable and `WORKDIR` to the absolute path of this repository. This file is excluded from version control.

### 6. Install the macOS Quick Action

```bash
python3 dev/install_service.py
```

This builds the Automator workflow from `automator/script.sh` and installs it to `~/Library/Services/`, accepting both PDFs and `.webloc` files. Re-run it any time you modify `script.sh`.

### 7. Verify the setup

```bash
python3 dev/test_setup.py
```

Checks dependencies, `config.yaml`, the context files, OCR availability, and — since both drifted twice while this was being built — that `README.md` still names every config key, every CLI flag, and every prompt-context file.

The Quick Action runs `dev/test_setup.py --preflight` before each batch — a fast subset covering dependencies, config, and the context files. It is silent when everything is in order, and aborts with a clear message if not.

## Usage

### macOS Quick Action (Recommended)

Right-click any PDF or `.webloc` file (or a mixed selection of both) in Finder and choose **Extract BibLaTeX-Chicago Bibliography (via Claude)**. The entry is appended to the staging file and copied to BibDesk if `autofile_bibdesk` is enabled.

See [Setup](#5-configure-the-automator-script) for initial configuration. To reinstall after changes to `automator/script.sh`:

```bash
python3 dev/install_service.py
```

The progress window carries a **Model** dropdown listing whatever `window_models` names in `config.yaml`. It holds for `window_start_delay` seconds (4 by default) before the first file, showing a countdown and allowing the operator to switch models. Use `--window` or `--no-window` on the command line to override the config.

Reference works (Grove, the Stanford Encyclopedia, Wikipedia) are where the stronger model earns its keep — see the operator note in `CLAUDE.md`.

### Command Line

```bash
# Process one or more PDFs and/or .webloc files
python3 src/biblio_agent.py path/to/paper.pdf path/to/bookmark.webloc

# Process without saving (print to stdout only)
python3 src/biblio_agent.py path/to/paper.pdf --no-save

# Process all PDFs and .webloc files in pdf-in/ and move them to pdf-out/
python3 src/biblio_agent.py --all

# Write to a custom output file
python3 src/biblio_agent.py path/to/paper.pdf --output custom.bib

# Use a different model for one run (overrides config.yaml)
python3 src/biblio_agent.py path/to/entry.webloc --model claude-opus-5
```

All flags:

| Flag                       | Effect                                                                                                     |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `--all`                    | Process everything in `pdf_in_folder` instead of named files.                                              |
| `--no-save`                | Print to stdout only — no staging file, no BibDesk import.                                                 |
| `--no-move`                | Leave processed files where they are (the Quick Action uses this, since your sources aren't in `pdf-in/`). |
| `--output FILE`            | Write to `FILE` instead of `main_bib_file`.                                                                |
| `--model ID`               | Use a different model for this run.                                                                        |
| `--config FILE`            | Use an alternate config file.                                                                              |
| `--window` / `--no-window` | Force the progress window on or off, overriding `show_window`.                                             |
| `-q`, `--quiet`            | Suppress status messages (sets `verbose: false`).                                                          |

Relative paths in `config.yaml` resolve against the repository, not the working directory, so these commands work from anywhere. A context file named in `config.yaml` but missing from disk is an error at startup.

## Project Structure

```
ostracon-ai/
├── CLAUDE.md             # Bibliographic extraction guidelines — the house style, and the
│                         #   first file in the cached prompt prefix. Stays at the root:
│                         #   Claude Code looks for it there.
├── config.yaml.example   # Configuration template (copy to config.yaml)
├── config.yaml           # Your configuration (gitignored — contains the API key)
├── requirements.txt      # Python dependencies
│
├── src/                  # The pipeline itself
│   ├── biblio_agent.py   # Main orchestrator; run this
│   ├── extract_pages.py  # PDF text extraction with OCR fallback; the shared SourceContent shape
│   ├── web_source.py     # Fetches and extracts bibliographic content from a .webloc's bookmarked page
│   ├── enrich.py         # CrossRef/Google Scholar enrichment and reconciliation, BibTeX field utilities
│   └── progress_window.py # Native macOS floating progress window (--window)
│
├── prompt-context/       # Reference material sent to Claude as the cached prompt prefix
│   ├── biblio-template.bib # One worked example per entry type, in this project's conventions
│   ├── biblatex-chicago-notes-ref.md  # Condensed biblatex-chicago field and entry-type reference
│   ├── notes-test.bib    # The package's annotated test suite (203 entries) — see Third-party material
│   ├── cms-notes-intro-guide.md       # Entry-type guide derived from the package's cms-notes-intro.tex
│   └── biblatex-chicago-fields.md     # Manual §4.2 verbatim; a consultation source, NOT loaded into
│                         #   the prompt — an A/B found no benefit for ~45k extra tokens (dev/ab-findings.md)
│
├── dev/                  # Developer tooling; nothing here runs during extraction
│   ├── test_setup.py     # Checks dependencies, config, context files, and documentation drift
│   ├── estimate_cost.py  # Measures the current prompt-cache cost profile
│   ├── install_service.py # Builds and installs the macOS Quick Action
│   │
│   │                     # Context generators — re-run after a package update, then diff
│   ├── extract_intro.py  # Regenerates cms-notes-intro-guide.md from the package's .tex
│   ├── extract_manual.py # Regenerates biblatex-chicago-fields.md from biblatex-chicago.tex
│   ├── build_template.py # Regenerates biblio-template.bib from real biblio.bib entries
│   │
│   │                     # Legacy-library normalization (see normalization-plan.md)
│   ├── bib_audit.py      # Read-only span scanner and the shared safety gates
│   ├── bib_normalize.py  # Tier A transform; surgical span edits, never re-serialized
│   ├── unwrap_names.py   # Removes whole-field \foreignlanguage wrappers from name fields
│   ├── rewrap_names.py   # Re-applies them per name component, so biber still parses the name
│   ├── bib_bisect.py     # Bisects for an entry that crashes BibDesk
│   ├── normalization-plan.md # Method, decisions, and the current worklist
│   │
│   ├── ab_compare.py     # Scores two extraction runs against curated ground truth
│   └── ab-findings.md    # Results of the §4.2 context experiment
│
├── automator/
│   ├── script.sh.example # Shell script template (copy to script.sh and edit)
│   └── script.sh         # Your local script (gitignored — machine-specific paths)
├── pdf-in/               # Drop PDFs and .webloc files here for batch processing (--all)
└── pdf-out/              # Processed PDFs are moved here (webloc files are typically relocated by BibDesk instead—see below)
```

## BibDesk Integration

By default the agent writes to the file set in `main_bib_file` (`config.yaml`), which you import into BibDesk manually. Each entry includes a `bdsk-file-1` bookmark to the source PDF or `.webloc` file.

Set `autofile_bibdesk: true` in `config.yaml` to skip the staging file entirely. The agent will import each entry directly into BibDesk via AppleScript (opening the staging file in BibDesk if it is not already open).

## Troubleshooting

**Entry saved to `failed_bib_file` instead of staging file.**

The generated entry had unbalanced braces. Open the failed file, fix the entry manually, and add it to the staging file.

**Entry colored amber/orange in BibDesk.**

A field couldn't be safely confirmed: either the grounding audit flagged it as possibly drawn from Claude's background knowledge and no CrossRef/Scholar match resolved it, or a Scholar match (or a CrossRef value) introduced a conflict that was intentionally left unresolved.

**`bdsk-file-1` bookmark not working after import.**

Make sure `pyobjc-framework-Cocoa` is installed in the Python environment used by the Quick Action (check the `PYTHON` path in `automator/script.sh`).

**Quick Action not appearing in Finder.**

Run `python3 dev/install_service.py` and check System Settings → General → Login Items & Extensions to confirm the action is enabled.

**Startup fails with "Context files configured in config.yaml are missing".**

A file named in `claude_md_file`, `template_file`, `ref_file`, or `example_files` isn't where the config says it is. These form the cached prompt prefix, so the agent refuses to start rather than run with incomplete context.

**OCR not working.**

Install `ocrmypdf` via Homebrew. The agent will fall back to direct text extraction if OCR is unavailable. When a scanned PDF is detected, a language selection dialog will appear—pick the language of the scan.

**A scanned PDF yields an almost empty entry, with only a date.**

OCR runs `--skip-text` first, which treats a page as done if it carries any text object at all. Some scans ship a text layer holding nothing but whitespace: ocrmypdf skips every page, exits successfully, and the extractor sees no usable text. Re-run after removing the stale layer or OCRing externally.

## Cost Estimate

Claude API calls dominate. At `claude-sonnet-4-6` rates ($3/$15 per 1M input/output tokens), cache writes cost 1.25× input at the default five-minute TTL, 2× at one hour, and cache reads 0.1×.

The static prefix measures **61,879 tokens** (209,015 chars). Writing it costs $0.23; every later call in the same run reads it back for $0.019.

| Call             | Runs when                                   | First file | Later files |
| ---------------- | ------------------------------------------- | ---------- | ----------- |
| Extraction       | always                                      | $0.25      | $0.03       |
| Grounding audit  | `enrich_missing_fields: true` (default)     | $0.005     | $0.005      |
| Enrichment merge | required/desired fields missing             | $0.03      | $0.03       |
| Reconciliation   | a CrossRef match strictly completes a value | $0.03      | $0.03       |

A clean source costs **~$0.25 for the first file in a run and ~$0.04 for each one after**; if all four calls fire, **~$0.30 and ~$0.09**.

Reconciliation is conservative — a Scholar-sourced conflict, or a CrossRef value that contradicts rather than completes, is flagged for review without reaching that fourth call — so the worst case remains rare.

### Batching and cache TTL

The prefix is written once per run and read thereafter, so cost turns on how often a run starts without a warm cache. At the default five-minute TTL the cache does not survive between separate Quick Action invocations.

| Usage pattern                             | 5-minute TTL | 1-hour TTL |
| ----------------------------------------- | ------------ | ---------- |
| One batch of 10, nothing else that hour   | $0.60        | $0.74      |
| Two batches of 5, 20 minutes apart        | $0.81        | $0.74      |
| 10 single-file invocations across an hour | $2.52        | $0.74      |

The one-hour TTL costs a flat **$0.14 more per cache write** and nothing more per file, so it loses only when a run is genuinely isolated. Any second run within the hour — batch or single — repays the extra write immediately.

To cut the prefix instead: dropping `notes-test.bib` from `example_files` while keeping `cms-notes-intro-guide.md` leaves ~15,500 tokens and a ~$0.08 first file — retaining the entry-type taxonomy while shedding most of the annotated examples.

These figures are produced by `dev/estimate_cost.py`, which measures the real assembled prompt with `count_tokens` rather than restating hardcoded numbers. Re-run it (`python3 dev/estimate_cost.py --model <id>`) after changing the prefix or model.

External APIs are negligible: CrossRef is free, and the ScrapingDog fallback runs about $0.0004/credit at a couple of credits per lookup.

## Why "Ostracon"?

> An ostracon (Greek: ὄστρακον /ós.tra.kon/, plural ὄστρακα /ós.tra.ka/) is a piece of pottery (or stone), usually broken off from a vase or other earthenware vessel. In archaeology and history, the term refers either to the fragment itself or to a potsherd used for writing or drawing.

## License

Copyright (c) 2026 [yrammos](https://github.com/yrammos). Licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Free for personal use; attribution requested for forks and modifications.

### Third-party material

Three files are drawn from the [biblatex-chicago](https://ctan.org/pkg/biblatex-chicago) package (v2.3b, 2024-04-15), Copyright © 2008–2024 David Fussner, distributed under the [LaTeX Project Public License](https://www.latex-project.org/lppl/).

| File                            | Origin                                                          | Modified                                                                                                                          |
| ------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `notes-test.bib`                | package `doc/` directory                                        | yes — LaTeX accent macros converted to Unicode, double-hyphen ranges converted to single hyphens; no bibliographic content altered |
| `cms-notes-intro-guide.md`      | derived from the package's `cms-notes-intro.tex` by `dev/extract_intro.py` | yes — LaTeX scaffolding stripped, cross-references rendered as plain text; wording unchanged                          |
| `biblatex-chicago-fields.md`    | derived from §4.2 of the package's `biblatex-chicago.tex` by `dev/extract_manual.py` | yes — LaTeX markup stripped, paragraphs reflowed, one heading added per field; wording unchanged                |

The two `.tex` sources are not vendored here: they ship with the package, and the generated files are what this project consumes. Upstream drift therefore surfaces as a diff in the generated file when the script is re-run, which is the artifact that matters.

Each file records its own provenance and modifications in a header, as the LPPL requires. `dev/extract_intro.py` and `dev/extract_manual.py` are included so the derivations can be reproduced or re-run against a newer upstream release.
