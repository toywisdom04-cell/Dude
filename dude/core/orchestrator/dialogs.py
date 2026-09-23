"""Generic Windows dialog classification for DUDE Orchestrator.

Answers, from a single perception snapshot plus task context:

- is a modal dialog present, and what kind (Save As, overwrite confirm,
  don't-save prompt, error/info message, unknown)?
- was it expected given what DUDE just did?
- what posture is safe (proceed / answer-with-authorization / dismiss / abort)?

Deliberately application-independent: it keys off window class (#32770
for Win32 common dialogs), UIA control types/names, ownership, and the
current task context — never one app's layout. Observed real dialogs
that shaped these patterns: Notepad "Save as" (filename Edit aid 1001,
Save aid 1), "Confirm Save As" (Yes aid CommandButton_6 / No aid
CommandButton_7 + "already exists" text), "Don't save" triple-button
prompts, and invalid-name error popups (OK aid CommandButton_1).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

log = logging.getLogger(__name__)


class DialogKind(Enum):
    """Kinds of modal UI the classifier recognizes."""
    NONE = "none"
    SAVE_AS = "save_as"
    OPEN_FILE = "open_file"
    CONFIRM_OVERWRITE = "confirm_overwrite"
    CONFIRM_GENERIC = "confirm_generic"
    DONT_SAVE = "dont_save"
    ERROR_MESSAGE = "error_message"
    INFO_MESSAGE = "info_message"
    UNKNOWN_MODAL = "unknown_modal"


class DialogPosture(Enum):
    """What the orchestrator may safely do about the dialog."""
    PROCEED = "proceed"    # Expected step in the plan; keep going.
    ANSWER = "answer"      # Known question; needs explicit task authorization.
    DISMISS = "dismiss"    # Safe to dismiss (Esc); nothing is decided.
    ABORT = "abort"        # Unknown or risky; fail safely, touch nothing.


@dataclass
class DialogClassification:
    """Result of classifying the current foreground UI."""
    kind: DialogKind = DialogKind.NONE
    expected: Optional[bool] = None  # None = no task context to judge by
    confidence: float = 0.0
    evidence: str = ""
    posture: DialogPosture = DialogPosture.PROCEED
    # Observed answer controls (name/role pairs), for authorized use only.
    answer_controls: list = field(default_factory=list)


def _button_names(snapshot) -> list:
    return [(c.name or "").strip()
            for c in (snapshot.controls or [])
            if c.ctype == "ButtonControl" and (c.name or "").strip()]


def _has_filename_field(snapshot) -> bool:
    for c in (snapshot.controls or []):
        if c.ctype != "EditControl":
            continue
        name = (c.name or "").lower()
        if "file name" in name or (c.automation_id or "") == "1001":
            return True
    return False


def _texts(snapshot) -> str:
    parts = []
    for c in (snapshot.controls or []):
        if c.ctype in ("TextControl", "DocumentControl") and c.name:
            parts.append(c.name)
    return "\n".join(parts).lower()


def _owner_matches_task(snapshot, task_app: str) -> Optional[bool]:
    """Best-effort: is the foreground dialog owned by the task's app?"""
    if not task_app:
        return None
    active = getattr(snapshot, "active_window", None) or {}
    hwnd = active.get("hwnd")
    if not hwnd:
        return None
    try:
        import win32gui
        import win32process
        import psutil
        owner = win32gui.GetWindow(hwnd, 4)  # GW_OWNER
        if not owner:
            return None
        _, owner_pid = win32process.GetWindowThreadProcessId(owner)
        _, fg_pid = win32process.GetWindowThreadProcessId(hwnd)
        if owner_pid == fg_pid:
            return True
        owner_name = (psutil.Process(owner_pid).name() or "").lower()
        base = task_app.lower()
        if base.endswith(".exe"):
            base = base[:-4]
        return base in owner_name or owner_name.replace(".exe", "") in base
    except Exception:
        return None


