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
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
import yaml
from anthropic import Anthropic

import extract_pages
from extract_pages import extract_pdf
from extraction_result import ExtractionResult
import web_source
import enrich
import gemini_search
import type_fields
import house_style
import library_match
import repairs
from api_keys import resolve_api_keys

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

import os

# The generic extraction rules, used when config.yaml names no rules_file.
DEFAULT_RULES_FILE = "./prompt-context/extraction-rules.md"

# A user's own conventions, sent after the generic rules and taking precedence
# over them. Optional and private: read when config.yaml names one, or else
# from this location when a file exists there. Never shipped with the repository.
DEFAULT_HOUSE_STYLE_FILE = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                            / "ostracon-ai" / "house-style.md")
DEFAULT_CAREFUL_MODEL = "claude-opus-5"


def glob_batch_files(folder):
    """Source files for an --all run: folder itself plus its careful/
    subfolder, if present (see process_batch's folder-convention note)."""
    files = glob_input_files(folder)
    careful_dir = Path(folder) / 'careful'
    if careful_dir.is_dir():
        files += glob_input_files(careful_dir)
    return sorted(files)


# Config paths are written relative to the repository ("./prompt-context/..."), so they
# must resolve against it rather than against the working directory - otherwise
# the tool only runs from the repo root, and the Automator wrapper's `cd
# "$WORKDIR"` is load-bearing rather than a convenience.
# This module lives in src/, so the repository root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Config keys holding a path that should be made absolute at load time, so every
# downstream consumer receives an absolute path without repeating this logic.
_PATH_KEYS = (
    'rules_file', 'house_style_file', 'template_file', 'ref_file',
    'pdf_in_folder', 'pdf_out_folder',
    'main_bib_file', 'failed_bib_file',
)

# Of those, the ones naming a file that must exist when the key is configured.
# A missing one is fatal rather than a warning: load_context_files() would
# otherwise substitute an empty string, and the run would silently produce
# entries with no template, field reference, or worked examples - degraded
# output that looks like success.
_REQUIRED_IF_SET = ('rules_file', 'house_style_file', 'template_file', 'ref_file')


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


# The closed list of entry types, defined as this library uses them. Step 1 of
# the extraction prompt shows it; Gemini's type check (src/gemini_search.py) too.
TYPE_DEFINITIONS = """      article         A contribution to an issue of a periodical (journal,
                      magazine, newspaper), in print or online, including a
                      review that has a title of its own. A volume with its
                      own title, an ISBN and a book publisher is a book even
                      when numbered in a series or yearbook; its chapters are
                      incollection or inbook.
      review          A review of another work (book, edition, recording,
                      performance) in a periodical, with no title of its own
                      beyond naming the work reviewed. A review with a title
                      of its own is article. A piece that merely discusses,
                      cites or answers other publications is not a review.
      periodical      A whole issue of a periodical, cited as a whole,
                      usually a special issue under its editors.
      book            A whole book written entirely by its author or authors.
      collection      A whole book whose chapters are by different authors,
                      under an editor, including one that grew out of a
                      conference but whose title does not call it proceedings.
      inbook          A chapter or essay in a book written entirely by the
                      same author or authors, even if the book has an editor
                      (a selection, a collected-essays volume, a critical
                      edition).
      incollection    A chapter in a book whose chapters are by different
                      authors, when that book is not proceedings (below).
      proceedings     A whole volume whose title calls it the proceedings
                      of a conference, congress or symposium (Proceedings,
                      Actes, Kongressbericht, or a cognate). A book of
                      conference papers whose title does not is collection,
                      even if its series or front matter names the event.
      inproceedings   A paper in a proceedings volume as defined above.
      unpublished     A work neither published nor available online: a
                      manuscript, typescript, lecture, working paper, or a
                      conference paper never printed in proceedings, obtained
                      from its author or as a handout. If it is available
                      online, it is online. The type is the work's: a draft
                      of a work published since takes the published work's
                      type.
      thesis          A thesis or dissertation submitted for a degree, unless
                      later published as a book.
      report          A report issued by an institution, usually numbered in
                      a series.
      online          A work available online that has no printed
                      counterpart and is not better classified as another
                      type. Rule out the others first: an article in an
                      online journal is article; an online encyclopedia
                      entry is inreference; an online copy of a printed work
                      takes the printed work's type. A paper, talk or
                      manuscript available online, including a conference
                      paper not in proceedings, is online.
      reference       A whole reference work (dictionary, encyclopedia,
                      lexicon), cited as a whole.
      inreference     An entry in a reference work, in print or online
                      (Grove Music Online, MGG).
      suppbook        Untitled supplementary matter in a book (introduction,
                      preface, afterword, notes), cited on its own: the item
                      is the supplement, not the book, whether its author is
                      the book's author or someone else.
      suppcollection  The same, in a collection.
      music           A musical recording without video (a CD, LP or
                      streamed album or track). A recording with video is
                      video.
      audio           A published musical score, a podcast, or other audio
                      material presented like a book (biblatex-chicago). An
                      unpublished score is misc.
      video           A film or video recording, including a recorded
                      opera or other performance.
      misc            A work that fits no other type; the last resort.
"""


_MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
           'september', 'october', 'november', 'december']


def _tokens(text):
    """The words of a value or a source, case, accents and markup aside."""
    import unicodedata
    t = unicodedata.normalize('NFKD', enrich.strip_latex(text or '').lower())
    return set(re.findall(r'\w+', ''.join(c for c in t if not unicodedata.combining(c))))


def _sourced(value, known):
    """Whether every word of `value` occurs in `known` (the source's words and
    the entry's), in any order; an initial counts when some word begins with
    it, and an ISO date when its year, month (name or abbreviation) and day
    occur. A full name for an initial, or a word the source lacks, is not
    sourced."""
    m = re.fullmatch(r'\s*(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?\s*', value or '')
    if m:
        year, month, day = m.groups()
        ok = year in known
        if month:
            name = _MONTHS[int(month) - 1] if 1 <= int(month) <= 12 else ''
            ok = ok and (name in known or name[:3] in known or month in known or str(int(month)) in known)
        if day:
            ok = ok and (day in known or str(int(day)) in known)
        return ok
    initials = {w[0] for w in known}
    return all(w in known or (len(w) == 1 and w in initials) for w in _tokens(value))


def render_problems(rendered, fields):
    """Printing faults the render check looks for besides dropped fields
    (cleanup spec §2): a closing double quote before a closing single one
    (a \\mkbibquote nested wrongly, printed '.”’'), and a name printed as
    both author and publisher where the author is not corporate."""
    out = []
    if re.search("\u201d\u2019", rendered):
        out.append("nested quotation marks close in the wrong order")
    au, pub = (fields.get('author') or '').strip(), (fields.get('publisher') or '').strip()
    if au and pub and not au.startswith('{') and au.lower() == pub.lower():
        out.append(f"{au!r} printed as both author and publisher")
    return out


