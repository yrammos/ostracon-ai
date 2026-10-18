#!/usr/bin/env python3
"""
Self-test for api_keys.py: which source an API key comes from, and what a
failing key command is allowed to say.

The commands are this interpreter running a one-line script, so the test
needs no helper on PATH and touches no keychain. Never reads config.yaml,
no network, no API call.

    python3 dev/test_api_keys.py
"""

from __future__ import annotations

import os
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import api_keys  # noqa: E402

FAKE = "sk-ant-test-0000"


def _printing(text, stderr="", code=0):
    """A key command printing text, as a string for *_api_key_command."""
    script = (f"import sys; sys.stdout.write({text!r}); "
              f"sys.stderr.write({stderr!r}); sys.exit({code})")
    return shlex.join([sys.executable, "-c", script])


def _raises(config, environ=None):
    try:
        api_keys.resolve_api_keys(config, environ or {})
    except ValueError as e:
        return str(e)
    raise AssertionError("no ValueError raised")


def test_plaintext_wins_over_env_and_command():
    config = {"anthropic_api_key": FAKE,
              "anthropic_api_key_command": _printing("from-command\n")}
    api_keys.resolve_api_keys(config, {"ANTHROPIC_API_KEY": "from-env"})
    assert config["anthropic_api_key"] == FAKE
    return True


def test_placeholder_counts_as_unset():
    for placeholder in ("YOUR_API_KEY_HERE", "sk-ant-api03-REPLACE_WITH_YOUR_KEY"):
        config = {"anthropic_api_key": placeholder}
        api_keys.resolve_api_keys(config, {"ANTHROPIC_API_KEY": "from-env"})
        assert config["anthropic_api_key"] == "from-env", placeholder
    return True


def test_env_wins_over_command():
    config = {"anthropic_api_key_command": _printing("from-command\n")}
    api_keys.resolve_api_keys(config, {"ANTHROPIC_API_KEY": "from-env"})
    assert config["anthropic_api_key"] == "from-env"
    return True


def test_env_is_not_read_for_scrapingdog():
    config = {}
    api_keys.resolve_api_keys(config, {"SCRAPINGDOG_API_KEY": "x", "ANTHROPIC_API_KEY": "y"})
    assert config["scrapingdog_api_key"] is None
    return True


def test_command_supplies_first_line_stripped():
    config = {"anthropic_api_key_command": _printing(f"  {FAKE}  \nsecond line\n"),
              "scrapingdog_api_key_command": _printing("dog-key\n")}
    api_keys.resolve_api_keys(config, {})
    assert config["anthropic_api_key"] == FAKE
    assert config["scrapingdog_api_key"] == "dog-key"
    return True


def test_nothing_configured_leaves_keys_unset():
    config = {"scrapingdog_api_key": ""}
    api_keys.resolve_api_keys(config, {})
    assert config["anthropic_api_key"] is None
    assert config["scrapingdog_api_key"] is None
    return True


def test_failure_names_command_and_stderr_but_not_stdout():
    # Stdout is assembled at run time, so it cannot leak via the command text.
    script = ("import sys; sys.stdout.write('LEAK' * 2); "
              "sys.stderr.write('item not found\\nmore detail\\n'); sys.exit(44)")
    command = shlex.join([sys.executable, "-c", script])
    msg = _raises({"anthropic_api_key_command": command})
    assert "LEAKLEAK" not in msg, msg
    assert "anthropic_api_key_command" in msg and command in msg, msg
    reason = msg.rsplit("failed: ", 1)[1]
    assert reason == "exit 44: item not found", msg
    return True


def test_empty_output_fails():
    msg = _raises({"anthropic_api_key_command": _printing("\n", stderr="locked\n")})
    assert "printed nothing (locked)" in msg, msg
    return True


def test_missing_executable_fails():
    msg = _raises({"scrapingdog_api_key_command": "/nonexistent/secret dog"})
    assert "scrapingdog_api_key_command" in msg and "not found" in msg, msg
    return True


def test_timeout_fails():
    saved = api_keys.COMMAND_TIMEOUT
    api_keys.COMMAND_TIMEOUT = 0.5
    try:
        script = "import time; time.sleep(5)"
        msg = _raises({"anthropic_api_key_command": shlex.join([sys.executable, "-c", script])})
    finally:
        api_keys.COMMAND_TIMEOUT = saved
    assert "no answer within" in msg, msg
    return True


def test_tilde_in_first_word_is_expanded():
    with tempfile.TemporaryDirectory() as home:
        helper = Path(home) / "bin" / "helper"
        helper.parent.mkdir()
        helper.write_text(f"#!{sys.executable}\nimport sys; print('key-for-' + sys.argv[1])\n")
        helper.chmod(0o755)
        saved = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            config = {"anthropic_api_key_command": "~/bin/helper ~/name"}
            api_keys.resolve_api_keys(config, {})
        finally:
            os.environ["HOME"] = saved
    # Only the program is expanded; arguments reach it verbatim, as no shell runs.
    assert config["anthropic_api_key"] == "key-for-~/name", config["anthropic_api_key"]
    return True


TESTS = [
    test_plaintext_wins_over_env_and_command,
    test_placeholder_counts_as_unset,
    test_env_wins_over_command,
    test_env_is_not_read_for_scrapingdog,
    test_command_supplies_first_line_stripped,
    test_nothing_configured_leaves_keys_unset,
    test_failure_names_command_and_stderr_but_not_stdout,
    test_empty_output_fails,
    test_missing_executable_fails,
    test_timeout_fails,
    test_tilde_in_first_word_is_expanded,
]


def main():
    failures = []
    for test in TESTS:
        try:
            assert test() is True
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failures.append((test.__name__, e))
            print(f"  ✗ {test.__name__}: {e}")

    print()
    if failures:
        print(f"{len(failures)}/{len(TESTS)} test(s) failed.")
        return 1
    print(f"All {len(TESTS)} api_keys self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
