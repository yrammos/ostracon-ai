#!/usr/bin/env python3
"""
Bibliographic extraction agent using Claude API.
Processes PDFs and .webloc files, generating BibLaTeX-Chicago entries.
"""

import sys
import re
import argparse
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import yaml
from anthropic import Anthropic

from extract_pages import extract_pdf
import web_source
import enrich

# Tesseract language codes shown in the OCR language picker.
OCR_LANGUAGES = [
    ("English",    "eng"),
    ("Russian",    "rus"),
    ("German",     "deu"),
    ("French",     "fra"),
    ("Italian",    "ita"),
    ("Spanish",    "spa"),
    ("Greek",      "ell"),
    ("Polish",     "pol"),
    ("Latin",      "lat"),
    ("Ukrainian",  "ukr"),
    ("Czech",      "ces"),
    ("Hungarian",  "hun"),
]

# The single dispatch point for source file type - the only place in this
# pipeline that branches on PDF vs. webloc. Every extractor takes a path and
# returns a SourceContent (or an "Error: ..." string), so everything after
# this lookup is generic.
EXTRACTORS = {
    '.pdf': extract_pdf,
    '.webloc': web_source.extract_webloc,
}


def glob_input_files(folder):
    """All supported source files in folder (PDFs and .weblocs), sorted."""
    return sorted(
        f for suffix in EXTRACTORS for f in Path(folder).glob(f'*{suffix}')
    )


# Model used for sources placed in pdf-in/careful/ when config.yaml sets no
# careful_model of its own - see the reference-work operator note in CLAUDE.md.
DEFAULT_CAREFUL_MODEL = "claude-opus-5"


def glob_batch_files(folder):
    """Source files for an --all run: folder itself plus its careful/
    subfolder, if present (see process_batch's folder-convention note)."""
    files = glob_input_files(folder)
    careful_dir = Path(folder) / 'careful'
    if careful_dir.is_dir():
        files += glob_input_files(careful_dir)
    return sorted(files)


# Config paths are written relative to the repository ("./CLAUDE.md"), so they
# must resolve against it rather than against the working directory - otherwise
# the tool only runs from the repo root, and the Automator wrapper's `cd
# "$WORKDIR"` is load-bearing rather than a convenience.
# This module lives in src/, so the repository root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Config keys holding a path that should be made absolute at load time, so every
# downstream consumer receives an absolute path without repeating this logic.
_PATH_KEYS = (
    'claude_md_file', 'template_file', 'ref_file',
    'pdf_in_folder', 'pdf_out_folder',
    'main_bib_file', 'failed_bib_file',
)

# Of those, the ones naming a file that must exist when the key is configured.
# A missing one is fatal rather than a warning: load_context_files() would
# otherwise substitute an empty string, and the run would silently produce
# entries with no template, field reference, or worked examples - degraded
# output that looks like success.
_REQUIRED_IF_SET = ('claude_md_file', 'template_file', 'ref_file')


