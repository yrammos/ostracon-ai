# BibLaTeX-Chicago-Claude (a.k.a. "Ostracon")

A Claude-powered macOS agent for generating BibLaTeX-Chicago entries from PDF files and `.webloc` bookmarks to online-only publications.

## Quick Start

```bash
brew install ocrmypdf                   # OCR support (optional)
pip install -r requirements.txt         # Python dependencies
cp config.yaml.example config.yaml     # then set anthropic_api_key within
cp automator/script.sh.example automator/script.sh  # then edit PYTHON and WORKDIR within
python3 install_service.py              # install the Finder quick action (optional, recommended)
```

Then right-click any PDF or `.webloc` file in Finder and choose **Extract BibLaTeX-Chicago Bibliography (via Claude)**. See [Setup](#setup) for full configuration details.

## Rationale

[Chicago](https://www.chicagomanualofstyle.org/tools_citationguide/citation-guide-1.html) is the bibliography style typically used in the humanities, cherished for its attention to source and transmission history, to various types of authorship, and to detail in general. Its "notes and bibliography" variant relies on footnotes or endnotes rather than inline ("author-date") references, and is the more common one in music theory and musicology.

The immense number of types and fields in the [BibLaTeX-Chicago](https://ch.mirrors.cicku.me/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf) package makes Zotero unsustainable as a bibliography manager, with the otherwise excellent [Better BibTeX](https://retorque.re/zotero-better-bibtex/) extension only alleviating a painful experience. For many writers, [BibDesk](https://bibdesk.sourceforge.io) is the only macOS manager that elegantly navigates the style's ontological complexity. Others avoid managers altogether and prefer to edit `.bib` files directly within a text editor.

With or without BibDesk, this agent enhances BibLaTeX-Chicago writing workflows by providing Zotero-like auto-creation and auto-fill capabilities for new bibliographic materials, whether in the form of PDF files or `.webloc` links. Thanks to its reliance on AI and elaborate prompting, the agent should not only match Zotero but actually outperform it in most cases.

Using alternative styles (e.g., APA) would involve only minor modifications to the prompts and context; it is left as a trivial exercise for the reader.

## Functionality

1. Takes one or more PDF and/or `.webloc` files as input.
2. For a PDF: runs OCR if necessary, after prompting the user to select the text language, then extracts text from the first page (~450 words), the last page (~150 words), running headers/footers from key pages (volume, issue, page range, chapter number), and embedded PDF metadata (title, author, subject, creation date). For a `.webloc` file: resolves the bookmarked URL, fetches the page, and extracts its main body text along with `citation_*`/`og:*`/JSON-LD metadata—the online equivalent of a PDF's embedded metadata.
3. Sends all of the above to the Claude API with project guidelines and a reference template, and returns a BibLaTeX-Chicago entry. An entry sourced from a `.webloc` populates `Url`/`Urldate`—the one case the guidelines allow this, since there is no PDF to file as the entry's locator instead.
4. Strips any field the guidelines forbid (e.g. ISSN, keywords, or a `Url` on a PDF-sourced entry) that Claude included regardless—a structural safeguard rather than a prompt instruction alone, since the latter isn't followed reliably (a PDF whose own text states a URL, for instance, can still tempt Claude into adding one).
5. If required or desired fields for the entry type are still missing, searches CrossRef and, as a fallback, Google Scholar (via ScrapingDog) for the work, then merges any fields found via a second Claude call.
6. Audits the entry for fields that may have been filled from Claude's background/training-data recollection rather than the source text—a real failure mode with academic works Claude may "recognize."
7. Re-checks CrossRef/Scholar for any recollection-based or still-missing container-level fields (editor, series, publisher, location, date). A conflicting external value is only ever applied automatically when it comes from CrossRef (DOI-keyed, high trust) *and* is a strict completion of the claimed value—spelling out an abbreviated first name, or adding a missing co-author—never a genuine contradiction, and never from Google Scholar's fuzzier match regardless of how confident it looks. Anything short of that is left untouched and flagged for manual review rather than silently overwritten, since a wrong field is worse than an empty one.
8. Validates brace balance before saving.
9. Saves the entry—with a BibDesk `bdsk-file` bookmark to the source PDF or `.webloc` file—either directly into BibDesk (if `autofile_bibdesk` is enabled) or to the staging file (`main_bib_file` in `config.yaml`). Entries with unresolved flagged fields are colored amber in BibDesk for manual review (only possible when `autofile_bibdesk` is enabled).
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

The other paths (`pdf_in_folder`, `pdf_out_folder`, `template_file`, `claude_md_file`) can be left untouched or adjusted to your setup. The optional `ref_file` key (set to `biblatex-chicago-notes-ref.md` by default) loads a condensed biblatex-chicago field reference into the Claude prompt to improve extraction quality; remove or comment it out to omit it.

Set `notifications: true` to enable macOS notifications. When enabled, the agent sends notifications for batch progress updates and validation failures. Defaults to `false`.

### 4. Customize the Extraction Prompt

Edit `CLAUDE.md` to match your bibliographic conventions. At minimum, review:

- The output format and field exclusions (e.g. which fields to omit), including the `.webloc`-only exception that allows `Url`/`Urldate`.
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
python3 install_service.py
```

This builds the Automator workflow from `automator/script.sh` and installs it to `~/Library/Services/`, accepting both PDFs and `.webloc` files. Re-run it any time you modify `script.sh`.

## Usage

### macOS Quick Action (Recommended)

Right-click any PDF or `.webloc` file (or a mixed selection of both) in Finder and choose **Extract BibLaTeX-Chicago Bibliography (via Claude)**. The entry is appended to the staging file and copied to the clipboard.

See [Setup](#5-configure-the-automator-script) for initial configuration. To reinstall after changes to `automator/script.sh`:

```bash
python3 install_service.py
```

### Command Line

```bash
# Process one or more PDFs and/or .webloc files
python biblio_agent.py path/to/paper.pdf path/to/bookmark.webloc

# Process without saving (print to stdout only)
python biblio_agent.py path/to/paper.pdf --no-save

# Process all PDFs and .webloc files in pdf-in/ and move them to pdf-out/
python biblio_agent.py --all

# Write to a custom output file
python biblio_agent.py path/to/paper.pdf --output custom.bib
```

## Project Structure

```
ostracon-ai/
├── biblio_agent.py       # Main orchestrator
├── extract_pages.py      # PDF text extraction with OCR fallback; the shared SourceContent shape
├── web_source.py         # Fetches and extracts bibliographic content from a .webloc's bookmarked page
├── enrich.py             # CrossRef/Google Scholar enrichment and reconciliation, BibTeX field utilities
├── install_service.py    # Builds and installs the macOS Quick Action
├── config.yaml           # Configuration (API key, paths, model)
├── requirements.txt      # Python dependencies
├── CLAUDE.md             # Bibliographic extraction guidelines for Claude
├── biblio-template.bib   # Reference template for BibLaTeX-Chicago types/fields
├── biblatex-chicago-notes-ref.md  # Condensed biblatex-chicago field reference (sent to Claude)
├── automator/
│   ├── script.sh.example # Shell script template (copy to script.sh and edit)
│   └── script.sh         # Your local script (gitignored—machine-specific paths)
├── pdf-in/               # Drop PDFs and .webloc files here for batch processing (--all)
└── pdf-out/              # Processed PDFs are moved here (webloc files are typically relocated by BibDesk instead—see below)
```

## BibDesk Integration

By default the agent writes to the file set in `main_bib_file` (`config.yaml`), which you import into BibDesk manually. Each entry includes a `bdsk-file-1` bookmark to the source PDF or `.webloc` file so it resolves correctly after import.

Set `autofile_bibdesk: true` in `config.yaml` to skip the staging file entirely. The agent will import each entry directly into BibDesk via AppleScript (opening the staging file in BibDesk if it is not already open) and immediately trigger BibDesk's auto-file to move the source file to your papers folder. When this happens, the agent's own move to `pdf_out_folder` is skipped for that file, since BibDesk has already relocated it.

## Troubleshooting

**Entry saved to `failed_bib_file` instead of staging file.**

The generated entry had unbalanced braces. Open the failed file, fix the entry manually, and add it to the staging file.

**Entry colored amber/orange in BibDesk.**

A field couldn't be safely confirmed: either the grounding audit flagged it as possibly drawn from Claude's background knowledge and no CrossRef/Scholar match resolved it, or a Scholar match (or a CrossRef match that wasn't a clear completion of the claimed value) came back conflicting. Rather than risk a silent, wrong override, the field is left as extracted and the entry is flagged for you to check by hand.

**`bdsk-file-1` bookmark not working after import.**

Make sure `pyobjc-framework-Cocoa` is installed in the Python environment used by the Quick Action (check the `PYTHON` path in `automator/script.sh`).

**Quick Action not appearing in Finder.**

Run `python3 install_service.py` and check System Settings → General → Login Items & Extensions to confirm the action is enabled.

**OCR not working.**

Install `ocrmypdf` via Homebrew. The agent will fall back to direct text extraction if OCR is unavailable. When a scanned PDF is detected, a language selection dialog will appear—pick the language of the document so Tesseract uses the correct model. In quiet/automation mode, the language defaults to `eng`; set `default_ocr_language` in `config.yaml` to override (e.g. `rus`, `deu`, `fra`). `.webloc` sources never need OCR, since their text comes from the fetched page rather than a scanned image.

## Cost Estimate

Costs are dominated by Claude API calls (`config.yaml`'s `model`, currently `claude-sonnet-4-6` at $3/$15 per 1M input/output tokens). Up to four calls happen per file, and the ~9,000 tokens of shared context (`CLAUDE.md` + `biblio-template.bib` + `biblatex-chicago-notes-ref.md`) is sent as a cached prompt prefix (`cache_control: {type: "ephemeral"}`), so only the first call in a run pays full price for it—every later call, including later files in the same batch, reads it back at a 90% discount:

| Call | Runs when | Approx. cost (cache hit) |
|---|---|---|
| Initial extraction | always | ~$0.044 (first file in a run) / ~$0.013 (later files, context already cached) |
| Grounding audit | always, if `enrich_missing_fields: true` (default) | ~$0.005 (doesn't use the shared context) |
| Enrichment merge | only if required/desired fields are missing from the source | ~$0.007 |
| Reconciliation | only if a flagged field has a CrossRef match that's a strict completion of the claimed value | ~$0.008 |

- **Best case** (clean, well-populated source): extraction + audit ≈ **$0.05/file** for the first file in a run, **~$0.02/file** for subsequent ones.
- **Worst case** (missing fields, needs reconciliation): all four calls ≈ **$0.06/file** for the first file, **~$0.03/file** for subsequent ones.

Reconciliation is deliberately conservative: a Scholar-sourced conflict, or a CrossRef match that genuinely contradicts rather than completes the claimed value, is flagged for manual review without ever reaching this fourth call, so in practice it fires less often than the worst case above suggests.

Since the Finder Quick Action is normally invoked on a multi-file selection in a single run (`biblio_agent.py --window --no-move <files...>`), most files in practice land in the cheaper "subsequent" tier—only the very first file pays the full cache-write price. External API costs are negligible on top of this: CrossRef is free; the Google Scholar fallback (ScrapingDog) runs at roughly $0.0004/credit on the pay-as-you-go plan ($10 for 25,000 credits), and each lookup uses only a couple of credits.

These figures are higher than the pipeline's original ~$0.02-$0.03/file estimate, reflecting the enrichment/audit/reconciliation calls added since—though caching claws back most of that increase for anything beyond the first file in a batch.

## "Ostracon"?

> An ostracon (Greek: ὄστρακον  /ós.tra.kon/, plural ὄστρακα  /ós.tra.ka/) is a piece of pottery (or stone), usually broken off from a vase or other earthenware vessel. In archaeology, ostraca may contain scratched-in words or other forms of writing which may give clues as to the time when the piece was in use.

## License

Copyright (c) 2026 [yrammos](https://github.com/yrammos). Licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Free for personal use; attribution requested for forks and modifications; commercial use prohibited.