def classify_dialog(snapshot, task_context: Optional[dict] = None) -> DialogClassification:
    """Classify the foreground UI in a perception snapshot.

    task_context may carry {"app", "goal", "last_action_type",
    "expecting"} where expecting is one of "save_as", "confirm", "open",
    or None. No context -> expected stays None (observed, not judged).
    """
    task_context = task_context or {}
    active = getattr(snapshot, "active_window", None) or {}
    title = (active.get("title") or "").strip()
    title_low = title.lower()
    wclass = (active.get("wclass") or "")
    buttons = _button_names(snapshot)
    lowered = sorted({b.lower() for b in buttons})
    has_file_field = _has_filename_field(snapshot)
    texts = _texts(snapshot)

    def _expected_for(*kinds: str) -> Optional[bool]:
        expecting = (task_context.get("expecting") or "").lower()
        if not expecting:
            return None
        return expecting in kinds

    # --- Save As: filename field + Save/Cancel, or explicit title/class ---
    if has_file_field and "save" in lowered and "cancel" in lowered:
        return DialogClassification(
            kind=DialogKind.SAVE_AS,
            expected=_expected_for("save_as", "save"),
            confidence=0.9,
            evidence=f"Save As dialog '{title}': filename field + Save/Cancel buttons",
            posture=DialogPosture.PROCEED,
            answer_controls=[("File name:", "EditControl"),
                             ("Save", "ButtonControl")],
        )
    # NOTE: the title-based Save As rule lives below the confirmation
    # checks on purpose: "Confirm Save As" contains "save as" but is an
    # overwrite question, not a file dialog.

    # --- Open file dialog ---
    if has_file_field and "open" in lowered and title_low.startswith("open"):
        return DialogClassification(
            kind=DialogKind.OPEN_FILE,
            expected=_expected_for("open"),
            confidence=0.85,
            evidence=f"Open dialog '{title}': filename field + Open button",
            posture=DialogPosture.PROCEED,
            answer_controls=[("File name:", "EditControl"),
                             ("Open", "ButtonControl")],
        )

    # --- Overwrite confirmation: Yes/No + already-exists wording ---
    if "yes" in lowered and "no" in lowered and "already exists" in texts:
        return DialogClassification(
            kind=DialogKind.CONFIRM_OVERWRITE,
            expected=_expected_for("confirm", "overwrite", "save"),
            confidence=0.9,
            evidence="overwrite confirmation: Yes/No + 'already exists' text",
            posture=DialogPosture.ANSWER,
            answer_controls=[("Yes", "ButtonControl"),
                             ("No", "ButtonControl")],
        )

    # --- Don't-save prompt: Save / Don't save / Cancel triple ---
    if "save" in lowered and "cancel" in lowered and any(
            "on't save" in b for b in lowered):
        return DialogClassification(
            kind=DialogKind.DONT_SAVE,
            expected=_expected_for("dont_save", "close"),
            confidence=0.9,
            evidence="don't-save prompt: Save/Don't save/Cancel buttons",
            posture=DialogPosture.ANSWER,
            answer_controls=[("Save", "ButtonControl"),
                             ("Don't save", "ButtonControl"),
                             ("Cancel", "ButtonControl")],
        )

    # --- Generic Yes/No confirmation ---
    if "yes" in lowered and "no" in lowered:
        return DialogClassification(
            kind=DialogKind.CONFIRM_GENERIC,
            expected=_expected_for("confirm"),
            confidence=0.7,
            evidence=f"Yes/No confirmation dialog '{title}'",
            posture=DialogPosture.ANSWER,
            answer_controls=[("Yes", "ButtonControl"),
                             ("No", "ButtonControl")],
        )

    # --- Title-based Save As fallback (after confirmations: "Confirm
    # Save As" is a question, not a file dialog) ---
    if "save as" in title_low:
        return DialogClassification(
            kind=DialogKind.SAVE_AS,
            expected=_expected_for("save_as", "save"),
            confidence=0.75,
            evidence=f"window titled like Save As: '{title}'",
            posture=DialogPosture.PROCEED,
            answer_controls=[("File name:", "EditControl"),
                             ("Save", "ButtonControl")],
        )

    # --- Single-OK message boxes (error wording may live in the
    # message text rather than the title) ---
    if lowered == ["ok"]:
        haystack = title_low + "\n" + texts
        kind = (DialogKind.ERROR_MESSAGE
                if any(k in haystack for k in ("error", "invalid",
                                               "cannot", "failed",
                                               "not valid"))
                else DialogKind.INFO_MESSAGE)
        return DialogClassification(
            kind=kind,
            expected=_expected_for("acknowledge"),
            confidence=0.7,
            evidence=f"single-OK message box '{title}'",
            posture=DialogPosture.DISMISS,
            answer_controls=[("OK", "ButtonControl")],
        )

    # --- Unknown modal: #32770 with buttons we cannot classify ---
    if wclass == "#32770" and buttons:
        return DialogClassification(
            kind=DialogKind.UNKNOWN_MODAL,
            expected=False if task_context.get("expecting") else None,
            confidence=0.6,
            evidence=f"unrecognized #{wclass} dialog '{title}' "
                     f"with buttons {sorted(set(buttons))[:6]}",
            posture=DialogPosture.ABORT,
        )

    return DialogClassification(
        kind=DialogKind.NONE,
        expected=True if not task_context.get("expecting") else None,
        confidence=0.9 if not task_context.get("expecting") else 0.5,
        evidence=f"no modal dialog: foreground '{title}'",
        posture=DialogPosture.PROCEED,
    )