def resolve_path(value):
    """Expand ~ and anchor a relative path to the repository root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def response_text(message):
    """The text of a response, ignoring any non-text blocks.

    `message.content` is a list of blocks whose first element is not
    necessarily text: models with thinking enabled put a ThinkingBlock first,
    so indexing content[0].text raises AttributeError. Adaptive thinking is on
    by default from Claude Sonnet 5 and Opus 4.7 onward, which makes the naive
    access a migration tripwire rather than a theoretical one.
    """
    return "".join(b.text for b in message.content if b.type == "text")


# show_window, window_models and notifications govern presentation only and
# moved under a single `interface:` config block. The bare top-level key of
# the same name is still honored for one release (warns once via stderr) so
# an existing config.yaml keeps working; see README.
_deprecated_interface_keys_warned = set()


def _interface_setting(config, key, default=None):
    """Look up a presentation-only setting, preferring config['interface']
    over the deprecated top-level key of the same name."""
    interface = config.get('interface') or {}
    if key in interface:
        return interface[key]
    if key in config:
        if key not in _deprecated_interface_keys_warned:
            _deprecated_interface_keys_warned.add(key)
            print(
                f"⚠ config key '{key}' is deprecated; move it under 'interface:' "
                f"(see README). The top-level key will stop being read in the "
                f"next release.",
                file=sys.stderr,
            )
        return config[key]
    return default


class BiblioAgent:
    def __init__(self, config_path="config.yaml"):
        """Initialize the agent with configuration."""
        self.config = self.load_config(config_path)
        self.client = Anthropic(api_key=self.config['anthropic_api_key'])
        self._progress_callback = None  # set to win.make_callback() in windowed path

    def _log(self, message: str, level: str = 'info') -> None:
        """Emit a progress message to stderr (if verbose) and/or the progress window."""
        if self.config.get('verbose'):
            print(message, file=sys.stderr)
        if self._progress_callback:
            self._progress_callback(message, level)

    def load_config(self, config_path):
        """Load configuration from YAML file.

        Path-valued keys are resolved to absolute paths here (see
        resolve_path), and any configured-but-missing context file is reported
        as an error rather than silently dropped.
        """
        config_path = resolve_path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                "Please create config.yaml from the template."
            )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Validate required fields
        if not config.get('anthropic_api_key') or config['anthropic_api_key'] == 'YOUR_API_KEY_HERE':
            raise ValueError(
                "Please set your Anthropic API key in config.yaml\n"
                "Get one at: https://console.anthropic.com/settings/keys"
            )

        for key in _PATH_KEYS:
            if config.get(key):
                config[key] = str(resolve_path(config[key]))

        # example_files entries are either a bare path or {path, label}.
        resolved_examples = []
        for spec in config.get('example_files') or []:
            if isinstance(spec, dict):
                spec = {**spec, 'path': str(resolve_path(spec['path']))}
            else:
                spec = str(resolve_path(spec))
            resolved_examples.append(spec)
        if resolved_examples:
            config['example_files'] = resolved_examples

        self._verify_context_files(config, config_path)
        return config

    @staticmethod
    def _verify_context_files(config, config_path):
        """Fail loudly when a configured context file is missing.

        Every one of these contributes to the cached prompt prefix. Treating an
        absent file as a warning means a typo or a renamed file yields a full
        run whose output is quietly worse - the failure mode this check exists
        to prevent. A key that isn't configured at all is fine; ref_file and
        example_files are genuinely optional.
        """
        missing = [
            (key, config[key]) for key in _REQUIRED_IF_SET
            if config.get(key) and not Path(config[key]).exists()
        ]
        for spec in config.get('example_files') or []:
            path = spec['path'] if isinstance(spec, dict) else spec
            if not Path(path).exists():
                missing.append(('example_files', path))

        if missing:
            listed = "\n".join(f"  {key}: {path}" for key, path in missing)
            raise FileNotFoundError(
                f"Context files configured in {config_path} are missing:\n{listed}\n\n"
                "These are sent to Claude as the cached prompt prefix; running "
                "without them silently degrades extraction quality. Fix the "
                "paths, restore the files, or remove the keys to run without them."
            )

    def load_context_files(self):
        """Load the guidelines, template, and optional reference/example files.

        Existence was already checked in load_config(), so a key that is set
        here has a readable file behind it; an unset optional key yields an
        empty string.
        """
        def read(key):
            path = self.config.get(key)
            if not path:
                return ""
            with open(path) as f:
                return f.read()

        context = {
            'claude_md': read('claude_md_file'),
            'template': read('template_file'),
            'ref': read('ref_file'),
        }

        # Optional worked-example corpora, in the order given. These are the
        # biblatex-chicago package's own annotated test suite and entry-type
        # guide: they teach TYPE DISCRIMINATION (why a source is @Inproceedings
        # rather than @Incollection) in a way the field manifests above can't.
        context['examples'] = []
        for spec in self.config.get('example_files') or []:
            path = Path(spec['path'] if isinstance(spec, dict) else spec)
            label = spec.get('label', path.name) if isinstance(spec, dict) else path.name
            with open(path) as f:
                context['examples'].append((label, f.read()))

        return context

    def _static_context_block(self, context):
        """Build the static guidelines/template/reference text shared across
        the extraction, enrichment, and reconciliation prompts.

        Kept byte-identical across calls (same source files, same order) so
        it can be cached as a stable prefix - see _cached_message_content().
        """
        parts = []
        if context['claude_md']:
            parts.append(f"<guidelines>\n{context['claude_md']}\n</guidelines>")
        if context['template']:
            parts.append(f"<reference_template>\n{context['template']}\n</reference_template>")
        if context['ref']:
            parts.append(f"<biblatex_chicago_reference>\n{context['ref']}\n</biblatex_chicago_reference>")

        for label, text in context.get('examples') or []:
            parts.append(f'<worked_examples source="{label}">\n{text}\n</worked_examples>')

        if not parts:
            return None

        preamble = (
            "Project guidelines, reference template, and biblatex-chicago field "
            "reference for BibLaTeX-Chicago entries:"
        )
        if context.get('examples'):
            # The upstream corpora follow the package's own house style, which
            # diverges from this project's on several points (they split
            # title/subtitle - which we now also do - but they also carry
            # `url` on print entries, `keywords`, `annote`, and the legacy
            # `school`/`address` aliases). Without this precedence note their
            # 200+ worked entries read as authoritative and would quietly
            # override the guidelines above.
            preamble += (
                "\n\nThe <worked_examples> blocks are the biblatex-chicago package's own "
                "annotated corpora. Use them for ENTRY-TYPE CLASSIFICATION at step 1 - "
                "working out what kind of thing this source is and which type therefore "
                "fits - not only as a tie-breaker between types you have already narrowed "
                "down to. The entry-type guide is organised by kind of source, and its "
                "\"Online materials\" section governs the access-mode question in "
                "particular (an online edition of a printed work keeps its print-equivalent "
                "type; only material with no print counterpart is @Online). The test "
                "suite's `annote` fields state what each source is and why its type and "
                "fields follow. Then use the same corpora to settle the harder boundaries "
                "between confusable types.\n\n"
                "Precedence splits by question, not by file. These corpora ARE the "
                "package's documentation, so they are authoritative on what "
                "biblatex-chicago supports: which entry types exist, what each means, "
                "which fields it takes, and how entrysubtype/relatedtype/\\bibstring work. "
                "<guidelines> and <reference_template> are authoritative only on this "
                "project's PRESENTATION choices where the package allows more than one - "
                "fields to omit, when Url/Urldate may appear, title case, name format, "
                "Title/Subtitle splitting. On a style question the guidelines win; on a "
                "what-does-the-package-support question the corpora win.\n\n"
                "<reference_template> covers only 20 of the ~40 entry types and is NOT an "
                "allowlist: choose whichever type genuinely fits, including ones it omits "
                "(@Letter, @CustomC, @Audio, @Artwork, @Manual, @Booklet, @Bookinbook, "
                "@Dataset, @Standard, @Performance, @Patent, @Image, the @Mv* types, and "
                "the legal types listed in <biblatex_chicago_reference>). Never force a "
                "source into a template type when a better-fitting one exists.\n\n"
                "Ignore the corpora's use of `url` on printed works, `keywords`, `annote`, "
                "and the legacy `school`/`address` aliases (use `institution` and "
                "`location`). Never copy an example's data into an entry: they illustrate "
                "form, not content."
            )
        return preamble + "\n\n" + "\n\n".join(parts)

    def _cached_message_content(self, context, dynamic_text):
        """Build a messages[].content value, marking the shared static
        context as an ephemeral prompt-cache breakpoint when present.

        Repeated calls within a run (and across PDFs in the same batch) reuse
        this cached prefix instead of paying full input price for it on every
        call.

        The 1-hour TTL costs 2x base input to write against the default
        5-minute tier's 1.25x, but the prefix is large enough (~62k tokens)
        that avoiding a re-write dominates: a write is ~$0.23 at the short
        TTL, a read ~$0.019. The Finder Quick Action is typically invoked on
        one file at a time, minutes apart, so a 5-minute cache expires between
        invocations and every file pays a full write. At 1 hour the surcharge
        is a flat ~$0.14 per write and any second run within the hour repays
        it - only a genuinely isolated single batch is cheaper on the short
        tier. See the README's Cost Estimate section.
        """
        static_block = self._static_context_block(context)
        if not static_block:
            return dynamic_text
        cache_ttl = self.config.get('cache_ttl', '1h')
        cache_control = {"type": "ephemeral"}
        if cache_ttl:
            cache_control["ttl"] = cache_ttl
        return [
            {"type": "text", "text": static_block, "cache_control": cache_control},
            {"type": "text", "text": dynamic_text},
        ]

    def build_prompt(self, content):
        """Build the prompt for Claude from a SourceContent (PDF, webpage, ...).

        content.label supplies the one sentence naming the source kind;
        content.url - set only for online-only sources with no PDF to file -
        is the single behavioral fork between source types, added as data
        (step 9 below) rather than as a separate code path.
        """
        source_desc = (
            "the first 2 pages and last page of the PDF" if content.label == "PDF"
            else f"the {content.label}"
        )
        prompt = f"""I need you to extract bibliographic information from a {content.label} and create a BibLaTeX entry using the biblatex-chicago package (notes and bibliography style).

Here is the extracted text from {source_desc}:

<source_text>
{content.text}
</source_text>

"""

        if content.metadata:
            metadata_lines = "\n".join(f"{k}: {v}" for k, v in content.metadata.items())
            prompt += f"""Here is metadata associated with the {content.label} itself. It may be incomplete, or
absent for fields it doesn't cover, but is a first-party signal from the source
itself - unlike the body text, it sometimes carries information a chapter-only
excerpt's text won't (e.g. an embedded Author field):

<source_metadata>
{metadata_lines}
</source_metadata>

"""

        prompt += """Please:
