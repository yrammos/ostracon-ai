"""
API keys from config.yaml, its environment, or a command that prints them.

Shared by biblio_agent.load_config() and dev/test_setup.py, so the pre-flight
check resolves a key exactly as the run will. Standard library only: the
pre-flight imports this before it knows whether anthropic is installed.
"""

import os
import shlex
import subprocess

COMMAND_TIMEOUT = 10  # seconds


def _is_set(value):
    """A plaintext key counts unless empty or still the template's placeholder."""
    return bool(value) and value != 'YOUR_API_KEY_HERE' and 'REPLACE' not in str(value)


def key_from_command(command, setting):
    """Run command without a shell and return the first line of its stdout.

    The first word is tilde-expanded, since the Quick Action's PATH is
    minimal and users will write ~/.local/bin/... rather than rely on it.
    Failures raise ValueError naming the setting, the command and the first
    line of stderr. Stdout never appears in a message: on a nonzero exit it
    may still hold the key.
    """
    argv = shlex.split(command)
    if not argv:
        raise ValueError(f"{setting} is empty")
    argv[0] = os.path.expanduser(argv[0])

    def fail(reason):
        return ValueError(f"{setting} `{command}` failed: {reason}")

    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=COMMAND_TIMEOUT)
    except FileNotFoundError:
        raise fail(f"{argv[0]} not found") from None
    except PermissionError:
        raise fail(f"{argv[0]} is not executable") from None
    except subprocess.TimeoutExpired:
        raise fail(f"no answer within {COMMAND_TIMEOUT}s") from None

    stderr = next((l.strip() for l in result.stderr.splitlines() if l.strip()), "")
    if result.returncode != 0:
        raise fail(f"exit {result.returncode}" + (f": {stderr}" if stderr else ""))
    lines = result.stdout.splitlines()
    key = lines[0].strip() if lines else ""
    if not key:
        raise fail("printed nothing" + (f" ({stderr})" if stderr else ""))
    return key


def resolve_api_keys(config, environ=None):
    """Settle anthropic_api_key and scrapingdog_api_key in place, once.

    Each comes from the first of: its plaintext field; for Anthropic only,
    $ANTHROPIC_API_KEY; its *_command. An Anthropic key found nowhere is
    left unset for the caller to report; ScrapingDog is optional anyway.
    """
    environ = os.environ if environ is None else environ
    for name, env_var in (('anthropic_api_key', 'ANTHROPIC_API_KEY'),
                          ('scrapingdog_api_key', None)):
        if _is_set(config.get(name)):
            continue
        command = config.get(f'{name}_command')
        if env_var and environ.get(env_var):
            config[name] = environ[env_var]
        elif command:
            config[name] = key_from_command(command, f'{name}_command')
        else:
            config[name] = None
    return config