class BiblioAgent:
    def __init__(self, config_path="config.yaml"):
        """Initialize the agent with configuration."""
        self.config = self.load_config(config_path)
        self.client = Anthropic(api_key=self.config['anthropic_api_key'])
        self._progress_callback = None  # set to win.make_callback() in windowed path
        # One record per API call: which call, model, and token counts.
        # dev/eval/run.py reads and resets it per source to measure cost.
        self.usage = []
        # Per-thread state: process_batch() runs several sources at once.
        self._local = threading.local()
        self._lock = threading.Lock()      # saving, moving, logging, progress
        self._ask_lock = threading.Lock()  # one OCR-language dialog at a time

    @property
    def inferred_fields(self):
        return getattr(self._local, 'inferred_fields', [])

    @inferred_fields.setter
    def inferred_fields(self, value):
        self._local.inferred_fields = value

    def _log(self, message: str, level: str = 'info') -> None:
        """Emit a progress message to stderr (if verbose) and/or the progress
        window. In a parallel batch a source's messages are held and emitted
        together when it finishes, so sources do not interleave."""
        held = getattr(self._local, 'held', None)
        if held is not None:
            held.append((message, level))
            return
        self._emit(message, level)

    def _emit(self, message, level):
        if self.config.get('verbose'):
            print(message, file=sys.stderr)
        if self._progress_callback:
            self._progress_callback(message, level)

    def _create(self, call, max_tokens, **kwargs):
        """messages.create() for every pipeline call.

        - `effort` (config, optional) is sent as output_config.effort, and
          raises the token limit to `thinking_max_tokens` (default 16000):
          models that always think count thinking tokens against max_tokens,
          so the old limits (4000; 600 for the audit) can leave no room for
          the reply itself.
        - A reply cut off at the limit, or refused, raises instead of being
          passed on: its text is partial or empty, and downstream code would
          otherwise treat it as an answer.
        - Usage is appended to self.usage whatever the outcome.
        """
        effort = self.config.get('effort')
        if effort:
            kwargs['output_config'] = {'effort': effort}
            max_tokens = max(max_tokens, int(self.config.get('thinking_max_tokens', 16000)))
        message = self.client.messages.create(max_tokens=max_tokens, **kwargs)

        usage = getattr(message, 'usage', None)
        self.usage.append({
            'call': call,
            'model': kwargs.get('model'),
            'input': getattr(usage, 'input_tokens', 0) or 0,
            'output': getattr(usage, 'output_tokens', 0) or 0,
            'cache_write': getattr(usage, 'cache_creation_input_tokens', 0) or 0,
            'cache_read': getattr(usage, 'cache_read_input_tokens', 0) or 0,
        })

        stop = getattr(message, 'stop_reason', None)
        if stop == 'max_tokens':
            raise RuntimeError(
                f"{call} reply truncated at max_tokens={max_tokens}; "
                "raise max_tokens (or thinking_max_tokens) in config.yaml")
        if stop == 'refusal':
            raise RuntimeError(f"{call} request refused by the model")
        return message

    def _validate(self, entry_text):
        """bib_validate's undocumented biber warnings for one entry, as
        messages; [] when clean or when no verdict could be reached."""
        try:
            import bib_validate
            found = bib_validate.validate(entry_text)
        except Exception as e:      # ValidatorUnavailable, or TeX missing
            self._log(f"   Validation skipped: {str(e).splitlines()[0]}", 'dim')
            return []
        messages = [m for _, m in found]
        for m in messages:
            self._log(f"   ⚠️  biber: {m} - flagging for review", 'warning')
        return messages

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
        resolve_api_keys(config)
        if not config.get('anthropic_api_key'):
            raise ValueError(
                "Please set anthropic_api_key_command (or anthropic_api_key) in "
                "config.yaml, or export ANTHROPIC_API_KEY\n"
                "Get a key at: https://console.anthropic.com/settings/keys"
            )

        # CLAUDE.md is instructions for agents developing this repository; the
        # extraction rules moved to their own file. A config still naming it
        # would send development instructions to the extracting model.
        if config.pop('claude_md_file', None):
            print("config: claude_md_file is no longer read; the extraction rules are "
                  f"rules_file (default {DEFAULT_RULES_FILE})", file=sys.stderr)
        config.setdefault('rules_file', DEFAULT_RULES_FILE)
        if not config.get('house_style_file') and DEFAULT_HOUSE_STYLE_FILE.is_file():
            config['house_style_file'] = str(DEFAULT_HOUSE_STYLE_FILE)

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
            'rules': read('rules_file'),
            'house_style': read('house_style_file'),
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
        if context['rules']:
            parts.append(f"<guidelines>\n{context['rules']}\n</guidelines>")
        if context.get('house_style'):
            parts.append(f"<house_style>\n{context['house_style']}\n</house_style>")
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
        if context.get('house_style'):
            preamble += (
                "\n\n<house_style> is the user's own conventions. Where it differs "
                "from <guidelines> or <reference_template>, follow <house_style>."
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
                "<reference_template> covers 13 of the ~40 entry types and is NOT an "
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
1. Choose the entry type. Work through steps a-f and write them out inside
   <type_analysis>...</type_analysis>, before the entry. Begin no line inside
   it with "@".

   a. Choose from this list. Each line defines the type as this library uses
      it. Use a type not on the list only if none fits, and say why.

"""
        prompt += TYPE_DEFINITIONS
        prompt += """
   b. Container rule. If the item sits inside something - a periodical, a
      book, a proceedings volume, a reference work - or belongs to an event, a
      degree or a website, the type must have a field for that kind of
      container: Journal or Issuetitle for a periodical; Booktitle or
      Maintitle for a book, proceedings or reference work; Eventtitle for an
      event; Institution for a degree or a report; Url for a work published
      only online. Rule out any type that cannot record where the item sits.

   c. Name the three types from the list that best fit the item. For each,
      give the reasons for it and the reasons against it, from the source. A
      field a type needs that the source does not show is a reason against
      that type; never supply a field to make a type fit.

   d. Name the attributes of the item that would decide between the three.
      Always include these two:
      - whether the item is a whole volume or one part of it, judged by the
        text the copy contains, not by the title page or table of contents
        that may accompany it;
      - if it is part of a book, who wrote the other chapters of that book.
        An editor's name on the title page does not answer this.
      Then add any others that bear on the choice: for example, whether this
      copy carries marks of publication (a running head naming a journal or
      book, an imprint, a DOI, page numbers continuing a larger volume);
      whether the piece reviews another work, and if so whether it has a
      title of its own.

   e. For each attribute, quote the source where it shows it, or write "not
      in the source". Do not infer an attribute the source does not show.

   f. Choose the type. If an attribute that decides the choice is not in the
      source, name it, and choose the type the evidence in hand favors.

   Then encode the entry as follows.
   - For a part of a larger work, set Title to the SPECIFIC piece's own title (e.g.
     "Manifesto"), and Booktitle to the overall container's title (often
     visible in a running header, distinct from the piece's own heading) -
     do NOT use the container's title as the entry's Title when the excerpt
     is clearly one part of a larger work.
   - For untitled supplemental material (a generic
     preface, foreword, introduction, or index with no title of its own,
     whether by the book's own author or someone else) rather than a titled
     chapter, use @Suppbook instead (or @Suppcollection for an edited
     volume) - in that case Title correctly holds the BOOK's own title, with
     Bookauthor (or Editor/Editora) for the book's author where it differs
     from the supplement's Author. Only use @Inbook/@Incollection when the
     piece has its own distinct title.
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
     for Location at step 5. A field left empty is a gap someone can see and
     fill; a field filled with the wrong work reads as evidence and will not
     be questioned.
   - A whole volume with editor(s) and no author of its own takes the editors
     in Editor, never in Author.
   - @Thesis takes Type = {\\bibstring{phdthesis}} or
     {\\bibstring{mastersthesis}} and Institution = the institution; @Report
     takes Type = {\\bibstring{techreport}} or a similarly descriptive
     bibstring; @Unpublished conference papers take the Note field the
     guidelines require.
   A review with a title of its own is @Article: Title (and Subtitle) hold
   its own title, and Titleaddon holds the reviewed work, written as Title
   is written below for a review with no title of its own.
   For a review with no title of its own, use @Review. The reviewed work is
   encoded directly into Title, and a compressed form of it into Shorttitle,
   using exactly this construction:

     Title = {\\bibstring{reviewof} \\mkbibemph{Authenticities: Philosophical
       Reflections on Musical Performance}, \\bibstring{by} Peter Kivy},
     Shorttitle = {\\bibstring{reviewof} \\mkbibemph{Authenticities},
       \\bibstring{by} Kivy},

   The name after \\bibstring{by} is the author of the work BEING REVIEWED. It
   is never the reviewer, who is this entry's own Author and appears nowhere
   in Title or Shorttitle - identify the two people separately.

   Shorttitle compresses in two ways at once, and BOTH are required:
   - the reviewed title is cut back to its first few words (at the colon,
     where there is one), and
   - the reviewed author is reduced to a bare surname, no forenames or
     initials.
   A Shorttitle that stops after the reviewed title, with no \\bibstring{by}
   clause, is incomplete. Where one review covers several works, each keeps
   its own \\bibstring{by} clause in both fields.

   Title holds the reviewed work's own title and author and nothing else. The
   publishing apparatus printed alongside them on the review's first page -
   volume or series position ("Vol. III of ..."), translator and editor
   credits ("translated and edited by ..."), publisher, place, price - belongs
   to the volume under review, not to this entry's Title. Leave it out.

   That is a rule about which FACTS Title carries. It is not licence to
   simplify how the title and author are written: inside Title and Shorttitle
   alike, encode them exactly as the guidelines require of any other entry.
   In particular, and these are the two that go wrong -
   - a reviewed title, or the part of one, in a language other than the
     review's own still takes its \\foreignlanguage{<language>}{...} wrapper,
     inside \\mkbibemph; and
   - a forename followed by an initial still takes the non-breaking tie:
     \\bibstring{by} Lee~A. Rothfarb, never "Lee A. Rothfarb".
   The Kivy example above is short only because that title is English and that
   author has no initial; it is not a model for stripping either.
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
8. Range separators depend on the field: Pages takes a single hyphen (67-97);
   Date, Origdate, Eventdate and Urldate take a solidus with both endpoints in
   full (2012/2013); every other field takes -- (Number = {1--2}).
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
   with its own type, not necessarily @Online.
   Include Url and Urldate only where the URL rule above allows them - an
   @Online entry, an online reference work, or an entry with no Date. A
   dated book or article is cited as the book or article, not by the page
   it was read on, and takes neither field. Where they do belong, Url is
   exactly this address: {content.url}
   and Urldate is {now.strftime('%Y-%m-%d')} (today's date).
"""

        prompt += ("\nOutput the <type_analysis> block from step 1, then the BibLaTeX entry, "
                   "with no other commentary or explanation.")

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
                with self._ask_lock:
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
            thin_yield_words=self.config.get(
                'thin_yield_threshold', extract_pages.DEFAULT_THIN_YIELD_WORDS),
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
            ExtractionResult: the entry text plus the review state that
            belongs to it (source label, needs_color, amber reason, field
            provenance), or one carrying `error`. The state used to travel as
            `%` markers prepended to the text and parsed back out by
            save_entry(); see extraction_result.py for why it no longer does.
        """
        path = Path(path)

        if not path.exists():
            return ExtractionResult(error=f"Error: File not found: {path}")

        extractor = EXTRACTORS.get(path.suffix.lower())
        if extractor is None:
            return ExtractionResult(error=f"Error: Unsupported file type: {path.name}")

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
                'open_in_browser': bool(self.config.get('open_blocked_in_browser')),
            }
        else:
            kwargs = {}
        content = extractor(path, **kwargs)

        if isinstance(content, str):  # error message
            return ExtractionResult(error=content)

        # Load context files
        self._log("   Loading context...", 'info')
        context = self.load_context_files()

        model = model_override or self.config['model']

        # Steps 2-3 when gemini_type_check is on: a light call reads the item's
        # identity, Gemini decides the type from it, and the extraction below
        # runs once, told that type. Otherwise (or when Gemini finds nothing)
        # Claude chooses the type itself, with its type analysis.
        gemini, decided = None, None
        if self.config.get('gemini_type_check'):
            decided, gemini = self._decide_type_first(content, model)

        # Build prompt
        prompt = self.build_prompt(content)
        if decided:
            prompt += self._decided_type_instruction(decided, gemini.get('reason') or '')
        if self.config.get('library_exemplars'):
            prompt += self._exemplar_block(decided, gemini)

        # Call Claude API
        if model_override:
            self._log(f"   Sending to Claude ({model}, careful)...", 'info')
        else:
            self._log("   Sending to Claude...", 'info')

        try:
            message = self._create(
                'extraction', self.config['max_tokens'],
                model=model,
                messages=[
                    {"role": "user", "content": self._cached_message_content(context, prompt)}
                ]
            )

            raw = response_text(message)

            # "Output ONLY the BibLaTeX entry" is not always obeyed: a model
            # explaining itself quotes `@Type` in prose ahead of the entry
            # (#32). Isolated once, here, so everything downstream sees only
            # the entry; commentary is discarded with a warning. A reply with
            # no entry passes through for save_entry() to reject, and skips
            # enrichment, which would spend lookups on prose.
            raw, type_analysis = enrich.split_type_analysis(raw)
            entry, extra = enrich.isolate_entry(raw)
            if entry is None:
                bibtex_entry = raw
                self._log("   ⚠️  Response contains no complete BibLaTeX entry", 'warning')
            else:
                bibtex_entry = entry
                if extra:
                    self._log(
                        f"   ⚠️  Response carried {len(extra)} characters outside the entry "
                        "(commentary or a second entry) - discarded, entry kept",
                        'warning'
                    )

            # The nodate move runs before anything reads the Date: a Date
            # holding "n.d." would otherwise count as a date and cost the
            # entry its Url.
            bibtex_entry, moved = enrich.move_nodate_to_year(bibtex_entry)
            if moved:
                self._log("   Moved \\bibstring{nodate} out of Date, which discards it", 'warning')

            # needs_color: the entry is coloured in BibDesk only for a real
            # failure - biber rejects it, its source is thin or not the work,
            # or (require_langid) it has no Langid. A doubt the pipeline could
            # not settle (an unconfirmed value, a disagreeing record) is
            # logged, not coloured: colour that marks most entries marks none.
            needs_color = False
            field_sources = None
            self._local.scholar_fields = set()

            # Step 3a: the question bank, for the type the entry now has.
            if gemini is not None and entry is not None:
                final_type = enrich.get_entry_type(bibtex_entry)
                gemini['final_type'] = final_type
                if decided and final_type != decided:
                    gemini['status'] = 'not-followed'
                    self._log(f"   ⚠️  Extraction wrote @{final_type}, not Gemini's @{decided}", 'warning')
                if decided:
                    self._ask_question_bank(gemini, final_type)
            if self.config.get('enrich_missing_fields', True) and entry is not None:
                bibtex_entry, field_sources = self.enrich_entry(bibtex_entry, content)
                bibtex_entry, _unsettled = self.verify_and_flag_recollection(bibtex_entry, content)
                replaced = getattr(self._local, 'replaced', None)
                if replaced:
                    note = "CrossRef replaced recollection: " + ", ".join(replaced)
                    field_sources = f"{field_sources}; {note}" if field_sources else note

            # The library (library_match): a duplicate is reported; a chapter
            # whose book the library holds takes the book's fields in the
            # library's form. After the grounding audit, which would report
            # them as inferred; before Gemini's answers, which fill gaps only,
            # so the library outranks them.
            if entry is not None and self.config.get('library_match', True):
                bibtex_entry, field_sources = self._match_library(bibtex_entry, field_sources)

            # Gemini's field answers go in after the grounding audit, which
            # would otherwise report them as inferred: they fill gaps only and
            # never overwrite; disagreements are logged.
            if gemini and gemini.get('questions'):
                bibtex_entry, field_sources, _unsettled = self._apply_gemini_answers(
                    bibtex_entry, gemini, field_sources)

            # Structural safety net: the prompt above already asks Claude not
            # to include these, but doesn't reliably follow through (e.g. a
            # PDF whose own body text states a URL can still get a Url field
            # despite the instruction against it) - so strip them here rather
            # than trusting prompt compliance alone. After enrichment, not
            # before: a webloc entry with no Date keeps its Url/Urldate, and
            # verify_and_flag_recollection() may then fill that Date in.
            bibtex_entry, stripped = enrich.strip_forbidden_fields(bibtex_entry)
            if stripped:
                self._log(f"   Stripped disallowed field(s): {', '.join(stripped)}", 'warning')

            # Langid decides quotation marks, key terms and punctuation under
            # autolang=other, so it is normalized in code rather than trusted
            # to the prompt: English variants become `american`, and, where
            # config sets require_langid, an entry without one goes to review.
            if entry is not None:
                bibtex_entry, langid = enrich.normalize_langid(bibtex_entry)
                if langid == 'rewritten':
                    self._log("   Rewrote an English Langid to `american`", 'warning')
                elif langid == 'missing' and self.config.get('require_langid'):
                    self._log("   ⚠️  No Langid - flagging for review", 'warning')
                    needs_color = True

            # House cleanup: the rules the library was normalized with, then
            # the library's own spelling for values whose house form is "as
            # the library writes it". Fixed and logged; no review marking.
            if entry is not None and self.config.get('house_cleanup', True):
                bibtex_entry, rules = house_style.normalize(bibtex_entry)
                if rules:
                    self._log(f"   House cleanup: {', '.join(rules)}", 'info')
                # The cleanup spec's mechanical repairs (src/repairs.py).
                bibtex_entry, applied = house_style.repair(
                    bibtex_entry, self.config.get('library_bib_file'))
                for a in applied:
                    self._log(f"   Repaired - {a}", 'info')
                for d in repairs.date_problems(bibtex_entry):
                    self._log(f"   ⚠️  {d}", 'warning')
                bibtex_entry, snapped = house_style.snap_to_library(
                    bibtex_entry, self.config.get('library_bib_file'))
                for f, old, new in snapped:
                    self._log(f"   {f.capitalize()}: {old!r} -> the library's {new!r}", 'info')

            inferred = list(self.inferred_fields) if entry is not None else []
            if inferred:
                self._log(f"   ⚠️  Inferred, not read from the source: {', '.join(inferred)}", 'warning')

            # biber's data-model check (bib_validate), with biblatex-chicago's
            # documented usages excused. A field biber rejects for the type, or
            # a mandatory one missing, sends the entry to review. Skipped, and
            # said so, where TeX is not installed; validate_entries: false
            # turns it off.
            validation = []
            repaired_once = False
            if entry is not None and self.config.get('validate_entries', True):
                validation = self._validate(bibtex_entry)
                # One repair attempt (cleanup spec §1): Claude gets biber's
                # warnings, the source and the repair rules, then the entry is
                # validated again. What remains is a real failure: coloured.
                if validation and self.config.get('repair_entries', True):
                    bibtex_entry, repaired = self._repair_entry(
                        bibtex_entry, validation, content, model, retype=not decided)
                    repaired_once = True
                    if repaired:
                        validation = self._validate(bibtex_entry)
                if validation:
                    needs_color = True

            # Render check (cleanup spec §2), opt-in: compile the entry and
            # look at what prints. See _render_check for what colours.
            if entry is not None and self.config.get('render_check', False):
                bibtex_entry, render_color = self._render_check(
                    bibtex_entry, content, model, retype=not decided, may_repair=not repaired_once)
                needs_color = needs_color or render_color

            # content.amber is web_source.py's plausibility check flagging a
            # source that's genuine but thin or sparse (no Author/Doi/
            # PublicationDate beyond Urldate, or a short body) - not wrong,
            # just worth a glance. Folded into needs_color, so it reaches BibDesk's review
            # color by exactly the path a PDF's review state does. The
            # "% AMBER: ..." comment save_entry writes from amber_reason
            # carries the same flag on the other branch, where entries are
            # appended as text and no color is possible; each survives where
            # the other cannot.
            amber_reason = None
            if content.amber:
                needs_color = True
                amber_reason = content.amber_reason
                self._log(f"   ⚠️  Thin/sparse source ({content.amber_reason}) - flagging for review", 'warning')

            self._log("   Validating entry...", 'info')
            self._log("   ✓ Complete", 'success')

            return ExtractionResult(
                entry=bibtex_entry,
                source_label=f"{content.label} ({content.url or path.name})",
                needs_color=needs_color,
                amber_reason=amber_reason,
                field_sources=field_sources,
                inferred_fields=inferred,
                validation=validation,
                type_analysis=type_analysis,
                gemini=gemini,
            )

        except Exception as e:
            return ExtractionResult(error=f"Error: {e}")

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

    IDENTITY_PROMPT = """Identify the bibliographic item this source is, from its opening
pages below. Do not decide an entry type. Reply with one JSON object and nothing
else, using null for anything the text does not show:

{{"title": the item's own title, "subtitle": its subtitle,
 "authors": ["Last, First", ...], "editors": ["Last, First", ...],
 "container": the title of the book, journal, website or reference work the
   item is part of,
 "entry_heading": for an entry in a dictionary or encyclopedia, the entry's heading,
 "supplement": for an untitled introduction, preface, foreword, afterword or
   notes, which of these it is,
 "reviewed_work": for a review with no title of its own, the title of the work reviewed,
 "year": the year of publication,
 "kind": a short phrase saying what the item looks like}}

<source label="{label}">
{text}
</source>"""

    def _identify(self, content, model):
        """The item's identity, read by one light Claude call from the start of
        the source: a dict, or None when the reply is not JSON."""
        import json
        text = (content.text or '')[:int(self.config.get('identity_source_chars', 8000))]
        if content.metadata:
            text = "\n".join(f"{k}: {v}" for k, v in content.metadata.items()) + "\n\n" + text
        message = self._create('identity', int(self.config.get('identity_max_tokens', 800)), model=model,
                               messages=[{"role": "user", "content": self.IDENTITY_PROMPT.format(
                                   label=content.label, text=text)}])
        m = re.search(r"\{.*\}", response_text(message), re.S)
        try:
            ident = json.loads(m.group(0)) if m else None
        except ValueError:
            return None
        return ident if isinstance(ident, dict) else None

    @staticmethod
    def _identity_fields(ident):
        """(fields, type_hint) for gemini_search.query_from_fields(): the item
        named the way an entry of its kind names it (Lista for an entry in a
        reference work, Type for a supplement, the reviewed work for a review)."""
        def s(k):
            v = ident.get(k)
            return v.strip() if isinstance(v, str) else ''
        def names(k):
            v = ident.get(k)
            return ' and '.join(x for x in v if isinstance(x, str)) if isinstance(v, list) else s(k)
        fields = {'author': names('authors'), 'editor': names('editors'), 'date': s('year')}
        if s('entry_heading'):
            fields.update(lista=s('entry_heading'), title=s('container') or s('title'))
            return fields, 'inreference'
        if s('supplement'):
            fields.update(type=s('supplement'), title=s('container') or s('title'))
            return fields, 'suppbook'
        if s('reviewed_work'):
            fields.update(title=s('reviewed_work'), journaltitle=s('container'))
            return fields, 'review'
        fields.update(title=s('title'), subtitle=s('subtitle'), booktitle=s('container'))
        return fields, ''

    def _decide_type_first(self, content, model):
        """Steps 2-3: (decided_type or None, info). info['status'] is decided,
        not-found, invalid-type, skipped: <why> or error: <message>; on
        anything but decided the extraction chooses the type itself."""
        info = {'status': None, 'usage': []}
        key = self.config.get('gemini_api_key')
        if not key:
            info['status'] = 'skipped: no gemini_api_key'
            return None, info
        self._log("   Reading the item's identity...", 'info')
        try:
            ident = self._identify(content, model)
        except Exception as e:  # noqa: BLE001 - the extraction still runs
            info['status'] = f'error: identity: {e}'
            return None, info
        info['identity'] = ident
        if not ident:
            info['status'] = 'skipped: no identity'
            return None, info
        fields, hint = self._identity_fields(ident)
        title, query = gemini_search.query_from_fields(fields, hint)
        info['query'] = query
        if not title:
            info['status'] = 'skipped: no title'
            return None, info
        gm = self.config.get('gemini_model') or gemini_search.MODEL
        self._log("   Asking Gemini for the type...", 'info')
        r = gemini_search.decide_type(query, TYPE_DEFINITIONS, key, model=gm)
        info['usage'] += r.get('usage') or []
        info.update({k: r.get(k) for k in ('type', 'found', 'reason', 'answer', 'sources', 'queries', 'history')})
        if r.get('error'):
            info['status'] = f"error: {r['error']}"
            self._log(f"   ⚠️  Gemini type check failed ({r['error']}) - Claude chooses the type", 'warning')
            return None, info
        if not r.get('found') or not r.get('type'):
            info['status'] = 'not-found'
            self._log("   Gemini did not identify the work - Claude chooses the type", 'info')
            return None, info
        if r['type'] not in gemini_search.listed_types(TYPE_DEFINITIONS):
            info['status'] = 'invalid-type'
            self._log(f"   ⚠️  Gemini gave a type not on the list (@{r['type']}) - Claude chooses the type", 'warning')
            return None, info
        info['status'] = 'decided'
        self._log(f"   Gemini: @{r['type']} ({r.get('reason', '')})", 'info')
        return r['type'], info

    @staticmethod
    def _decided_type_instruction(entry_type, reason):
        """What the extraction is told when Gemini has decided the type."""
        return (f"\n\nAn external check, grounded in Google Search, has decided this work's "
                f"entry type: @{entry_type}. Its reason: {reason}\nUse @{entry_type}. In step 1, "
                f"write the type analysis briefly and choose @{entry_type}. Encode the entry under "
                f"@{entry_type}, putting the container's information in the fields @{entry_type} "
                "uses. Never add a value the source does not show.")

    def _ask_question_bank(self, info, final_type):
        """Step 3a: the bank's questions for final_type, asked in Gemini's
        conversation; answers land in info for _apply_gemini_answers()."""
        key = self.config.get('gemini_api_key')
        gm = self.config.get('gemini_model') or gemini_search.MODEL
        bank_path = self.config.get('gemini_questions_file') or 'prompt-context/gemini-questions.yaml'
        try:
            bank = gemini_search.load_bank(Path(bank_path).expanduser())
        except (OSError, AssertionError, yaml.YAMLError) as e:
            info['questions_status'] = f"error: question bank: {e}"
            self._log(f"   ⚠️  Question bank unreadable ({e}) - no questions asked", 'warning')
            return
        questions = gemini_search.select(bank, final_type)
        # Every other field the type takes, asked the same way: Gemini gives
        # a value only where a page it found shows one. Bank questions about
        # the same field win (they carry wording tuned for it).
        asked = {q['use'].split(None, 1)[1].strip().lower() for q in questions
                 if q['use'].startswith('field ')}
        questions = questions + [
            {'id': f'field_{f}', 'when': [final_type], 'use': f'field {f}', 'auto': True,
             'ask': f"{f}: {d}".strip().rstrip(':')}
            for f, d in type_fields.fields_for(final_type) if f not in asked]
        if not questions:
            return
        self._log(f"   Asking Gemini {len(questions)} question(s)...", 'info')
        qa = gemini_search.ask_questions(info.get('history') or [], questions, key, model=gm)
        info['usage'] += qa.get('usage') or []
        if qa.get('error'):
            info['questions_status'] = f"error: {qa['error']}"
            self._log(f"   ⚠️  Gemini questions failed ({qa['error']})", 'warning')
        else:
            info['questions_status'] = 'answered'
            info['questions'] = questions
            info['answers'] = qa['answers']

    def _library(self):
        """(Lib or None, citekeys never matched). library_exclude_keys is set
        by the eval harness: its sources are library entries themselves."""
        lib = library_match.load(self.config.get('library_bib_file'))
        return lib, set(self.config.get('library_exclude_keys') or ())

    def _match_library(self, entry_text, field_sources):
        """Duplicates logged; container fields from the library's siblings
        filled (library_match.container_fill). (entry_text, field_sources)."""
        try:
            lib, exclude = self._library()
        except Exception as e:  # noqa: BLE001 - the library must never cost the entry
            self._log(f"   ⚠️  Library unreadable ({e}) - no matching", 'warning')
            return entry_text, field_sources
        if lib is None:
            return entry_text, field_sources
        dups = library_match.duplicates(entry_text, lib, exclude)
        if dups:
            # Kept on the saved entry too: in a batch the log line scrolls past.
            self._log(f"   ⚠️  Already in the library as {', '.join(dups)}", 'warning')
            note = "Already in library as " + ", ".join(dups)
            field_sources = f"{field_sources}; {note}" if field_sources else note
        etype = enrich.get_entry_type(entry_text)
        allowed = {f for f, _ in type_fields.fields_for(etype)} or None
        scholar = self._scholar_fields()
        # The library outranks a value from a search snippet (Scholar) and one
        # Claude recollected that no record confirmed (ruling 2026-11-05).
        recollected = {f.lower() for f in self.inferred_fields}
        entry_text, r = library_match.container_fill(
            entry_text, lib, allowed=allowed, replaceable=set(scholar) | recollected,
            exclude=exclude | set(dups))
        # A value the library now holds or confirms is grounded: no longer
        # Scholar's alone (so Gemini does not outrank it), nor inferred.
        settled = set(r['replaced'] + r['snapped'] + r['confirmed'])
        scholar.difference_update(settled)
        if recollected & settled:
            self.inferred_fields = [f for f in self.inferred_fields if f.lower() not in settled]
            self._log(f"   The library settles recollected: {', '.join(sorted(recollected & settled))}", 'info')
        filled = r['added'] + r['replaced']
        if filled:
            note = f"Library ({', '.join(r['siblings'])}): " + ", ".join(filled)
            field_sources = f"{field_sources}; {note}" if field_sources else note
            self._log(f"   Filled from the library's {', '.join(r['siblings'])}: {', '.join(filled)}", 'info')
        for f in r['snapped']:
            self._log(f"   {f.capitalize()}: the library's spelling", 'info')
        for d in r['differs']:
            self._log(f"   Library differs (entry kept) - {d}", 'info')
        if r['disagree']:
            self._log(f"   Library siblings disagree, left alone: {', '.join(r['disagree'])}", 'info')
        return entry_text, field_sources

    EXEMPLAR_INTRO = (
        "\n\nEntries from the user's own library, in the house form this entry should "
        "follow (field choice, spelling of names, places and publishers, title casing). "
        "They describe other works: take their form, never their values, unless the "
        "source shows the same value.\n")

    def _exemplar_block(self, decided, gemini):
        """library_exemplars: up to three library entries for the prompt - the
        container's own (from the identity call) first, then the decided
        type's. '' when there is no library or nothing to show."""
        try:
            lib, exclude = self._library()
        except Exception:  # noqa: BLE001
            return ''
        ident = (gemini or {}).get('identity') or {}
        container = ident.get('container') if isinstance(ident.get('container'), str) else None
        found = library_match.exemplars(lib, entry_type=decided, container=container,
                                        exclude=exclude, n=int(self.config.get('library_exemplar_count', 3)))
        if not found:
            return ''
        self._log(f"   Showing {len(found)} library entr{'y' if len(found) == 1 else 'ies'} as house-form examples", 'info')
        return self.EXEMPLAR_INTRO + "\n\n".join(found)

    def _scholar_fields(self):
        """This source's fields whose value came only from Google Scholar - a
        search snippet, which Gemini's grounded answer outranks (ruling
        2026-11-05). Reset per source in extract_bibtex."""
        if getattr(self._local, 'scholar_fields', None) is None:
            self._local.scholar_fields = set()
        return self._local.scholar_fields

    def _apply_gemini_answers(self, entry_text, gemini, field_sources):
        """Step 3a's answers applied: (entry_text, field_sources, needs_color).
        A field Gemini fills is recorded in field_sources, not coloured; only a
        bank check or bank field that contradicts the entry colours it."""
        entry_text, report = gemini_search.apply_answers(
            entry_text, gemini['questions'], gemini.get('answers') or {},
            gemini.get('final_type') or enrich.get_entry_type(entry_text),
            outranks=self._scholar_fields())
        if report.get('replaced'):
            note = "Gemini over Scholar: " + ", ".join(report['replaced'])
            field_sources = f"{field_sources}; {note}" if field_sources else note
            self._log(f"   Gemini's value replaced Scholar's: {', '.join(report['replaced'])}", 'info')
        gemini['answer_report'] = report
        if report['added']:
            note = "Gemini: " + ", ".join(report['added'])
            field_sources = f"{field_sources}; {note}" if field_sources else note
            self._log(f"   Filled from Gemini: {', '.join(report['added'])}", 'info')
        for c in report['conflicts']:
            self._log(f"   ⚠️  Gemini disagrees - {c}", 'warning')
        for c in report.get('differs', []):
            self._log(f"   Gemini differs (entry kept) - {c}", 'info')
        return entry_text, field_sources, bool(report['conflicts'])

    # The repair prompt: the cleanup spec's §3.1 type table and §3.11 order.
    REPAIR_RULES = """Repair, in this order (biblatex-chicago, notes and bibliography):
1. If the type is wrong, re-type the entry{retype_note}. Guidance:
   - chapter in a book by the same author(s): @inbook; in an edited multi-author volume:
     @incollection with Editor and Booktitle; in an exhibition catalog: @incollection,
     Note = {exhibition catalog}; a paper in published proceedings: @inproceedings;
   - an untitled introduction, preface, foreword or afterword by another author: @suppbook,
     Title = the book, Type marking the part;
   - a journal piece with only a generic title (interview by ..., review of ...): @review;
     with a title of its own: @article, the genre in Titleaddon; a colloquy with many
     participants: @article, Editor = the convener, Titleaddon = {colloquy};
   - a blog post: @article, Entrysubtype = {magazine}, Location = {blog}, Journal = the blog;
   - a podcast episode: @audio, Title = the episode, Booktitle = the podcast, Note = {podcast};
   - a single-author dictionary: @book (@reference prints no Author);
   - a published dissertation: @book, Note = {\\bibstring{phdthesis}, University};
   - a report, working paper or manual from an organization: @report, Institution, Type,
     Number; a manuscript, handout or talk with no publisher: @unpublished, Note describing it;
   - an annotated score or archival item: @misc, Entrysubtype, Institution = the archive.
2. If a field's value repeats another field, delete it.
3. Otherwise move the value into a field the type prints, usually Note or Titleaddon.
Never add a value the source does not show; never delete information. Keep Journal (not
Journaltitle), Langid, and the bookkeeping fields as they are."""

    def _repair_entry(self, entry_text, warnings, content, model, retype=False):
        """One Claude call to repair what biber or the render check found.
        (entry_text, changed). A value neither the source nor the entry held is
        reverted; protected and bookkeeping fields are restored."""
        rules = self.REPAIR_RULES.replace(
            "{retype_note}", "" if retype else " - not here: the type was decided upstream; keep it")
        source = (content.text or '')[:12000]
        prompt = (f"This BibLaTeX entry has problems:\n" + "\n".join(f"- {w}" for w in warnings)
                  + f"\n\n{rules}\n\n<source>\n{source}\n</source>\n\n<entry>\n{entry_text}\n</entry>\n\n"
                  "Reply with the repaired entry only.")
        self._log(f"   Repairing {len(warnings)} problem(s)...", 'info')
        try:
            message = self._create('repair', int(self.config.get('max_tokens', 4000)), model=model,
                                   messages=[{"role": "user", "content": prompt}])
        except Exception as e:  # noqa: BLE001 - the entry stands
            self._log(f"   ⚠️  Repair call failed ({e})", 'warning')
            return entry_text, False
        fixed, _ = enrich.isolate_entry(response_text(message))
        if fixed is None:
            self._log("   ⚠️  Repair reply was not an entry - kept as it was", 'warning')
            return entry_text, False
        if not retype and enrich.get_entry_type(fixed) != enrich.get_entry_type(entry_text):
            self._log("   ⚠️  Repair changed a decided type - kept as it was", 'warning')
            return entry_text, False
        old, new = enrich.parse_bibtex_fields(entry_text), enrich.parse_bibtex_fields(fixed)
        known = _tokens(content.text or '') | set().union(*(_tokens(v) for v in old.values()))
        for f, v in new.items():
            if v != old.get(f) and not _sourced(v, known):
                fixed = enrich.set_field(fixed, f, old[f]) if f in old else enrich.remove_field(fixed, f)
                self._log(f"   Repair added {f} the source does not show - reverted", 'info')
        # Bookkeeping and protected fields come back as written.
        for line in entry_text.splitlines():
            m = re.match(r'\s*([A-Za-z][\w-]*)\s*=', line)
            if m and ('-' in m.group(1) or m.group(1).lower() in ('langid', 'keywords')) \
                    and not re.search(rf'(?im)^\s*{re.escape(m.group(1))}\s*=', fixed):
                body = fixed.rstrip()
                if body.endswith('}'):
                    head = body[:-1].rstrip()
                    fixed = (head if head.endswith(',') else head + ',') + '\n' \
                        + line.rstrip().rstrip(',') + ',\n}'
        changed = fixed != entry_text
        if changed:
            self._log("   Repaired the entry", 'info')
        return fixed, changed

    def _render_check(self, entry_text, content, model, retype=False, may_repair=True):
        """Compile the entry and read what prints (cleanup spec §2).
        (entry_text, needs_color). Coloured: a compile failure; a filled field
        that never reaches the page (moved into Note, and coloured even so:
        the maintainer's ruling); wrongly nested quotes or a string printed
        twice that survive one repair. Not coloured: the check could not run."""
        import bib_render
        from bib_validate import ValidatorUnavailable
        key = (re.search(r'@\w+\{([^,\s]+),', entry_text) or [None, 'x'])[1]
        try:
            text = bib_render.render(entry_text, key)
        except ValidatorUnavailable as e:
            self._log(f"   Render check skipped: {str(e).splitlines()[0]}", 'dim')
            return entry_text, False
        except bib_render.RenderFailed as e:
            self._log(f"   ⚠️  The entry does not compile: {str(e).splitlines()[0]}", 'warning')
            return entry_text, True
        except Exception as e:  # noqa: BLE001
            self._log(f"   Render check skipped: {e}", 'dim')
            return entry_text, False
        colour = False
        fields = {k.lower(): v for k, v in enrich.parse_bibtex_fields(entry_text).items()}
        dropped = bib_render.dropped_fields(fields, text)
        if dropped:
            colour = True
            moved = [f"{fields[f]}" for f in dropped if f != 'note']
            for f in dropped:
                if f != 'note':
                    entry_text = enrich.remove_field(entry_text, f)
            if moved:
                old = enrich.parse_bibtex_fields(entry_text)
                note = next((v for k, v in old.items() if k.lower() == 'note'), '')
                value = ", ".join(([note] if note else []) + moved)
                entry_text = (enrich.set_field(entry_text, 'note', value) if note
                              else enrich.add_field(entry_text, 'note', value))
            self._log(f"   ⚠️  Never printed: {', '.join(dropped)} - moved into Note", 'warning')
        problems = render_problems(text, fields)
        if problems and may_repair:
            entry_text, changed = self._repair_entry(entry_text, problems, content, model, retype)
            if changed:
                try:
                    problems = render_problems(bib_render.render(entry_text, key), fields)
                except Exception:  # noqa: BLE001
                    pass
        if problems:
            colour = True
            for p in problems:
                self._log(f"   ⚠️  {p}", 'warning')
        return entry_text, colour

    def enrich_entry(self, entry_text, content):
        """
        Fill in bibliographic fields the source didn't supply (volume, issue,
        pages, chapter, etc.) via CrossRef/Google Scholar, then ask Claude to
        merge only those fields into the entry.

        Args:
            entry_text: The BibLaTeX entry produced so far
            content: The SourceContent (PDF or webpage) the entry was drawn from

        Returns (entry_text, field_sources): the possibly-unchanged entry, and
        a one-line per-field provenance summary, or None when nothing was
        enriched. The summary used to be prepended to the returned text as a
        "% Sources -- ..." comment and sliced back off by save_entry(); it is
        now returned beside the text and rendered once, at the point of
        writing. See extraction_result.py.
        """
        entry_type = enrich.get_entry_type(entry_text)
        fields = enrich.parse_bibtex_fields(entry_text)
        required, desired = enrich.missing_fields(entry_type, fields)
        if not required and not desired:
            self._log(f"   Source: {content.label} (all fields present, no enrichment needed)", 'info')
            return entry_text, None
        if not enrich.fillable_gaps(entry_type, fields, content.text):
            self._log(f"   Source: {content.label} (missing {', '.join(required + desired)}; "
                      f"no lookup can supply it)", 'info')
            return entry_text, None

        title = enrich.strip_latex(fields.get('title', ''))
        found, field_sources = enrich.gather_enrichment(
            content.text, title, entry_type, fields,
            crossref_email=self.config.get('crossref_email'),
            scrapingdog_api_key=self.config.get('scrapingdog_api_key'),
        )
        if not found:
            self._log(f"   ⚠️  Missing fields not found via CrossRef/Scholar: {', '.join(required + desired)}", 'warning')
            return entry_text, None

        by_source = {}
        for field, source in field_sources.items():
            by_source.setdefault(source, []).append(field)
        self._scholar_fields().update(f for f, src in field_sources.items() if src == 'Google Scholar')
        summary = '; '.join(f"{src}: {', '.join(sorted(fs))}" for src, fs in by_source.items())
        self._log(f"   Enriched -- {summary}", 'info')

        context = self.load_context_files()
        prompt = self._build_enrich_prompt(entry_text, found)
        try:
            message = self._create(
                'merge', self.config['max_tokens'],
                model=self.config['model'],
                messages=[{"role": "user", "content": self._cached_message_content(context, prompt)}],
            )
            # isolate_entry(), not clean_bibtex(): a reply with no entry must
            # fail here, not come back as prose.
            merged, _ = enrich.isolate_entry(response_text(message))
            if merged is None:
                return entry_text, None
        except Exception as e:
            self._log(f"   ⚠️  Enrichment merge failed: {e}", 'warning')
            return entry_text, None

        # Per-field provenance, returned so save_entry can persist it as a
        # comment - it should survive past this run's log rather than being
        # visible only in the console/window.
        source_fields = sorted(f for f, v in fields.items() if v)
        source_lines = []
        if source_fields:
            source_lines.append(f"{content.label}: {', '.join(source_fields)}")
        for src, fs in by_source.items():
            source_lines.append(f"{src}: {', '.join(sorted(fs))}")

        return merged, '; '.join(source_lines)

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
        message = self._create(
            'audit',
            # Generous enough that a reply which ignores the one-line format and
            # narrates instead still reaches the marker. At 150 the model would
            # analyse each field in prose, hit the cap mid-sentence, and never
            # emit the marker at all - see the parse-failure branch below.
            self.config.get('audit_max_tokens', 600),
            model=self.config['model'],
            messages=[{"role": "user", "content": prompt}],
        )
        response = response_text(message)

        m = re.search(r'UNGROUNDED_FIELDS:\s*(.+)', response)
        if not m:
            # No marker means the audit did not return a verdict - NOT that
            # every field is grounded. Failing open here silently disarms the
            # one check standing between a recalled value and a saved entry,
            # so treat it as "could not verify" and let the caller flag it.
            self._log("   ⚠️  Grounding audit returned no verdict - entry kept as extracted", 'warning')
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
            + (" - REPLACE: the claimed value was not read from the source, and the "
               "verified record is this work's; use the verified value"
               if c.get('kind') == 'replace' else "")
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

For a field marked REPLACE, the claimed value was the initial extraction's
recollection, not read from the source: put the verified value in its place,
formatted the same way, rather than merging the two.

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
            message = self._create(
                'reconcile', self.config['max_tokens'],
                model=self.config['model'],
                messages=[{"role": "user", "content": self._cached_message_content(context, prompt)}],
            )
            # isolate_entry(), not clean_bibtex() - see enrich_entry().
            merged, _ = enrich.isolate_entry(response_text(message))
            if merged is None:
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
        # Fields whose value was neither read from the source nor confirmed by
        # a primary record: reported to the user (log and a % comment).
        self.inferred_fields = []
        self._local.replaced = []

        try:
            ungrounded = self._audit_entry_grounding(entry_text, content)
        except Exception as e:
            # Flag rather than pass. An audit that didn't run leaves the entry
            # unverified, which is precisely what the amber marker is for -
            # returning False here would present an unchecked entry as clean.
            self._log(f"   ⚠️  Grounding audit failed ({e}) - entry kept as extracted", 'warning')
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
        provenance = {}
        reconcile_candidates, additions, unresolved = enrich.verify_recollection(
            work_title, ungrounded, fields, year=year,
            crossref_email=self.config.get('crossref_email'),
            scrapingdog_api_key=self.config.get('scrapingdog_api_key'),
            log=lambda msg: self._log(f"     {msg}", 'dim'),
            provenance=provenance,
        )
        self._scholar_fields().update(f for f, src in provenance.items() if src == 'Google Scholar')

        if reconcile_candidates:
            entry_text = self.reconcile_fields(entry_text, content, reconcile_candidates)
            replaced = [c['field'] for c in reconcile_candidates if c.get('kind') == 'replace']
            if replaced:
                self._local.replaced = replaced
                self._log(f"   Replaced recollected value(s) with CrossRef's: {', '.join(replaced)}", 'info')

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
                self.inferred_fields = still_ungrounded
            if still_missing_container:
                reasons.append(f"container-level field(s) ({', '.join(still_missing_container)})")
            if reasons:
                self._log(
                    f"   ⚠️  Could not verify {' and '.join(reasons)} via CrossRef/Scholar - left as extracted",
                    'warning'
                )

        return entry_text, unresolved

    def clean_bibtex(self, bibtex_entry):
        """The entry alone, without code fencing or surrounding prose.

        Falls back to the stripped response when it holds no complete entry,
        so a caller that prints or saves the result still shows what came
        back. Whether there IS an entry is enrich.isolate_entry()'s question,
        and save_entry() asks it directly rather than inferring it from this.
        """
        entry, _ = enrich.isolate_entry(bibtex_entry)
        return entry if entry is not None else bibtex_entry.strip()

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
        """Save the BibDesk document for main_bib_file if it is currently open.

        A `Save Document` script hook runs inside `save` and may edit the
        document after its bytes reach disk, so a document still modified
        afterwards is saved once more. The timeouts allow for a slow hook
        rather than reporting a completed save as failed.
        """
        bib_path = str(Path(self.config['main_bib_file']).expanduser().resolve())
        script = f'''
tell application "BibDesk"
    repeat with d in documents
        if path of d is "{bib_path}" then
            set theDoc to contents of d
            with timeout of 600 seconds
                save theDoc
                try
                    if modified of theDoc then save theDoc
                end try
            end timeout
            return "ok"
        end if
    end repeat
    return "not open"
end tell'''
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=1260
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

    def save_entry(self, result, pdf_path):
        """Validate, enrich with a BibDesk bookmark, and append to the main bib file.

        `result` is the ExtractionResult extract_bibtex() returned. Its review
        state arrives as attributes; it is no longer prepended to the entry
        text as `%` markers for this method to match positionally and slice
        off. That protocol is what made the amber-colouring regression both
        possible and silent - one marker's regex could never match, and
        because re.match anchors at position 0, every matcher below it then
        failed too, for every entry and every source type, reporting nothing.
        See extraction_result.py and issues #17/#18.

        The `%` comments still reach the saved file, rendered here from the
        result rather than parsed out of it.
        """
        pdf_path = Path(pdf_path)

        needs_color = result.needs_color
        # Outermost first: Source, then AMBER, then Sources. Isolating the
        # entry discards everything outside it, so these are attached after,
        # not before.
        comment_block = ''.join(line + "\n" for line in result.comment_lines())

        # Reject anything that is not one complete entry, before touching the
        # main file (#32). The brace check only names the reason.
        entry, _ = enrich.isolate_entry(result.entry)
        if entry is None:
            rejected = result.entry.strip()
            error_msg = enrich.brace_problem(rejected) or "response is not a BibTeX entry"
            self.save_failure(rejected, pdf_path.name, error_msg)
            self.notify_failure(pdf_path.name, error_msg)
            return False

        entry = comment_block + entry

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
                      + (" (see the % AMBER comment in the saved entry)" if result.amber_reason else ""),
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

        workers = max(1, int(self.config.get('batch_workers', 3)))
        done = [0]

        def one(i, pdf_path):
            """Extract, save and move one source; returns its outcome."""
            if progress_window and progress_window.cancelled:
                return 'skipped', pdf_path.name, None
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
            result = self.extract_bibtex(pdf_path, batch_info=(i, total), model_override=model_for_file)
            if result.failed:
                self._log(f"   ✗ {result.error}", 'error')
                return 'failed', pdf_path.name, result.error
            with self._lock:   # one writer to the staging file / BibDesk at a time
                saved = self.save_entry(result, pdf_path)
                if saved and move_files:
                    self.move_to_processed(pdf_path)
            if not saved:
                return 'failed', pdf_path.name, "save failed"
            return 'success', pdf_path.name, None

        def run(i, hold=True):
            """one() for files[i - 1]; with `hold`, its messages are held and
            emitted as a block when it finishes."""
            pdf_path = files[i - 1]
            if hold and workers > 1:
                self._local.held = []
            try:
                outcome = one(i, pdf_path)
            except Exception as e:  # noqa: BLE001 - one bad source must not stop the batch
                outcome = 'failed', pdf_path.name, f"{type(e).__name__}: {e}"
                self._log(f"   ✗ {outcome[2]}", 'error')
            finally:
                held, self._local.held = getattr(self._local, 'held', None), None
            with self._lock:
                for message, level in held or []:
                    self._emit(message, level)
                status, name, error = outcome
                if status == 'skipped':
                    return outcome
                done[0] += 1
                if status == 'success':
                    self._emit(f"   ✓ Done ({int(done[0] / total * 100)}% complete)", 'success')
            return outcome

        # The first source runs alone, so that it writes the prompt cache the
        # others then read; the rest run `batch_workers` at a time.
        outcomes = [run(1, hold=False)]
        with ThreadPoolExecutor(workers) as pool:
            outcomes += list(pool.map(run, range(2, total + 1)))
        for status, name, error in outcomes:
            if status == 'success':
                results['success'].append(name)
            elif status == 'failed':
                results['failed'].append((name, error))
            else:
                results['skipped'].append(name)
        if any(o[0] == 'skipped' for o in outcomes):
            self._log("Cancelled by user.", 'warning')

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

            result = agent.extract_bibtex(pdf_path)

            if result.failed:
                print(result.error, file=sys.stderr)
                continue

            clean_entry = agent.clean_bibtex(result.entry)
            if not args.no_save:
                saved = agent.save_entry(result, pdf_path)
                if not saved:
                    print(f"Error: failed to save entry for {pdf_path.name}", file=sys.stderr)
                    sys.exit(1)
            print(clean_entry)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
