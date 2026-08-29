#!/usr/bin/env python3
"""
Test script to verify the bibliographic extractor setup.
"""

import re
import subprocess
import sys
from pathlib import Path

# This script lives in dev/, so the repository root is one level up.
ROOT = Path(__file__).resolve().parent.parent

def test_imports():
    """Test if all required packages are installed.

    Table-driven and covering every line of requirements.txt: checking only a
    subset let a setup pass and then fail on the first .webloc source, since
    requests and beautifulsoup4 went unverified.
    """
    print("Testing Python package imports...")

    required = [
        ("anthropic",  "anthropic",             "Claude API client"),
        ("pypdf",      "pypdf",                 "PDF text extraction"),
        ("yaml",       "pyyaml",                "configuration"),
        ("requests",   "requests",              "fetching .webloc pages"),
        ("bs4",        "beautifulsoup4",        "parsing fetched pages"),
        ("AppKit",     "pyobjc-framework-Cocoa", "progress window, BibDesk bookmarks"),
    ]

    ok = True
    for module, package, purpose in required:
        try:
            __import__(module)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  ✗ {package} ({purpose}) - Run: pip install {package}")
            ok = False
    return ok


def test_config():
    """Test if config file exists and is valid."""
    print("\nTesting configuration...")
    
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        print("  ✗ config.yaml not found")
        print("    Create it from the template and add your API key")
        return False
    
    print("  ✓ config.yaml exists")
    
    try:
        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        api_key = config.get('anthropic_api_key', '')
        if not api_key or api_key == 'YOUR_API_KEY_HERE':
            print("  ✗ API key not set in config.yaml")
            print("    Get your key at: https://console.anthropic.com/settings/keys")
            return False
        
        if not api_key.startswith('sk-ant-'):
            print("  ⚠  API key format looks unusual (should start with 'sk-ant-')")
        else:
            print("  ✓ API key configured")
        
    except Exception as e:
        print(f"  ✗ Error reading config: {e}")
        return False
    
    return True

def test_project_files():
    """Test if required project files exist."""
    print("\nTesting project files...")
    
    files = {
        'CLAUDE.md': 'Project guidelines',
        'prompt-context/biblio-template.bib': 'Reference template',
        'prompt-context/biblatex-chicago-notes-ref.md': 'Field reference',
        'prompt-context/notes-test.bib': 'Annotated test suite (example_files)',
        'prompt-context/cms-notes-intro-guide.md': 'Entry-type guide (example_files)',
    }

    all_present = True
    for filename, description in files.items():
        path = ROOT / filename
        if path.exists():
            print(f"  ✓ {filename} ({description})")
        else:
            print(f"  ⚠  {filename} not found ({description})")
            all_present = False

    return all_present

def test_ocr():
    """Test if OCR is available."""
    print("\nTesting OCR capability...")
    
    import shutil
    if shutil.which("ocrmypdf"):
        print("  ✓ ocrmypdf installed (can handle scanned PDFs)")
        return True
    else:
        print("  ⚠  ocrmypdf not found (scanned PDFs won't be processed)")
        print("    Install with: brew install ocrmypdf")
        return False

def test_pdf_folder():
    """Test if the batch input folder exists."""
    print("\nTesting input folder...")

    pdf_path = ROOT / "pdf-in"
    if pdf_path.is_dir():
        n = len(list(pdf_path.glob("*.pdf"))) + len(list(pdf_path.glob("*.webloc")))
        print(f"  ✓ pdf-in/ exists ({n} source file(s) waiting)")
        return True
    print("  ⚠  pdf-in/ not found")
    print("    Create it with: mkdir pdf-in")
    return False


def _documented(readme, token):
    """A token counts as documented if the README names it anywhere."""
    return f"`{token}`" in readme or f"\n{token}: " in readme or token in readme


def _names(readme, token):
    """Whether the README names `token` as a token, not as a substring.

    A plain `token in readme` test passes on any longer name that happens to
    contain it - a stray `XXweb_source.py` in the structure tree counted as
    documenting `web_source.py`, so the audit reported a clean run against a
    README that named no such file. A leading path segment must still count,
    though, since the README writes `dev/test_setup.py` in prose and
    `test_setup.py` in the tree: `/` is a boundary, word characters and
    hyphens are not.
    """
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", readme) is not None


