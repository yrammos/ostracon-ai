#!/usr/bin/env python3
"""Build and install the Ostracon AI Quick Action to ~/Library/Services/."""

import plistlib
import shutil
import subprocess
import uuid
from pathlib import Path

SERVICE_NAME = "Extract BibLaTeX-Chicago Bibliography (via Claude)"
WORKFLOW_NAME = f"{SERVICE_NAME}.workflow"
SERVICES_DIR = Path.home() / "Library" / "Services"
SCRIPT_PATH = Path(__file__).parent / "automator" / "script.sh"


def build_info_plist() -> dict:
    return {
        "NSServices": [
            {
                "NSMenuItem": {"default": SERVICE_NAME},
                "NSMessage": "runWorkflowAsService",
                "NSRequiredContext": {},
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
            "applicationBundleIDsByPath": {},
            "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 11,
            "processesInput": False,
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False,
            "shortcutMenu": {},
            "targetApplicationBundleIDs": [],
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


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
        print("  Open Automator manually, open the workflow, and save it.")
    else:
        print("✓ Registered via Automator — right-click a PDF or .webloc file in Finder to use it")


if __name__ == "__main__":
    main()
