#!/usr/bin/env python3
"""
Test script to verify the bibliographic extractor setup.
"""

import sys
from pathlib import Path

def test_imports():
    """Test if all required packages are installed."""
    print("Testing Python package imports...")
    
    try:
        import anthropic
        print("  ✓ anthropic")
    except ImportError:
        print("  ✗ anthropic - Run: pip install anthropic")
        return False
    
    try:
        import pypdf
        print("  ✓ pypdf")
    except ImportError:
        print("  ✗ pypdf - Run: pip install pypdf")
        return False
    
    try:
        import yaml
        print("  ✓ pyyaml")
    except ImportError:
        print("  ✗ pyyaml - Run: pip install pyyaml")
        return False
    
    return True

def test_config():
    """Test if config file exists and is valid."""
    print("\nTesting configuration...")
    
    config_path = Path("config.yaml")
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
        'biblio-template.bib': 'Reference template',
        'biblatex-chicago-notes-ref.md': 'Field reference',
        'notes-test.bib': 'Annotated test suite (example_files)',
        'cms-notes-intro-guide.md': 'Entry-type guide (example_files)',
    }

    # Resolve against the repository, not the working directory, so this check
    # agrees with how biblio_agent.py itself resolves config paths.
    root = Path(__file__).resolve().parent

    all_present = True
    for filename, description in files.items():
        path = root / filename
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
    """Test if PDF folder exists."""
    print("\nTesting PDF folder...")
    
    pdf_path = Path("pdf")
    if pdf_path.exists() and pdf_path.is_dir():
        pdf_count = len(list(pdf_path.glob("*.pdf")))
        print(f"  ✓ pdf/ folder exists ({pdf_count} PDFs found)")
        return True
    else:
        print("  ⚠  pdf/ folder not found")
        print("    Create it with: mkdir pdf")
        return False

def main():
    print("=" * 60)
    print("Bibliographic Extractor - Setup Test")
    print("=" * 60)
    
    results = {
        'imports': test_imports(),
        'config': test_config(),
        'project_files': test_project_files(),
        'ocr': test_ocr(),
        'pdf_folder': test_pdf_folder()
    }
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    critical = ['imports', 'config']
    critical_passed = all(results[k] for k in critical)
    
    if critical_passed:
        print("✓ Core requirements met - ready to process PDFs!")
        print("\nTry: python biblio_agent.py pdf/your-file.pdf")
    else:
        print("✗ Some critical requirements missing")
        print("\nPlease fix the errors above before running the agent.")
    
    optional = ['project_files', 'ocr', 'pdf_folder']
    if not all(results[k] for k in optional):
        print("\n⚠  Optional components missing:")
        if not results['project_files']:
            print("   - Add CLAUDE.md and biblio-template.bib for better results")
        if not results['ocr']:
            print("   - Install ocrmypdf to handle scanned PDFs")
        if not results['pdf_folder']:
            print("   - Create pdf/ folder and add your PDFs")
    
    print()

if __name__ == "__main__":
    main()