1. Identify the publication type (@Book, @Article, @InCollection, etc.). Work
   out what KIND of source this is first, consulting the <worked_examples>
   corpora above - the entry-type guide is organised by kind of source, and
   the test suite's `annote` fields say why each source takes the type it
   does. Only once you have narrowed the field should you weigh the specific
   distinctions below. Look
   for markers showing this excerpt is only PART of a larger container work -
   e.g. "Chapter 1"/"chapter one", a numbered/titled heading distinct from a
   running-header book title, or a paper within a named conference
   proceedings volume - rather than treating the whole excerpt as a
   standalone work. When that's the case:
   - Use @Inbook if it's one chapter of a book sharing the same author(s)
     throughout.
   - Use @Incollection if it's one chapter of an edited volume where
     different chapters have different authors (an Editor field for the
     volume, distinct from this chapter's Author).
     An editor's name on the title page is NOT what decides this. Plenty of
     single-author volumes are edited by someone else - a selection from one
     writer's work, a collected-essays volume, a critical edition - and those
     are @Inbook WITH an Editor field, not @Incollection. The question is
     always who wrote the OTHER contributions: several hands means
     @Incollection, one hand throughout means @Inbook however prominent the
     editor is. A title page reading "<Author>, <Title>, edited by <Editor>",
     or a series list on the endpapers naming only that one author's books,
     both point to @Inbook.
   - Use @Inproceedings if it's one paper within a conference proceedings
     volume (Booktitle = the proceedings volume, Booktitleaddon = the
     conference name/location if given). The discriminator against
     @Incollection is a genuinely published proceedings volume with its own
     title (often "Proceedings of..." or naming the conference) - not merely
     that the piece originated as a conference talk. If the paper was
     presented at a conference but was NEVER collected into a published
     proceedings volume, it is @Unpublished instead, not @Inproceedings (see
     the guidelines for the Note field convention this requires).
   - In all these cases, set Title to the SPECIFIC piece's own title (e.g.
     "Manifesto"), and Booktitle to the overall container's title (often
     visible in a running header, distinct from the piece's own heading) -
     do NOT use the container's title as the entry's Title when the excerpt
     is clearly one part of a larger work.
   - Booktitle, Booktitleaddon, Eventtitle, Series and the volume's Editor
     must come from the CONTAINER presenting itself as such: a title page, a
     running header or footer, a half-title, a copyright page, a "Proceedings
     of ..." or series statement. A book, journal or conference NAMED IN THE
     BODY TEXT - discussed, quoted, cited, or sitting in a reference list -
     is a work this piece talks about, not the volume it appears in, and must
     never be used for these fields. The two are easy to confuse precisely
     because a chapter's opening pages tend to cite the literature heavily.
     If nothing in the given text presents itself as the container, OMIT
     Booktitle rather than supplying a plausible one, exactly as instructed
     for Location at step 5. An @Incollection or @Inproceedings that is
     missing its Booktitle is reported as incomplete and can be finished by
     hand; one carrying a confident wrong Booktitle is not reported at all.
   - Exception: if the piece is UNTITLED supplemental material (a generic
     preface, foreword, introduction, or index with no title of its own,
     written by someone other than the book's main author) rather than a
     titled chapter, use @Suppbook instead (or @Suppcollection for an edited
     volume) - in that case Title correctly holds the BOOK's own title, with
     Bookauthor (or Editor/Editora) distinguishing the book's real author
     from the supplement's Author. Only use @Inbook/@Incollection when the
     piece has its own distinct title.
   If instead the whole source IS the complete work (not one part of a
   larger container) and it has editor(s) but no single author of its own -
   e.g. an edited volume's own landing page, credited "Edited by X and Y"
   with no Author - use @Collection (or @MvCollection/@Reference as
   appropriate), with the editor(s) in the Editor field taking the primary
   name role. Do NOT put editor names into Author in this case. Use
   @Proceedings instead of @Collection specifically when the volume itself
   is a published conference proceedings (same discriminator as
   @Inproceedings vs @Incollection above - a named conference/proceedings
   title, not just an edited-volume structure).
   For unpublished academic work, distinguish @Thesis (a dissertation
   submitted to a degree-granting institution - Type = {\bibstring{phdthesis}}
   or {\bibstring{mastersthesis}}, Institution = the institution), @Report (an
   institutional/technical/research report NOT submitted for a degree - Type
   = {\bibstring{techreport}} or a similarly descriptive bibstring), and
   @Unpublished (anything else unpublished, including a conference paper with
   no proceedings volume - see the guidelines for the required Note field).
   For a review of another work (rather than a standalone article), use
   @Review and encode the reviewed work's title/author directly into Title
   per the convention in the guidelines and demonstrated in
   biblio-template.bib's Dunsby1997 entry - do not use a generic @Article for
   a review.
2. Extract all relevant bibliographic fields
3. Split a colon-separated title into Title (before the colon) and Subtitle
   (after it), omitting the colon itself - biblatex-chicago supplies it. Same
   for Booktitle/Booksubtitle and Maintitle/Mainsubtitle. Do NOT split when the
   colon sits inside a quoted/emphasised sub-phrase (e.g. a reviewed work's
   title in an @Review entry) rather than at this entry's own title boundary,
   nor when the two halves are joined by a question mark or full stop instead
   of a colon - in those cases keep the title whole and use Shorttitle.
4. Pay special attention to volume, issue/number, page range, and chapter number.
   These are frequently printed in running headers or footers rather than in the
   main body text - check the "HEADERS/FOOTERS FROM KEY PAGES" section above (if
   present) in addition to the BEGINNING/END sections, since this information can
   appear on any of the first few or last few pages, not just the very first or
   very last page.
5. For Location (place of publication, required for @Book/@Collection/@Inbook/
   @Incollection/etc.), look for the publisher's imprint line, typically on the
   title page or copyright page - e.g. "Cambridge, MA: MIT Press" or "New York:
   Oxford University Press". Use the first city given if more than one is listed.
   Do not guess a city from the publisher's name alone; only use a city actually
   printed on the page. If genuinely not stated anywhere in the given text, omit
   the field rather than inventing one.
6. Format as a single BibLaTeX entry using biblatex-chicago standards
7. Use a citation key in the format: AuthorYEAR (e.g., Smith2023)
8. Use single hyphens (-) for all ranges (pages, dates, etc.)
9. Do NOT include these fields: ISSN, ISBN, keywords, reference, devonthink
10. Omit any field you cannot populate from the given text/metadata entirely -
   do not include it with an empty value (e.g. do not write `Langid = {}` for
   a field you have no data for). Only include fields that actually apply.
"""

        # The guidelines ask for date-added/date-modified stamps but have no way
        # to obtain the current time - you cannot run `date`, and left to guess
        # you will produce a plausible-looking but wrong timestamp. Supply it.
        now = datetime.now().astimezone()
        prompt += f"""11. The current date and time is {now.strftime('%Y-%m-%d %H:%M:%S %z')}.
   Use exactly this value for both date-added and date-modified. Do not infer
   a date from the publication itself or from anything in the source text.
"""

        if content.url:
            prompt += f"""12. This work has no PDF; it is published at the web address below.
   (The source text above describes it, but may have come from a
   bibliographic registry rather than from that address itself.)
   Identify the publication type from the work itself as
   usual (step 1) - a webpage may still be a printable book, article, etc.
   with its own type, not necessarily @Online. Set Url to exactly this
   address: {content.url}
   Set Urldate to {now.strftime('%Y-%m-%d')} (today's date).
"""

        prompt += "\nOutput ONLY the BibLaTeX entry, with no additional commentary or explanation."

        return prompt

    def _pdf_extractor_kwargs(self, pdf_path):
        """OCR-related options extract_pdf() needs that only apply to PDFs
        (interactive language prompting, OCR thresholds/timeout).

        interactive_ocr is independent of verbose: verbose controls whether
        progress is printed, interactive_ocr controls whether OCR is allowed
        to stop and ask - a caller with no one there to answer (dev/eval/run.py)
        needs the second off regardless of the first.
        """
        quiet = not self.config.get('verbose', True)
        default_lang = self.config.get('default_ocr_language', 'eng')

        if self.config.get('interactive_ocr', True):
            def language_prompt_fn():
                lang = self._ask_ocr_language(pdf_path.name)
                self._log(f"   OCR language: {lang}", 'info')
                return lang
        else:
            language_prompt_fn = lambda: default_lang

        return dict(
            quiet=quiet,
            language_prompt_fn=language_prompt_fn,
            min_words_threshold=self.config.get('ocr_threshold', 100),
            ocr_timeout=self.config.get('ocr_timeout', 180),
        )

    def extract_bibtex(self, path, batch_info=None, model_override=None):
        """
        Extract bibliographic information from a source file (PDF or .webloc).

        Args:
            path: Path to the source file
            batch_info: Optional (current_index, total) tuple for batch progress display
            model_override: Use this model for this call only, instead of
                config['model'] - for pdf-in/careful/ sources (see process_batch).

        Returns:
            str: BibLaTeX entry or error message
        """
        path = Path(path)

        if not path.exists():
            return f"Error: File not found: {path}"

        extractor = EXTRACTORS.get(path.suffix.lower())
        if extractor is None:
            return f"Error: Unsupported file type: {path.name}"

        if batch_info:
            i, total = batch_info
            self._log(f"\n📄 Processing: {path.name}", 'info')
        else:
            self._log(f"📄 Processing: {path.name}", 'info')

        # Extract source content (PDF pages / webpage - the one place this
        # branches on file type; everything below is generic)
        self._log("   Extracting content...", 'info')
        suffix = path.suffix.lower()
        if suffix == '.pdf':
            kwargs = self._pdf_extractor_kwargs(path)
        elif suffix == '.webloc':
            # crossref_email is for the CrossRef fallback when the page is
            # behind a bot wall. `log` routes web_source's own path reporting
            # (which source produced the text, why each rejected one was
            # rejected, which browser tabs were examined) through _log, so it
            # reaches the progress window and not only stderr - the windowed
            # run shows _log messages exclusively, so a bare print there is
            # invisible exactly when a .webloc is hardest to diagnose.
            kwargs = {
                'crossref_email': self.config.get('crossref_email'),
                'log': lambda msg: self._log(f"   {msg}", 'info'),
            }
        else:
            kwargs = {}
        content = extractor(path, **kwargs)

        if isinstance(content, str):  # error message
            return content

        # Load context files
        self._log("   Loading context...", 'info')
        context = self.load_context_files()

        # Build prompt
        prompt = self.build_prompt(content)

        # Call Claude API
        model = model_override or self.config['model']
        if model_override:
            self._log(f"   Sending to Claude ({model}, careful)...", 'info')
        else:
            self._log("   Sending to Claude...", 'info')

        try:
            message = self.client.messages.create(
                model=model,
                max_tokens=self.config['max_tokens'],
                messages=[
                    {"role": "user", "content": self._cached_message_content(context, prompt)}
                ]
            )

            bibtex_entry = response_text(message)

            # Structural safety net: the prompt above already asks Claude not
            # to include these, but doesn't reliably follow through (e.g. a
            # PDF whose own body text states a URL can still get a Url field
            # despite the instruction against it) - so strip them here rather
            # than trusting prompt compliance alone.
            bibtex_entry, stripped = enrich.strip_forbidden_fields(bibtex_entry)
            if stripped:
                self._log(f"   Stripped disallowed field(s): {', '.join(stripped)}", 'warning')

            needs_color = False
            if self.config.get('enrich_missing_fields', True):
                bibtex_entry = self.enrich_entry(bibtex_entry, content)
                bibtex_entry, needs_color = self.verify_and_flag_recollection(bibtex_entry, content)

            # content.amber is web_source.py's plausibility check flagging a
            # source that's genuine but thin or sparse (no Author/Doi/
            # PublicationDate beyond Urldate, or a short body) - not wrong,
            # just worth a glance. Folded into the same needs_color signal a
            # recollection-audit failure uses, so it reaches BibDesk's review
            # color by exactly the path a PDF's review state does. The
            # additional "% AMBER: ..." comment carries the same flag on the
            # other branch, where entries are appended as text and no color
            # is possible; each survives where the other cannot (see
            # save_entry).
            if content.amber:
                needs_color = True
                self._log(f"   ⚠️  Thin/sparse source ({content.amber_reason}) - flagging for review", 'warning')
                bibtex_entry = f"% AMBER: {content.amber_reason}\n" + bibtex_entry

            if needs_color:
                bibtex_entry = "% NEEDS_COLOR_FLAG\n" + bibtex_entry

            bibtex_entry = f"% Source: {content.label} ({content.url or path.name})\n" + bibtex_entry

            self._log("   Validating entry...", 'info')
            self._log("   ✓ Complete", 'success')

            return bibtex_entry

        except Exception as e:
            return f"Error: {e}"

    def _build_enrich_prompt(self, entry, found_fields):
        """Build a prompt asking Claude to merge externally-sourced fields
        into an entry without touching anything else."""
        fields_str = "\n".join(f"{k} = {v}" for k, v in found_fields.items())
        prompt = f"""This BibLaTeX-Chicago entry is missing some fields that the source
did not contain. Supplementary bibliographic data was found from an external
source (CrossRef and/or Google Scholar) for the following fields:

<supplementary_data>
{fields_str}
</supplementary_data>

Here is the current entry:

<entry>
{entry}
</entry>

"""
        prompt += """Add ONLY the supplementary fields listed above to the entry, formatted
correctly per the guidelines above (e.g. single hyphens for page ranges, correct
field names/casing). Do not change any existing field value. Do not add any
other fields. Output ONLY the corrected BibLaTeX entry, with no additional
commentary."""
        return prompt

    def enrich_entry(self, entry_text, content):
        """
        Fill in bibliographic fields the source didn't supply (volume, issue,
        pages, chapter, etc.) via CrossRef/Google Scholar, then ask Claude to
        merge only those fields into the entry.

        Args:
            entry_text: The BibLaTeX entry produced so far
            content: The SourceContent (PDF or webpage) the entry was drawn from

        Returns the (possibly unchanged) entry text.
        """
        entry_type = enrich.get_entry_type(entry_text)
        fields = enrich.parse_bibtex_fields(entry_text)
        required, desired = enrich.missing_fields(entry_type, fields)
        if not required and not desired:
            self._log(f"   Source: {content.label} (all fields present, no enrichment needed)", 'info')
            return entry_text

        title = enrich.strip_latex(fields.get('title', ''))
        found, field_sources = enrich.gather_enrichment(
            content.text, title, entry_type, fields,
            crossref_email=self.config.get('crossref_email'),
            scrapingdog_api_key=self.config.get('scrapingdog_api_key'),
        )
        if not found:
            self._log(f"   ⚠️  Missing fields not found via CrossRef/Scholar: {', '.join(required + desired)}", 'warning')
            return entry_text

        by_source = {}
        for field, source in field_sources.items():
            by_source.setdefault(source, []).append(field)
        summary = '; '.join(f"{src}: {', '.join(sorted(fs))}" for src, fs in by_source.items())
        self._log(f"   Enriched -- {summary}", 'info')

        context = self.load_context_files()
        prompt = self._build_enrich_prompt(entry_text, found)
        try:
            message = self.client.messages.create(
                model=self.config['model'],
                max_tokens=self.config['max_tokens'],
                messages=[{"role": "user", "content": self._cached_message_content(context, prompt)}],
            )
            merged = self.clean_bibtex(response_text(message))
            valid, _ = self.validate_braces(merged)
            if not valid:
                return entry_text
        except Exception as e:
            self._log(f"   ⚠️  Enrichment merge failed: {e}", 'warning')
            return entry_text

        # Record per-field provenance as a persistent comment so it survives
        # past this run's log, rather than only being visible in the console/window.
        source_fields = sorted(f for f, v in fields.items() if v)
        source_lines = []
        if source_fields:
            source_lines.append(f"{content.label}: {', '.join(source_fields)}")
        for src, fs in by_source.items():
            source_lines.append(f"{src}: {', '.join(sorted(fs))}")
        comment = f"% Sources -- {'; '.join(source_lines)}\n"

        return comment + merged

    def _build_audit_prompt(self, entry, content):
        """Build a prompt asking Claude to self-audit which of its own field
        values are grounded in the given text/metadata versus recalled from
        its own background knowledge of the work."""
        metadata_block = ""
        if content.metadata:
            lines = "\n".join(f"{k}: {v}" for k, v in content.metadata.items())
            metadata_block = f"\n<source_metadata>\n{lines}\n</source_metadata>\n"

        return f"""Here is text extracted from a {content.label}:

<source_text>
{content.text}
</source_text>
{metadata_block}
Here is a BibLaTeX entry produced for this {content.label}:

<entry>
{entry}
</entry>

For each non-empty field in the entry, decide whether its value is explicitly
present in (or directly derivable from) the text or metadata above, or
whether it instead relies on outside/background knowledge about this work
(e.g. recognizing the book or article and recalling its author, publisher,
date, or place from what you already know about it, rather than reading it
from the given text/metadata).

Your ENTIRE reply must be one line, beginning with the marker, and nothing
else - no analysis, no preamble, no reasoning. Emit the marker first:

UNGROUNDED_FIELDS: <comma-separated field names not grounded in the given text/metadata, or NONE>"""

    def _audit_entry_grounding(self, entry_text, content):
        """Ask Claude which fields in entry_text are grounded in the given
        text/metadata vs. recalled from its own background knowledge.

        Returns a list of ungrounded field names (possibly empty).
        """
        prompt = self._build_audit_prompt(entry_text, content)
        message = self.client.messages.create(
            model=self.config['model'],
            # Generous enough that a reply which ignores the one-line format and
            # narrates instead still reaches the marker. At 150 the model would
            # analyse each field in prose, hit the cap mid-sentence, and never
            # emit the marker at all - see the parse-failure branch below.
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        response = response_text(message)

        m = re.search(r'UNGROUNDED_FIELDS:\s*(.+)', response)
        if not m:
            # No marker means the audit did not return a verdict - NOT that
            # every field is grounded. Failing open here silently disarms the
            # one check standing between a recalled value and a saved entry,
            # so treat it as "could not verify" and let the caller flag it.
            self._log("   ⚠️  Grounding audit returned no verdict - flagging for review", 'warning')
            raise ValueError("grounding audit returned no parseable verdict")

        raw = m.group(1).strip()
        if raw.upper() == 'NONE':
            return []
        return [f.strip().lower() for f in raw.split(',') if f.strip()]

    def _build_reconcile_prompt(self, entry, content, candidates):
        """Build a prompt asking Claude to merge a claimed value with a
        verified one it's already been confirmed (in code, by
        enrich._is_completion() - see verify_recollection()) to merely
        complete rather than contradict - so this call's job is purely
        formatting the merge correctly per the project's own conventions
        (name format, title case, etc.), not judging whether to apply it."""
        lines = "\n".join(
            f"- {c['field']}: claimed = {c['claimed']!r} (from the initial extraction) "
            f"vs. verified = {c['verified']!r} (from {c['source']})"
            for c in candidates
        )
        metadata_block = ""
        if content.metadata:
            meta_lines = "\n".join(f"{k}: {v}" for k, v in content.metadata.items())
            metadata_block = f"\n<source_metadata>\n{meta_lines}\n</source_metadata>\n"

        prompt = f"""This BibLaTeX entry has fields whose claimed value is a less complete
