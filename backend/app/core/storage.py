"""Filesystem permission helpers for sensitive application data."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def secure_upload_directory(directory: str | Path) -> None:
    """Restrict Windows upload storage to its owner, SYSTEM, and local administrators."""
    if os.name != "nt":
        return

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    principals = ("*S-1-3-4", "*S-1-5-18", "*S-1-5-32-544")

    def run_icacls(*args: str) -> None:
        result = subprocess.run(
            ["icacls.exe", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise OSError(
                f"Could not secure upload directory ACL (icacls exit {result.returncode})"
                + (f": {detail}" if detail else "")
            )

    # Reset legacy explicit entries, remove inherited access on the whole tree, then
    # restore only trusted principals. The final inheritable ACEs protect future uploads.
    run_icacls("/reset", "/T", "/Q")
    run_icacls(
        "/inheritance:r",
        "/grant:r",
        *(f"{sid}:(F)" for sid in principals),
        "/T",
        "/Q",
    )
    run_icacls(
        "/grant",
        *(f"{sid}:(OI)(CI)(F)" for sid in principals),
        "/Q",
    )
