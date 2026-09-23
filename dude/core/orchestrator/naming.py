"""Windows naming constraints for DUDE Orchestrator (Phase 5, P10/P11).

Single source of truth for what makes a legal filename vs directory,
used wherever a plan turns words into file destinations. The point is
execution robustness, not planner cleverness:

- a destination directory and a filename are different things; a bare
  name must never silently absorb a path, and a path must never be
  typed where only a name belongs;
- illegal names (reserved device names, illegal characters, empty
  names, dot/space tails) are detected BEFORE committing any action,
  so the planner can decline with a reason instead of the executor
  discovering it through a failed dialog.
"""
from __future__ import annotations

import os
import re

# Characters Windows forbids inside a single path component.
ILLEGAL_CHARS = set('<>:"/\\|?*')

# Reserved device names (any extension): CON PRN AUX NUL COM1-9 LPT1-9.
_RESERVED = ({"con", "prn", "aux", "nul"} |
             {f"com{i}" for i in range(1, 10)} |
             {f"lpt{i}" for i in range(1, 10)})


def split_destination(raw: str):
    """Split raw text into (directory or "", filename).

    A trailing component with an illegal separator structure stays whole;
    callers validate the filename part with validate_filename().
    """
    text = (raw or "").strip().strip("\"'")
    if not text:
        return "", ""
    # Normalize forward slashes for splitting only; the original spelling
    # is preserved for the UI to type.
    parts = re.split(r"[\\/]", text)
    if len(parts) == 1:
        return "", parts[0]
    return "\\".join(parts[:-1]), parts[-1]


def validate_filename(name: str):
    """Check a bare filename. Returns (ok, reason).

    ok=False means no GUI save/rename with this name can succeed; the
    caller should decline rather than invent a replacement silently.
    """
    if name is None:
        return False, "missing filename"
    text = name.strip().strip("\"'")
    if not text:
        return False, "empty filename"
    if text != text.strip(" ."):
        return False, "leading/trailing dots or spaces are stripped by Windows"
    bad = sorted({c for c in text if c in ILLEGAL_CHARS})
    if bad:
        return False, "illegal character(s) %s (use a plain name)" % "".join(bad)
    stem = text.split(".")[0]
    if stem.lower() in _RESERVED:
        return False, f"reserved device name {stem!r} (Windows rejects it)"
    if len(text) > 200:
        return False, "name implausibly long for a test artifact"
    return True, ""


def validate_directory(directory: str):
    """Check a destination directory string. Returns (ok, reason)."""
    if not directory or not directory.strip():
        return False, "missing directory"
    text = directory.strip()
    if not os.path.isabs(text):
        return False, "directory must be absolute"
    return True, ""
