#!/usr/bin/env python3
"""
Bibliographic extraction agent using Claude API.
Processes PDFs and generates BibLaTeX-Chicago entries.
"""

import sys
import argparse
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import yaml
from anthropic import Anthropic

from extract_pages import extract_content

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
        """Load configuration from YAML file."""
        config_path = Path(config_path)
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
        
        return config
    
    def load_context_files(self):
        """Load CLAUDE.md, biblio-template.bib, and optional ref file for context."""
        context = {}

        # Load CLAUDE.md
        claude_md_path = Path(self.config['claude_md_file'])
        if claude_md_path.exists():
            with open(claude_md_path) as f:
                context['claude_md'] = f.read()
        else:
            self._log(f"⚠️  Warning: {claude_md_path} not found", 'warning')
            context['claude_md'] = ""

        # Load biblio-template.bib
        template_path = Path(self.config['template_file'])
        if template_path.exists():
            with open(template_path) as f:
                context['template'] = f.read()
        else:
            self._log(f"⚠️  Warning: {template_path} not found", 'warning')
            context['template'] = ""

        # Load optional reference file (e.g. biblatex-chicago-notes-ref.md)
        ref_file = self.config.get('ref_file')
        context['ref'] = ""
        if ref_file:
            ref_path = Path(ref_file)
            if ref_path.exists():
                with open(ref_path) as f:
                    context['ref'] = f.read()
            else:
                self._log(f"⚠️  Warning: ref_file {ref_path} not found", 'warning')

        return context
    
    def build_prompt(self, pdf_text, context):
        """Build the prompt for Claude."""
        prompt = f"""I need you to extract bibliographic information from a PDF and create a BibLaTeX entry using the biblatex-chicago package (notes and bibliography style).

Here is the extracted text from the first 2 pages and last page of the PDF:

<pdf_text>
{pdf_text}
</pdf_text>

"""
        
        if context['claude_md']:
            prompt += f"""Here are the project guidelines:

<guidelines>
{context['claude_md']}
</guidelines>

"""
        
        if context['template']:
            prompt += f"""Here is a reference template showing the types and fields you should use:

<reference_template>
{context['template']}
</reference_template>

"""

        if context['ref']:
            prompt += f"""Here is a condensed reference for biblatex-chicago entry types and fields (notes and bibliography variant):

<biblatex_chicago_reference>
{context['ref']}
</biblatex_chicago_reference>

"""

        prompt += """Please:
1. Identify the publication type (@Book, @Article, @InCollection, etc.)
2. Extract all relevant bibliographic fields
3. Format as a single BibLaTeX entry using biblatex-chicago standards
4. Use a citation key in the format: AuthorYEAR (e.g., Smith2023)
5. Use single hyphens (-) for all ranges (pages, dates, etc.)
6. Do NOT include these fields: ISSN, ISBN, keywords, reference, devonthink

Output ONLY the BibLaTeX entry, with no additional commentary or explanation."""

        return prompt
    
    def extract_bibtex(self, pdf_path, batch_info=None):
        """
        Extract bibliographic information from a PDF.

        Args:
            pdf_path: Path to PDF file
            batch_info: Optional (current_index, total) tuple for batch progress display

        Returns:
            str: BibLaTeX entry or error message
        """
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            return f"Error: File not found: {pdf_path}"

        if batch_info:
            i, total = batch_info
            self._log(f"\n📄 Processing: {pdf_path.name}", 'info')
        else:
            self._log(f"📄 Processing: {pdf_path.name}", 'info')

        # Extract text from PDF
        self._log("   Extracting text...", 'info')

        quiet = not self.config.get('verbose', True)
        default_lang = self.config.get('default_ocr_language', 'eng')

        if quiet:
            language_prompt_fn = lambda: default_lang
        else:
            def language_prompt_fn():
                lang = self._ask_ocr_language(pdf_path.name)
                self._log(f"   OCR language: {lang}", 'info')
                return lang

        pdf_text = extract_content(pdf_path, quiet=quiet, language_prompt_fn=language_prompt_fn)

        if pdf_text.startswith("Error:"):
            return pdf_text

        # Load context files
        self._log("   Loading context...", 'info')
        context = self.load_context_files()

        # Build prompt
        prompt = self.build_prompt(pdf_text, context)

        # Call Claude API
        self._log("   Sending to Claude...", 'info')

        try:
            message = self.client.messages.create(
                model=self.config['model'],
                max_tokens=self.config['max_tokens'],
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            bibtex_entry = message.content[0].text

            self._log("   Validating entry...", 'info')
            self._log("   ✓ Complete", 'success')

            return bibtex_entry

        except Exception as e:
            return f"Error: {e}"
    
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

    def _save_via_bibdesk(self, entry, bib_path):
        """Open the staging file in BibDesk (if needed), import the entry, and auto-file it.

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
    auto file item 1 of thePubs
    return "ok"
end tell'''

        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip()
            if output == "ok":
                self._log("   ✓ Imported into BibDesk and auto-filed", 'success')
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
        if not self.config.get('notifications', True):
            return
        script = f'display notification "{message}" with title "Ostracon AI"'
        if subtitle:
            script += f' subtitle "{subtitle}"'
        subprocess.run(['osascript', '-e', script], capture_output=True)

    def notify_failure(self, pdf_name, error_msg):
        """Send a macOS notification about a validation failure."""
        if not self.config.get('notifications', True):
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

        # Attach a BibDesk file bookmark
        entry = self.add_bdsk_bookmark(entry, pdf_path)

        if self.config.get('autofile_bibdesk', False):
            output_path = Path(self.config['main_bib_file']).expanduser()
            if not output_path.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.touch()
            bib_path = str(output_path.resolve())
            try:
                self._save_via_bibdesk(entry, bib_path)
                return True
            except RuntimeError as e:
                self._log(f"   ⚠️  BibDesk import failed: {e}", 'warning')
                return False

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
        """Move a processed PDF to the output folder."""
        pdf_path = Path(pdf_path)
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
        Process a list of PDFs (or all PDFs in the input folder when pdf_files is None).

        Args:
            move_files: If True, move processed files to output folder
            pdf_files: Explicit list of Path objects; falls back to pdf_in_folder if None
            progress_window: Optional ProgressWindow instance for set_progress() calls

        Returns:
            dict: Summary with 'success', 'failed', 'skipped' lists
        """
        if pdf_files is not None:
            files = [Path(p) for p in pdf_files]
        else:
            in_folder = Path(self.config.get('pdf_in_folder', './pdf-in'))
            if not in_folder.exists():
                self._log(f"Error: Input folder not found: {in_folder}", 'error')
                self._log(f"Create it with: mkdir {in_folder}", 'error')
                return {'success': [], 'failed': [], 'skipped': []}
            files = sorted(in_folder.glob('*.pdf'))

        if not files:
            self._log("No PDF files found.", 'warning')
            return {'success': [], 'failed': [], 'skipped': []}

        total = len(files)
        self._log(f"\n📚 Processing {total} PDF(s)\n", 'info')
        self.notify_progress(f"Processing {total} file{'s' if total != 1 else ''}…")

        if self.config.get('autofile_bibdesk', False):
            self._save_bibdesk_document()

        results = {'success': [], 'failed': [], 'skipped': []}

        for i, pdf_path in enumerate(files, 1):
            if progress_window and progress_window.cancelled:
                self._log("Cancelled by user.", 'warning')
                break

            if progress_window:
                progress_window.set_progress(i, pdf_path.name)

            self.notify_progress(f"[{i}/{total}] {pdf_path.name}", subtitle="Extracting bibliography")

            # Extract bibliography
            bibtex_entry = self.extract_bibtex(pdf_path, batch_info=(i, total))

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
    return config.get('show_window', False)


def _run_windowed(agent, pdf_files, move_files):
    """Run processing with a native floating window on the main thread."""
    import threading
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from progress_window import ProgressWindow

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    win = ProgressWindow(total_files=len(pdf_files))
    agent._progress_callback = win.make_callback()
    win.show()

    def _process():
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
        description="Extract bibliographic data from PDFs using Claude"
    )
    parser.add_argument(
        'pdf_files',
        nargs='*',
        help='Path(s) to PDF file(s) to process'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Process all PDFs in the input folder (pdf-in/)'
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
    if not args.all and not args.pdf_files:
        parser.error("Either provide PDF file(s) or use --all to process the input folder")

    try:
        # Initialize agent
        agent = BiblioAgent(args.config)

        # Override config options
        if args.output:
            agent.config['main_bib_file'] = args.output
        if args.quiet:
            agent.config['verbose'] = False

        show_window = _resolve_show_window(args, agent.config)

        # ── batch mode (--all) ────────────────────────────────────────────────
        if args.all:
            if show_window:
                in_folder = Path(agent.config.get('pdf_in_folder', './pdf-in'))
                pdf_files = sorted(in_folder.glob('*.pdf'))
                _run_windowed(agent, pdf_files, move_files=not args.no_move)
            else:
                results = agent.process_batch(move_files=not args.no_move)
                if results['failed']:
                    sys.exit(1)
            return

        # ── explicit file list ────────────────────────────────────────────────
        pdf_files = [Path(f) for f in args.pdf_files]

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