version of what an external bibliographic source (CrossRef) reports for the
same work - e.g. an abbreviated first name, or an author list missing a
co-author:

<fields_to_reconcile>
{lines}
</fields_to_reconcile>

<entry>
{entry}
</entry>

For reference, here is the source text/metadata the entry was originally
drawn from - useful for formatting nuances (e.g. spelling, diacritics), but
do not add, remove, or alter any field other than those explicitly listed in
<fields_to_reconcile>, even if you notice other bibliographic details below:

<source_text>
{content.text}
</source_text>
{metadata_block}
"""
        prompt += """For each field above, merge the claimed and verified values into the
single most complete, correct form (e.g. spelling out an abbreviated first
name, or adding a missing co-author to the list), applying the formatting
guidelines/template above (e.g. the "LastName, FirstName~Initials" name
format, and its worked examples for how a merged author list should look) -
do not just concatenate the two raw values as given, and do not draw on your
own background/training knowledge of this work beyond what's given here.

Do not add, remove, or change any field not listed in <fields_to_reconcile>,
even one you can now see is missing or fillable from the source text/metadata
above - that is out of scope for this step. Output ONLY the corrected
BibLaTeX entry, with no additional commentary."""
        return prompt

    def reconcile_fields(self, entry_text, content, candidates):
        """
        Ask Claude to reconcile claimed-vs-verified field values (see
        _build_reconcile_prompt) and return the updated entry. Falls back to
        the unchanged entry on any failure, or if the merge added a field
        outside what was asked for (the prompt now shows the full source
        text/metadata for grounding checks, which risks Claude "helpfully"
        filling in an unrelated field it noticed there - e.g. a Url found in
        the PDF body - so this is checked structurally rather than trusted
        from the prompt instructions alone).
        """
        context = self.load_context_files()
        prompt = self._build_reconcile_prompt(entry_text, content, candidates)
        allowed_fields = set(enrich.parse_bibtex_fields(entry_text)) | {c['field'].lower() for c in candidates}
        try:
            message = self.client.messages.create(
                model=self.config['model'],
                max_tokens=self.config['max_tokens'],
                messages=[{"role": "user", "content": self._cached_message_content(context, prompt)}],
            )
            merged = self.clean_bibtex(response_text(message))
            valid, _ = self.validate_braces(merged)
            if not valid:
                return entry_text

            unexpected = set(enrich.parse_bibtex_fields(merged)) - allowed_fields
            if unexpected:
                self._log(
                    f"   ⚠️  Reconciliation added unexpected field(s) {', '.join(sorted(unexpected))} - discarding merge",
                    'warning'
                )
                return entry_text

            for c in candidates:
                self._log(f"   Reconciled '{c['field']}' against {c['source']} data", 'info')
            return merged
        except Exception as e:
            self._log(f"   ⚠️  Reconciliation failed: {e}", 'warning')
        return entry_text

    def verify_and_flag_recollection(self, entry_text, content):
        """
        Ask Claude to self-audit which field values are not grounded in the
        given source text/metadata (i.e. likely drawn from its own background
        knowledge), then attempt to confirm or refute those specific fields
        via CrossRef/Google Scholar. The same lookup also fills in a few
        container-level fields (Editor, Publisher, Date) when the entry is
        missing them entirely - e.g. an edited collection's Editor, which is
        otherwise never sourced anywhere else in this pipeline.

        Fields whose claimed value differs from a verified external record are
        only auto-reconciled (via a second, narrowly-scoped Claude call - see
        reconcile_fields) when enrich.verify_recollection() has already
        vetted them in code as a safe CrossRef-sourced completion, never a
        genuine contradiction. Anything that doesn't clear that bar - a real
        contradiction, or any Google Scholar-sourced conflict - is left as
        Claude produced it, but needs_color_flag is returned True so the
        caller can mark the saved publication for manual review.

        Returns (entry_text, needs_color_flag).
        """
        entry_type = enrich.get_entry_type(entry_text)
        fields = enrich.parse_bibtex_fields(entry_text)

        try:
            ungrounded = self._audit_entry_grounding(entry_text, content)
        except Exception as e:
            # Flag rather than pass. An audit that didn't run leaves the entry
            # unverified, which is precisely what the amber marker is for -
            # returning False here would present an unchecked entry as clean.
            self._log(f"   ⚠️  Grounding audit failed ({e}) - flagging for review", 'warning')
            return entry_text, True

        missing_container = enrich.container_fields_missing(fields, entry_type)
        if not ungrounded and not missing_container:
            return entry_text, False

        if ungrounded:
            self._log(f"   Checking recollection-based fields: {', '.join(ungrounded)}", 'info')
        if missing_container:
            self._log(f"   Also checking for missing container-level fields: {', '.join(missing_container)}", 'info')

        work_title = enrich.work_level_title(entry_type, fields)
        year = fields.get('date') or fields.get('year')
        reconcile_candidates, additions, unresolved = enrich.verify_recollection(
            work_title, ungrounded, fields, year=year,
            crossref_email=self.config.get('crossref_email'),
            scrapingdog_api_key=self.config.get('scrapingdog_api_key'),
            log=lambda msg: self._log(f"     {msg}", 'dim'),
        )

        if reconcile_candidates:
            entry_text = self.reconcile_fields(entry_text, content, reconcile_candidates)

        for field, value in additions.items():
            self._log(f"   Filled '{field}' from a verified work-level record", 'info')
            entry_text = enrich.add_field(entry_text, field, value)

        if unresolved:
            # Re-derive what's actually still outstanding rather than reusing
            # the pre-search lists, which would misreport anything that just
            # got reconciled/filled above as still missing.
            reconciled_fields = {c['field'] for c in reconcile_candidates}
            still_ungrounded = [f for f in ungrounded if f not in reconciled_fields]
            still_missing_container = [f for f in missing_container if f not in additions]
            reasons = []
            if still_ungrounded:
                reasons.append(f"recollection-based field(s) ({', '.join(still_ungrounded)})")
            if still_missing_container:
                reasons.append(f"container-level field(s) ({', '.join(still_missing_container)})")
            if reasons:
                self._log(
                    f"   ⚠️  Could not verify {' and '.join(reasons)} via CrossRef/Scholar - flagging for review",
                    'warning'
                )

        return entry_text, unresolved

    def clean_bibtex(self, bibtex_entry):
        """Remove code fencing and surrounding prose from BibLaTeX entry if present."""
        entry = bibtex_entry.strip()
        # Remove ```bibtex or ``` fencing
        if entry.startswith("```"):
            lines = entry.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            entry = "\n".join(lines).strip()

        # Strip any preamble text before the first @
        at_pos = entry.find('@')
        if at_pos > 0:
            entry = entry[at_pos:]

        # Strip any trailing text after the entry's closing brace
        depth = 0
        entry_end = len(entry)
        for i, char in enumerate(entry):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    entry_end = i + 1
                    break
        entry = entry[:entry_end]

        return entry.strip()

    def validate_braces(self, entry):
        """Check that all braces in the entry are balanced."""
        depth = 0
        for char in entry:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth < 0:
                    return False, "unmatched closing brace"
        if depth != 0:
            return False, f"unclosed braces (depth={depth})"
        return True, ""

    def add_bdsk_bookmark(self, entry, pdf_path):
        """Inject a bdsk-file-1 field with a macOS file bookmark into the entry."""
        try:
            from Foundation import NSURL
            import base64
            import os
            import plistlib

            pdf_path = Path(pdf_path).resolve()
            url = NSURL.fileURLWithPath_(str(pdf_path))
            NSURLBookmarkCreationSuitableForBookmarkFile = 1 << 10
            bookmark_data, error = url.bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error_(
                NSURLBookmarkCreationSuitableForBookmarkFile, None, None, None
            )
            if error or bookmark_data is None:
                self._log(f"   ⚠️  Could not create bookmark: {error}", 'warning')
                return entry

            bib_dir = Path(self.config['main_bib_file']).expanduser().parent
            rel_path = os.path.relpath(str(pdf_path), str(bib_dir))

            plist_bytes = plistlib.dumps(
                {'relativePath': rel_path, 'bookmark': bytes(bookmark_data)},
                fmt=plistlib.FMT_BINARY
            )
            b64 = base64.b64encode(plist_bytes).decode('ascii')

            # Insert bdsk-file-1 before the entry's closing brace.
            # The entry ends with the closing } on its own line.
            lines = entry.rstrip().split('\n')
            if lines[-1].strip() == '}':
                last_field = lines[-2].rstrip()
                if not last_field.endswith(','):
                    lines[-2] = last_field + ','
                lines.insert(-1, f'  bdsk-file-1 = {{{b64}}}')
            return '\n'.join(lines)

        except ImportError:
            self._log("   ⚠️  pyobjc not available, skipping bdsk-file-1", 'warning')
            return entry

    # Amber/orange - flags a publication for two distinct reasons, both
    # "produced, but worth a human glance": (1) the entry contains at least
    # one field Claude filled in from its own background knowledge of the
    # work rather than the given source text/metadata, and that CrossRef/
    # Scholar could neither confirm nor refute (see verify_and_flag_
    # recollection); (2) the source itself is genuine but thin or sparse -
    # web_source.py's content-plausibility check (see extract_bibtex's use
    # of content.amber). Applied whenever autofile_bibdesk is on, whatever
    # the source type; with it off, entries are appended as text and the
    # flag travels as save_entry's "% AMBER: ..." comment instead.
    UNVERIFIED_COLOR = "{65535, 40000, 0, 65535}"

    def _save_via_bibdesk(self, entry, bib_path, needs_color=False, auto_file=True):
        """Open the staging file in BibDesk (if needed), import the entry, and
        (for a source with a document behind it) auto-file that document.

        `auto_file=False` for a source that has no document to file - a
        .webloc bookmarks a webpage, so there is nothing for BibDesk to
        rename and move. Everything else here still applies to it: the import
        and, above all, the review color, which is the only form the amber
        flag can take once BibDesk owns the file (its importer re-serializes
        the entry and discards every % comment).

        Uses a temp file for the import to avoid AppleScript quoting issues.
        Raises RuntimeError on failure so the caller can log and fall through.
        """
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.bib', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write(entry)
            tmp_path = tmp.name

        color_line = f"set color of pub to {self.UNVERIFIED_COLOR}\n    " if needs_color else ""
        file_line = "auto file pub" if auto_file else ""
        script = f'''
tell application "BibDesk"
    set bibPath to "{bib_path}"
    set theDoc to missing value
    repeat with d in documents
        if path of d is bibPath then
            set theDoc to d
            exit repeat
        end if
    end repeat
    if theDoc is missing value then
        set theDoc to (open POSIX file bibPath)
    end if
    set thePubs to import theDoc from POSIX file "{tmp_path}"
    if thePubs is missing value or (count of thePubs) is 0 then return "import failed"
    set pub to item 1 of thePubs
    set cite key of pub to (generated cite key of pub)
    {color_line}{file_line}
    return "ok"
end tell'''

        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip()
            if output == "ok":
                self._log("   ✓ Imported into BibDesk" + (" and auto-filed" if auto_file else ""),
                          'success')
            else:
                raise RuntimeError(output or result.stderr.strip())
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(e)
        finally:
            os.unlink(tmp_path)

    def _ask_ocr_language(self, pdf_name=""):
        """Show a macOS dropdown to select the OCR language. Returns a tesseract language code."""
        display_names = [name for name, _ in OCR_LANGUAGES]
        list_str = '{' + ', '.join(f'"{n}"' for n in display_names) + '}'
        prompt = f"Select OCR language for: {pdf_name}" if pdf_name else "Select OCR language:"
        script = (
            f'set lang_list to {list_str}\n'
            f'set chosen to choose from list lang_list with prompt "{prompt}" default items {{"English"}}\n'
            f'if chosen is false then return "English"\n'
            f'return item 1 of chosen'
        )
        try:
            out = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=60
            )
            chosen = out.stdout.strip()
        except Exception:
            chosen = "English"
        for name, code in OCR_LANGUAGES:
            if name == chosen:
                return code
        return "eng"

    def notify_progress(self, message, subtitle=""):
        """Send a macOS notification for progress updates (when notifications are enabled)."""
        if not _interface_setting(self.config, 'notifications', True):
            return
        script = f'display notification "{message}" with title "Ostracon AI"'
        if subtitle:
            script += f' subtitle "{subtitle}"'
        subprocess.run(['osascript', '-e', script], capture_output=True)

    def notify_incomplete(self, pdf_name, missing_fields):
        """Send a macOS notification that an entry was saved but is missing fields."""
        if not _interface_setting(self.config, 'notifications', True):
            return
        msg = f"{pdf_name}: saved but missing {', '.join(missing_fields)}."
        subprocess.run(
            ['osascript', '-e',
             f'display notification "{msg}" with title "Ostracon AI" subtitle "Incomplete Entry" sound name "Basso"'],
            capture_output=True
        )

    def notify_failure(self, pdf_name, error_msg):
        """Send a macOS notification about a validation failure."""
        if not _interface_setting(self.config, 'notifications', True):
            return
        failed_path = Path(self.config.get('failed_bib_file', '~/Desktop/biblio-failed.bib')).expanduser()
        msg = f"Brace validation failed for {pdf_name}: {error_msg}. Entry saved to {failed_path}."
        subprocess.run(
            ['osascript', '-e',
             f'display notification "{msg}" with title "Ostracon AI" subtitle "Validation Failed" sound name "Basso"'],
            capture_output=True
        )

    def _save_bibdesk_document(self):
        """Save the BibDesk document for main_bib_file if it is currently open."""
        bib_path = str(Path(self.config['main_bib_file']).expanduser().resolve())
        script = f'''
tell application "BibDesk"
    repeat with d in documents
        if path of d is "{bib_path}" then
            save d
            return "ok"
        end if
    end repeat
    return "not open"
end tell'''
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=15
            )
            if result.stdout.strip() == "ok":
                self._log("   ✓ Staging file saved", 'success')
        except Exception as e:
            self._log(f"   ⚠️  Could not save BibDesk document: {e}", 'warning')

    def save_failure(self, entry, pdf_name, error_msg):
        """Append a failed entry with an error note to the failed bib file."""
        failed_path = Path(self.config.get('failed_bib_file', '~/Desktop/biblio-failed.bib')).expanduser()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(failed_path, 'a') as f:
            f.write(f"% FAILED: {timestamp}\n")
            f.write(f"% Source: {pdf_name}\n")
            f.write(f"% Error: {error_msg}\n")
            f.write(entry)
            f.write("\n\n")
        self._log(f"   ✗ Failed entry saved to {failed_path}", 'error')

    def save_entry(self, bibtex_entry, pdf_path):
        """Validate, enrich with a BibDesk bookmark, and append to the main bib file."""
        pdf_path = Path(pdf_path)

        # extract_bibtex() prepends a "% Source: ..." comment recording which
        # kind of source (PDF/webpage) and locator produced this entry;
        # clean_bibtex() strips anything before the first '@', so pull it out
        # first and re-attach it once cleaning is done (outermost marker -
        # see the prepend order in extract_bibtex).
        #
        # Until fixed here, this pattern's \b sat right after the literal
        # ':' (`Source:\b`) - impossible, since neither side of that
        # position is a word character - so it never matched anything.
        # Because % Source: is always the outermost/first line and re.match
        # anchors at position 0, that didn't just drop this comment: with
        # bibtex_entry left unstripped, EVERY marker regex below it also
        # failed to match against the unrelated text still sitting at
        # position 0, for every entry this method ever saved - PDF or
        # .webloc alike. needs_color could never become True by this path
        # before this fix, so BibDesk's amber coloring (UNVERIFIED_COLOR)
        # was inert from the day it was introduced, not merely blind to
        # .webloc sources as the comments below (written after this fix)
        # describe for the design going forward.
        source_comment = ''
        marker_match = re.match(r'(%\s*Source\b:[^\n]*\n)', bibtex_entry)
        if marker_match:
            source_comment = marker_match.group(1)
            bibtex_entry = bibtex_entry[marker_match.end():]

        # verify_and_flag_recollection() prepends a bare "% NEEDS_COLOR_FLAG"
        # marker when a recollection-based field couldn't be confirmed or
        # refuted via CrossRef/Scholar. Unlike the Sources comment below, this
        # one is discarded (not re-attached) - it only decides whether to
        # color the BibDesk publication, so it shouldn't persist in the .bib text.
        needs_color = False
        marker_match = re.match(r'(%\s*NEEDS_COLOR_FLAG\s*\n)', bibtex_entry)
        if marker_match:
            needs_color = True
            bibtex_entry = bibtex_entry[marker_match.end():]

        # extract_bibtex() prepends a "% AMBER: ..." comment when
        # content.amber is set (web_source.py's plausibility check: a
        # genuine but thin/sparse source). Unlike NEEDS_COLOR_FLAG, this one
        # IS re-attached below: a .webloc source is never fileable (see the
        # is_fileable_source check further down), so it never reaches
        # BibDesk's own color, and this comment is the only place the flag
        # survives into the saved .bib text.
        amber_comment = ''
        marker_match = re.match(r'(%\s*AMBER\b:[^\n]*\n)', bibtex_entry)
        if marker_match:
            amber_comment = marker_match.group(1)
            bibtex_entry = bibtex_entry[marker_match.end():]

        # enrich_entry() prepends a "% Sources -- ..." comment recording field
        # provenance; clean_bibtex() strips anything before the first '@', so
        # pull the comment out first and re-attach it once cleaning is done.
        sources_comment = ''
        marker_match = re.match(r'(%\s*Sources\b[^\n]*\n)', bibtex_entry)
        if marker_match:
            sources_comment = marker_match.group(1)
            bibtex_entry = bibtex_entry[marker_match.end():]

        entry = self.clean_bibtex(bibtex_entry)

        # Reject responses that are not BibTeX entries
        if not entry.lstrip().startswith('@'):
            error_msg = "response is not a BibTeX entry"
            self.save_failure(entry, pdf_path.name, error_msg)
            self.notify_failure(pdf_path.name, error_msg)
            return False

        # Validate brace balance before touching the main file
        valid, error_msg = self.validate_braces(entry)
        if not valid:
            self.save_failure(entry, pdf_path.name, error_msg)
            self.notify_failure(pdf_path.name, error_msg)
            return False

        if sources_comment:
            entry = sources_comment + entry
        if amber_comment:
            entry = amber_comment + entry
        if source_comment:
            entry = source_comment + entry

        # Flag entries still missing critical fields after enrichment (does not block saving)
        entry_type = enrich.get_entry_type(entry)
        fields = enrich.parse_bibtex_fields(entry)
        still_missing, _ = enrich.missing_fields(entry_type, fields)
        if still_missing:
            self._log(f"   ⚠️  Incomplete: missing {', '.join(still_missing)}", 'warning')
            self.notify_incomplete(pdf_path.name, still_missing)
            entry = f"% INCOMPLETE: missing {', '.join(still_missing)}\n" + entry

        # Attach a BibDesk file bookmark and auto-file the linked document -
        # PDF sources only. A .webloc is just a bookmark to a webpage used to
        # extract bibliographic data; it has no document worth filing into
        # BibDesk's library, so it is neither bookmarked nor moved.
        #
        # Having a document to file is NOT the same question as belonging in
        # BibDesk, though, and conflating the two is what left web-sourced
        # entries with no review flag at all: under autofile_bibdesk the
        # importer re-serializes each entry and discards every % comment, so
        # the color is the only surviving form of the flag - and a .webloc
        # entry, excluded from the import path entirely, could not be colored
        # and could not keep its comment either. It goes through the import
        # like any other entry now; only `auto file` (which needs a document)
        # is withheld.
        has_document = pdf_path.suffix.lower() == '.pdf'
        if has_document:
            entry = self.add_bdsk_bookmark(entry, pdf_path)

        if self.config.get('autofile_bibdesk', False):
            output_path = Path(self.config['main_bib_file']).expanduser()
            if not output_path.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.touch()
            bib_path = str(output_path.resolve())
            try:
                self._save_via_bibdesk(entry, bib_path, needs_color=needs_color,
                                        auto_file=has_document)
                return True
            except RuntimeError as e:
                self._log(f"   ⚠️  BibDesk import failed: {e}", 'warning')
                return False

        # Plain-text append: no BibDesk, so no color is possible - but here
        # the % comments do survive into the file, which is what the AMBER
        # comment is for. The two carriers are complementary, not redundant.
        if needs_color:
            self._log("   ⚠️  Unverified field(s) or a thin source - color flag needs "
                      "autofile_bibdesk to apply"
                      + (" (see the % AMBER comment in the saved entry)" if amber_comment else ""),
                      'warning')

        output_path = Path(self.config['main_bib_file']).expanduser()
        if not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.touch()

        content = output_path.read_text(encoding='utf-8')
        marker = '@comment{BibDesk Static Groups{'
        marker_pos = content.find(marker)
        if marker_pos != -1:
            content = content[:marker_pos] + entry + "\n\n" + content[marker_pos:]
        else:
            content = content + entry + "\n\n"
        output_path.write_text(content, encoding='utf-8')

        self._log(f"   ✓ Saved to {output_path}", 'success')
        return True

    def move_to_processed(self, pdf_path):
        """Move a processed source file to the output folder."""
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            # autofile_bibdesk already relocated the linked file into
            # BibDesk's own Papers folder as part of auto-filing (BibDesk
            # renames/moves the file it's given, it doesn't just read it) -
            # there's nothing left here to move, and that's fine.
            self._log(f"   (already relocated by BibDesk's autofile, not moving to pdf-out)", 'info')
            return None

        out_folder = Path(self.config.get('pdf_out_folder', './pdf-out'))
        out_folder.mkdir(parents=True, exist_ok=True)

        dest = out_folder / pdf_path.name
        # Handle duplicates by adding a suffix
        if dest.exists():
            stem = pdf_path.stem
            suffix = pdf_path.suffix
            counter = 1
            while dest.exists():
                dest = out_folder / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.move(str(pdf_path), str(dest))

        self._log(f"   ✓ Moved to {dest}", 'success')
        return dest

    def process_batch(self, move_files=True, pdf_files=None, progress_window=None):
        """
        Process a list of source files (or all supported files in the input
        folder when pdf_files is None).

        Sources placed in pdf_in_folder/careful/ are processed with
        careful_model instead of model - the folder is a deliberate gesture
        (dropping a reference-work source there) rather than a choice made
        under time pressure in a dropdown.

        Args:
            move_files: If True, move processed files to output folder
            pdf_files: Explicit list of Path objects; falls back to pdf_in_folder if None
            progress_window: Optional ProgressWindow instance for set_progress() calls

        Returns:
            dict: Summary with 'success', 'failed', 'skipped' lists
        """
        in_folder = Path(self.config.get('pdf_in_folder', './pdf-in'))
        if pdf_files is not None:
            files = [Path(p) for p in pdf_files]
        else:
            if not in_folder.exists():
                self._log(f"Error: Input folder not found: {in_folder}", 'error')
                self._log(f"Create it with: mkdir {in_folder}", 'error')
                return {'success': [], 'failed': [], 'skipped': []}
            files = glob_batch_files(in_folder)

        if not files:
            self._log("No source files found.", 'warning')
            return {'success': [], 'failed': [], 'skipped': []}

        total = len(files)
        self._log(f"\n📚 Processing {total} file(s)\n", 'info')
        self.notify_progress(f"Processing {total} file{'s' if total != 1 else ''}…")

        if self.config.get('autofile_bibdesk', False):
            self._save_bibdesk_document()

        results = {'success': [], 'failed': [], 'skipped': []}
        careful_dir = (in_folder / 'careful').resolve()

        for i, pdf_path in enumerate(files, 1):
            if progress_window and progress_window.cancelled:
                self._log("Cancelled by user.", 'warning')
                break

            if progress_window:
                progress_window.set_progress(i, pdf_path.name)
                # Re-read the window's model popup before each file, so a
                # change made mid-batch takes effect from the next item. Cheap:
                # caches are per-model and coexist, so alternating costs one
                # write per model for the whole run, not one per switch.
                chosen = getattr(progress_window, 'selected_model', None)
                if chosen and chosen != self.config['model']:
                    self.config['model'] = chosen

            # A file from pdf_in_folder/careful/ always takes careful_model,
            # regardless of the dropdown above - the point of the folder is
            # to not depend on the operator noticing and reacting in time.
            is_careful = pdf_path.resolve().parent == careful_dir
            model_for_file = self.config.get('careful_model', DEFAULT_CAREFUL_MODEL) if is_careful else None

            self.notify_progress(f"[{i}/{total}] {pdf_path.name}", subtitle="Extracting bibliography")

            # Extract bibliography
            bibtex_entry = self.extract_bibtex(pdf_path, batch_info=(i, total), model_override=model_for_file)

            if bibtex_entry.startswith("Error:"):
                self._log(f"   ✗ {bibtex_entry}", 'error')
                results['failed'].append((pdf_path.name, bibtex_entry))
                continue

            # Save entry
            saved = self.save_entry(bibtex_entry, pdf_path)
            if not saved:
                results['failed'].append((pdf_path.name, "save failed"))
                continue

            # Move file if requested
            if move_files:
                self.move_to_processed(pdf_path)

            results['success'].append(pdf_path.name)
            pct_done = int(i / total * 100)
            self._log(f"   ✓ Done ({pct_done}% complete)", 'success')

        # Summary
        n_ok = len(results['success'])
        n_fail = len(results['failed'])
        self._log("\n" + "=" * 40, 'dim')
        self._log("SUMMARY", 'info')
        self._log("=" * 40, 'dim')
        self._log(f"✓ Success: {n_ok}", 'success')
        self._log(f"✗ Failed:  {n_fail}", 'error' if n_fail else 'info')

        if results['failed']:
            self._log("\nFailed files:", 'error')
            for name, error in results['failed']:
                self._log(f"  - {name}: {error}", 'error')

        if self.config.get('autofile_bibdesk', False):
            self._save_bibdesk_document()

        if n_fail:
            summary_msg = f"{n_ok} succeeded, {n_fail} failed"
        else:
            summary_msg = f"{n_ok} file{'s' if n_ok != 1 else ''} processed successfully"
        self.notify_progress(summary_msg, subtitle="Done")

        return results

def _resolve_show_window(args, config):
    """Determine whether to show the progress window."""
    if args.quiet:
        return False
    if args.window is not None:
        return args.window  # --window or --no-window explicitly set
    return _interface_setting(config, 'show_window', False)


def _run_windowed(agent, pdf_files, move_files):
    """Run processing with a native floating window on the main thread."""
    import threading
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from progress_window import ProgressWindow

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    win = ProgressWindow(
        total_files=len(pdf_files),
        models=_interface_setting(agent.config, 'window_models', None) or [agent.config['model']],
        current_model=agent.config['model'],
    )
    agent._progress_callback = win.make_callback()
    win.show()

    def _process():
        if win.cancelled:  # window closed before the worker thread got going
            win.finish(had_error=False)
            return

        results = agent.process_batch(
            move_files=move_files,
            pdf_files=pdf_files,
            progress_window=win,
        )
        had_error = bool(results['failed'])
        win.finish(had_error=had_error)

    t = threading.Thread(target=_process, daemon=True)
    t.start()

    app.run()

    # After the run loop exits, collect stdout output (entries were saved to file).
    # Return whether any failures occurred so the caller can set exit code.
    return False  # errors surfaced via window; exit 0 for Automator clipboard path


def main():
    parser = argparse.ArgumentParser(
        description="Extract bibliographic data from PDFs and .webloc files using Claude"
    )
    parser.add_argument(
        'input_files',
        nargs='*',
        help='Path(s) to PDF and/or .webloc file(s) to process'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Process all supported files in the input folder (pdf-in/)'
    )
    parser.add_argument(
        '--no-move',
        action='store_true',
        help='Do not move processed files to output folder'
    )
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to config file (default: config.yaml)'
    )
    parser.add_argument(
        '--output',
        help='Output bib file (overrides config)'
    )
    parser.add_argument(
        '--model',
        help='Claude model to use (overrides config), e.g. claude-opus-5'
    )
    parser.add_argument(
        '--no-save',
        action='store_true',
        help='Print to stdout instead of saving'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress status messages (for automation)'
    )

    win_group = parser.add_mutually_exclusive_group()
    win_group.add_argument(
        '--window',
        dest='window',
        action='store_const',
        const=True,
        default=None,
        help='Show floating progress window (overrides config show_window)'
    )
    win_group.add_argument(
        '--no-window',
        dest='window',
        action='store_const',
        const=False,
        help='Disable floating progress window'
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.all and not args.input_files:
        parser.error("Either provide file(s) or use --all to process the input folder")

    try:
        # Initialize agent
        agent = BiblioAgent(args.config)

        # Override config options
        if args.output:
            agent.config['main_bib_file'] = args.output
        if args.model:
            agent.config['model'] = args.model
        if args.quiet:
            agent.config['verbose'] = False

        show_window = _resolve_show_window(args, agent.config)

        # ── batch mode (--all) ────────────────────────────────────────────────
        if args.all:
            if show_window:
                in_folder = Path(agent.config.get('pdf_in_folder', './pdf-in'))
                pdf_files = glob_batch_files(in_folder)
                _run_windowed(agent, pdf_files, move_files=not args.no_move)
            else:
                results = agent.process_batch(move_files=not args.no_move)
                if results['failed']:
                    sys.exit(1)
            return

        # ── explicit file list ────────────────────────────────────────────────
        pdf_files = [Path(f) for f in args.input_files]

        if show_window and len(pdf_files) >= 1:
            _run_windowed(agent, pdf_files, move_files=not args.no_move)
            return

        # ── non-windowed single/multi file mode ───────────────────────────────
        for pdf_path in pdf_files:
            agent._log(f"\n📄 Processing: {pdf_path.name}", 'info')

            bibtex_entry = agent.extract_bibtex(pdf_path)

            if bibtex_entry.startswith("Error:"):
                print(bibtex_entry, file=sys.stderr)
                continue

            clean_entry = agent.clean_bibtex(bibtex_entry)
            if not args.no_save:
                saved = agent.save_entry(bibtex_entry, pdf_path)
                if not saved:
                    print(f"Error: failed to save entry for {pdf_path.name}", file=sys.stderr)
                    sys.exit(1)
            print(clean_entry)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
