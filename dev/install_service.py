#!/usr/bin/env python3
"""Build and install the Ostracon AI Quick Action to ~/Library/Services/."""

import plistlib
import shutil
import subprocess
import time
import uuid
from pathlib import Path

SERVICE_NAME = "Extract BibLaTeX-Chicago Bibliography (via Claude)"
WORKFLOW_NAME = f"{SERVICE_NAME}.workflow"
SERVICES_DIR = Path.home() / "Library" / "Services"
# This script lives in dev/, so the repository root is one level up.
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "automator" / "script.sh"
FINDER_BUNDLE_ID = "com.apple.finder"
FINDER_PATH = "/System/Library/CoreServices/Finder.app"
PBS = "/System/Library/CoreServices/pbs"


def build_info_plist() -> dict:
    return {
        "NSServices": [
            {
                "NSMenuItem": {"default": SERVICE_NAME},
                "NSMessage": "runWorkflowAsService",
                "NSRequiredContext": {"NSApplicationIdentifier": FINDER_BUNDLE_ID},
                "NSSendFileTypes": ["com.adobe.pdf", "com.apple.web-internet-location"],
            }
        ]
    }


def build_document_wflow(script_content: str) -> dict:
    return {
        "AMApplicationBuild": "521.1",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [
            {
                "action": {
                    "AMAccepts": {
                        "Container": "List",
                        "Optional": True,
                        "Types": ["com.apple.cocoa.path"],
                    },
                    "AMActionVersion": "2.0.3",
                    "AMApplication": ["Finder"],
                    "AMParameterProperties": {
                        "COMMAND_STRING": {},
                        "CheckedForUserDefaultShell": {},
                        "inputMethod": {},
                        "shell": {},
                        "source": {},
                    },
                    "AMProvides": {
                        "Container": "List",
                        "Types": ["com.apple.cocoa.path"],
                    },
                    "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
                    "ActionName": "Run Shell Script",
                    "ActionParameters": {
                        "COMMAND_STRING": script_content,
                        "CheckedForUserDefaultShell": True,
                        "inputMethod": 1,  # 1 = as arguments, 0 = stdin
                        "shell": "/bin/zsh",
                        "source": "",
                    },
                    "BundleIdentifier": "com.apple.RunShellScript",
                    "CFBundleVersion": "2.0.3",
                    "CanShowSelectedItemsWhen": False,
                    "CanShowWhenRun": False,
                    "Category": ["AMCategoryUtilities"],
                    "Class Name": "RunShellScriptAction",
                    "InputUUID": str(uuid.uuid4()).upper(),
                    "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
                    "OutputUUID": str(uuid.uuid4()).upper(),
                    "UUID": str(uuid.uuid4()).upper(),
                    "UnlocalizedApplications": ["Automator"],
                    "arguments": {
                        "0": {
                            "default value": 0,
                            "name": "inputMethod",
                            "required": "0",
                            "type": "0",
                            "uuid": "0",
                        },
                        "1": {
                            "default value": "",
                            "name": "shell",
                            "required": "0",
                            "type": "0",
                            "uuid": "1",
                        },
                        "2": {
                            "default value": "",
                            "name": "COMMAND_STRING",
                            "required": "0",
                            "type": "0",
                            "uuid": "2",
                        },
                    },
                    "isViewVisible": True,
                    "location": "309.5:253.00000000000000",
                    "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/English.lproj/main.nib",
                },
                "isViewVisible": True,
            }
        ],
        "connectors": {},
        "workflowMetaData": {
            # Four things together put a workflow in Finder's Quick Actions
            # submenu rather than Services: scoping to Finder here,
            # presentationMode 15, and NSIconName/NSBackgroundColorName in
            # Info.plist. The workflows on this machine that land in Quick
            # Actions have all four; those in Services have none.
            "applicationBundleID": FINDER_BUNDLE_ID,
            "applicationBundleIDsByPath": {FINDER_PATH: FINDER_BUNDLE_ID},
            "applicationPath": FINDER_PATH,
            "applicationPaths": [FINDER_PATH],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "serviceApplicationBundleID": FINDER_BUNDLE_ID,
            "serviceApplicationPath": FINDER_PATH,
            "processesInput": False,
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False,
            "shortcutMenu": {},
            "targetApplicationBundleIDs": [FINDER_BUNDLE_ID],
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def wait_for_registration(workflow_dir: Path, timeout: float = 15.0) -> bool:
    """Poll pbs until it reports the workflow, so Finder relaunches into a
    complete Services list rather than a half-rebuilt one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dump = subprocess.run([PBS, "-dump_pboard"], capture_output=True, text=True)
        if str(workflow_dir) in dump.stdout:
            return True
        time.sleep(0.5)
    return False


def main():
    script_content = SCRIPT_PATH.read_text()

    workflow_dir = SERVICES_DIR / WORKFLOW_NAME
    contents_dir = workflow_dir / "Contents"

    # Remove existing installation if present
    if workflow_dir.exists():
        shutil.rmtree(workflow_dir)

    contents_dir.mkdir(parents=True)

    with open(contents_dir / "Info.plist", "wb") as f:
        plistlib.dump(build_info_plist(), f)

    with open(contents_dir / "document.wflow", "wb") as f:
        plistlib.dump(build_document_wflow(script_content), f)

    print(f"✓ Installed: {workflow_dir}")

    # Open the workflow in Automator and re-save it so macOS registers it properly
    print("  Opening in Automator to register the action...")
    script = f'''
        tell application "Automator"
            open POSIX file "{workflow_dir}"
            save document 1
            quit
        end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ⚠️  Automator registration failed: {result.stderr.strip()}")
        print("  Open Automator manually, open the workflow and save it,")
        print("  then re-run this script — saving in Automator rewrites Info.plist.")
        return

    # Automator regenerates Info.plist from document.wflow on save, widening
    # NSSendFileTypes to public.item and dropping NSRequiredContext, but also
    # adding the NSIconName/NSBackgroundColorName that mark a Quick Action.
    # Merge our two keys over its version so both survive; replacing the file
    # wholesale costs the icon keys and demotes the item to Services.
    with open(contents_dir / "Info.plist", "rb") as f:
        generated = plistlib.load(f)
    generated["NSServices"][0].update(build_info_plist()["NSServices"][0])
    with open(contents_dir / "Info.plist", "wb") as f:
        plistlib.dump(generated, f)

    flush = subprocess.run([PBS, "-flush"], capture_output=True, text=True)
    if flush.returncode != 0:
        print(f"  ⚠️  Could not refresh the Services cache: {flush.stderr.strip()}")
        print("  Log out and back in, or restart, for Finder to pick up the change.")
        return

    # Finder builds its Services menu once, at launch, by asking pbs. Relaunching
    # it before pbs has finished rebuilding after the flush caches an incomplete
    # menu, and the item is missing until Finder is restarted again. Wait for pbs
    # to report the workflow before relaunching.
    if not wait_for_registration(workflow_dir):
        print("  ⚠️  The Services cache did not list the workflow within 15s.")
        print(f"  Run: killall Finder    (or re-run {Path(__file__).name})")
        return

    # A plain SIGTERM makes Finder relaunch and rebuild its menus; -HUP does not.
    subprocess.run(["killall", "Finder"], capture_output=True)
    print("✓ Registered — right-click a PDF or .webloc file in Finder, under Quick Actions")


if __name__ == "__main__":
    main()
