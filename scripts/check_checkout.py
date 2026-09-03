#!/usr/bin/env python3
"""Verify that this checkout matches the byte-exact files the data build expects.

`hydromind data rebuild` refuses to start when data/glasgow-dtm-edge-patch.csv
disagrees with the SHA-256 pinned in data/glasgow-5m-sources.json. The usual
cause is not a corrupt download but the checkout itself: Git for Windows enables
core.autocrlf by default, which rewrites LF to CRLF and changes the file's bytes.

Run it directly if a data build complains about the source lock:

    python scripts/check_checkout.py

This deliberately duplicates hydromind.reproducible_data._edge_patch_error rather
than importing it: CI runs this before `pip install`, precisely so a corrupt
checkout is reported before it can be mistaken for an installation problem. Keep
the two messages in step; do not merge them.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def fail(message: str) -> None:
    print(f"::error::{message}" if GITHUB_ACTIONS else f"ERROR: {message}")


def main() -> int:
    lock = json.loads(
        (PROJECT_ROOT / "data" / "glasgow-5m-sources.json").read_text(encoding="utf-8")
    )
    patch = lock["legacy_edge_patch"]
    path = PROJECT_ROOT / patch["path"]

    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual == patch["sha256"]:
        print(f"{patch['path']}: {len(raw)} bytes, sha256 matches the source lock.")
        return 0

    if b"\r\n" in raw and hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == patch["sha256"]:
        fail(
            "This checkout rewrote LF to CRLF, so the hash-locked DTM edge patch no "
            "longer matches the source lock. The repository ships a .gitattributes "
            "that prevents this; it is missing, ignored, or the file was written by "
            "another tool. Recover with: git config core.autocrlf false && "
            "git rm --cached -r . && git reset --hard"
        )
    fail(
        f"{patch['path']}: expected sha256 {patch['sha256']} ({patch['bytes']} bytes), "
        f"found {actual} ({len(raw)} bytes)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