def test_documentation():
    """Check the docs still cover every config key, CLI flag, file and directory.

    These drift silently: a setting added to config.yaml.example or a flag
    added to argparse stays undocumented until someone happens to notice, and
    the README reads as complete the whole time. Cheap to check, so check.

    Both README.md and NOTES.md count. The question this asks has always been
    "is it documented in this repository", not "is it in that one file"; the
    single-file read was incidental, and became wrong once the two were split
    by audience -- what a reader needs in order to act stayed in the README,
    the reasoning and the developer tooling moved across. NOTES.md is optional
    so that a repository without one still passes.
    """
    print("\nTesting documentation coverage...")

    readme_path = ROOT / "README.md"
    if not readme_path.exists():
        print("  ⚠  README.md not found")
        return False
    readme = readme_path.read_text(encoding="utf-8")
    notes_path = ROOT / "NOTES.md"
    if notes_path.exists():
        readme += "\n" + notes_path.read_text(encoding="utf-8")
    ok = True

    # 1. Every config key in the template.
    example = ROOT / "config.yaml.example"
    if example.exists():
        keys = sorted(set(re.findall(r"(?m)^([a-z_]+):", example.read_text(encoding="utf-8"))))
        missing = [k for k in keys if not _documented(readme, k)]
        if missing:
            print(f"  ✗ config keys not in README: {', '.join(missing)}")
            ok = False
        else:
            print(f"  ✓ all {len(keys)} config keys documented")

    # 2. Every CLI flag argparse defines. Read from source rather than by
    #    importing, so a missing dependency doesn't turn this into an error.
    agent = ROOT / "src" / "biblio_agent.py"
    if agent.exists():
        src = agent.read_text(encoding="utf-8")
        flags = sorted({f for call in re.findall(r"add_argument\((.*?)\)", src, re.S)
                          for f in re.findall(r"'(--[a-z][a-z-]*)'", call)})
        missing = [f for f in flags if not _names(readme, f)]
        if missing:
            print(f"  ✗ CLI flags not in README: {', '.join(missing)}")
            ok = False
        else:
            print(f"  ✓ all {len(flags)} CLI flags documented")

    # 3. Every tracked file, wherever it sits, plus every directory holding
    #    one. Checking only the repository root would be vacuous now that the
    #    tree is a hierarchy: three files remain at the top level, so a
    #    root-only audit would pass while src/, dev/ and prompt-context/ went
    #    entirely unchecked. Skipped outside a checkout.
    try:
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                                 capture_output=True, text=True, timeout=10, check=True).stdout.split()
    except (subprocess.SubprocessError, FileNotFoundError):
        print("  ⚠  git unavailable - skipped the file listing check")
        return ok

    # Boilerplate documents itself; images are referenced by markup, not named
    # in the structure tree.
    boilerplate = {"LICENSE", "README.md", ".gitignore"}
    paths = [Path(f) for f in tracked
             if Path(f).name not in boilerplate and Path(f).suffix != ".png"]

    # Directories whose contents are dated run artifacts rather than code:
    # every full evaluation run adds a `<date>.md` here, and naming each one
    # in the tree would mean a README edit per run and a failing check for
    # whoever forgets. Documenting the directory documents its contents - but
    # the directory itself must still be named, so the tree cannot stay silent
    # about it either.
    dated_dirs = {"dev/eval/baselines"}
    paths = [p for p in paths
             if str(p.parent) not in dated_dirs or not _names(readme, f"{p.parent.name}/")]

    # The tree names files by basename under a directory node, so match on the
    # basename and check the directory separately.
    missing = sorted({p.name for p in paths if not _names(readme, p.name)})
    if missing:
        print(f"  ✗ files not in Project Structure: {', '.join(missing)}")
        ok = False
    else:
        print(f"  ✓ all {len(paths)} tracked files listed")

    dirs = sorted({p.parts[0] for p in paths if len(p.parts) > 1})
    missing_dirs = [d for d in dirs if not _names(readme, f"{d}/")]
    if missing_dirs:
        print(f"  ✗ directories not in Project Structure: {', '.join(missing_dirs)}")
        ok = False
    else:
        print(f"  ✓ all {len(dirs)} source directories listed")

    return ok

def preflight():
    """Fast pre-batch check: only what determines whether a run can work.

    Silent on success so the Quick Action stays quiet, and non-zero with a
    short human-readable reason on failure - a missing dependency otherwise
    surfaces as a Python traceback in an alert box, after the progress window
    has already opened. Deliberately skips OCR (a warning, not a blocker),
    pdf-in/ (irrelevant when files are named explicitly), and the
    documentation audit (a developer concern, and it shells out to git).
    """
    import io
    import contextlib

    problems = []
    for name, check in (("dependencies", test_imports), ("configuration", test_config),
                        ("context files", test_project_files)):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            passed = check()
        if not passed:
            detail = [l.strip() for l in buf.getvalue().splitlines()
                      if l.strip().startswith(("✗", "⚠"))]
            problems.append(f"{name}:\n  " + "\n  ".join(detail))

    if problems:
        print("Setup check failed - the batch was not started.\n")
        print("\n\n".join(problems))
        print("\nRun dev/test_setup.py for the full report.")
        return 1
    return 0


def main():
    print("=" * 60)
    print("Bibliographic Extractor - Setup Test")
    print("=" * 60)
    
    results = {
        'imports': test_imports(),
        'config': test_config(),
        'project_files': test_project_files(),
        'ocr': test_ocr(),
        'pdf_folder': test_pdf_folder(),
        'documentation': test_documentation(),
    }
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    critical = ['imports', 'config']
    critical_passed = all(results[k] for k in critical)
    
    if critical_passed:
        print("✓ Core requirements met - ready to process PDFs!")
        print("\nTry: python3 src/biblio_agent.py path/to/your-file.pdf")
    else:
        print("✗ Some critical requirements missing")
        print("\nPlease fix the errors above before running the agent.")
    
    optional = ['project_files', 'ocr', 'pdf_folder', 'documentation']
    if not all(results[k] for k in optional):
        print("\n⚠  Optional components missing:")
        if not results['project_files']:
            print("   - Restore the missing context files (see above)")
        if not results['ocr']:
            print("   - Install ocrmypdf to handle scanned PDFs")
        if not results['pdf_folder']:
            print("   - Create pdf-in/ and drop sources there for --all")
        if not results['documentation']:
            print("   - README.md has drifted from the code (see above)")
    
    print()

if __name__ == "__main__":
    if "--preflight" in sys.argv:
        sys.exit(preflight())
    main()
