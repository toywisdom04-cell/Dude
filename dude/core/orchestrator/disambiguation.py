"""Generic semantic disambiguation for ambiguous same-type controls.

Answers "which one?" without coordinates, AutomationIds, or per-app
recipes. Evidence used, in order:

    exact semantic/name match
    ordinal position among equivalent candidates ("first", "second")
    control type ("field" -> edit, "button" -> button, ...)
    nearby label relationship ("the field under Name")
    parent/container relationship ("in the dialog")
    enabled/visible state, task-window ownership (prefilter)

Reading order (top-to-bottom, left-to-right by rectangle) decides
ordinal position and tiebreaks — NEVER raw UIA tree order, which
follows neither visual order nor creation order and is the classic
source of "first X" resolving to the second control.

Rectangles are used ONLY for reading order and enabled/visible
state. They are never the selection rule and never leave this module
as an action coordinate source (coordinates come from the chosen
control's own rect, as before).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ORDINAL_WORDS = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "sixth": 5, "seventh": 6, "eighth": 7, "ninth": 8, "tenth": 9,
    "last": -1,
}

# UI-kind words -> UIA ControlTypeName fragments (lowercase contains).
TYPE_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "field": ("editcontrol", "documentcontrol"),
    "textfield": ("editcontrol", "documentcontrol"),
    "textbox": ("editcontrol", "documentcontrol"),
    "entry": ("editcontrol",),
    "edit": ("editcontrol",),
    "button": ("buttoncontrol",),
    "checkbox": ("checkboxcontrol",),
    "tab": ("tabitemcontrol",),
    "item": ("listitemcontrol", "treeitemcontrol", "menuitemcontrol"),
    "menu": ("menucontrol", "menubarcontrol", "menuitemcontrol"),
    "dialog": ("windowcontrol", "panecontrol"),
    "editor": ("documentcontrol", "editcontrol"),
    "document": ("documentcontrol",),
    "label": ("textcontrol",),
    "text": ("textcontrol", "editcontrol", "documentcontrol"),
    "control": (),
}

_STOP = {"the", "a", "an", "my", "this", "that"}


@dataclass
class ParsedRef:
    ordinal: Optional[int] = None
    type_names: Tuple[str, ...] = ()
    type_word: str = ""
    label_hint: str = ""
    container_hint: str = ""
    base_words: Tuple[str, ...] = ()


@dataclass
class RankInput:
    """One candidate control, normalized from any source."""
    name: str = ""
    ctype: str = ""
    role: str = ""
    automation_id: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    enabled: bool = True
    visible: bool = True
    offscreen: bool = False
    parent_name: str = ""
    nearby_text: str = ""
    sibling_index: int = -1
    ref: Any = None


def parse_reference(query: str) -> ParsedRef:
    """Split 'second text field under Name' into structured intent."""
    q = (query or "").strip().lower()
    ordinal: Optional[int] = None
    m = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|"
                  r"eighth|ninth|tenth|last)\b", q)
    if m:
        ordinal = ORDINAL_WORDS[m.group(1)]
    else:
        m2 = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", q)
        if m2:
            try:
                ordinal = int(m2.group(1)) - 1
            except Exception:
                ordinal = None
    label_hint = ""
    ml = re.search(r"\b(?:under|below|next\s+to|beside|near|after|for)\s+"
                   r"(.+?)(?:\s+(?:in|of|on)\b|$)", q)
    if ml:
        label_hint = ml.group(1).strip(" .\"'")
    container_hint = ""
    mc = re.search(r"\bin\s+(?:the\s+)?(.+?)\s+"
                   r"(dialog|window|group|groupbox|tab|panel)\b", q)
    if mc:
        container_hint = (mc.group(1) + " " + mc.group(2)).strip()
    # Strip ordinal/label/container/type words -> base name words.
    rest = q
    rest = re.sub(r"\b(first|second|third|fourth|fifth|sixth|seventh|"
                  r"eighth|ninth|tenth|last|\d+(?:st|nd|rd|th))\b", " ", rest)
    if label_hint:
        rest = rest.replace(ml.group(0), " ")
    if container_hint:
        rest = rest.replace(mc.group(0), " ")
    type_names: Tuple[str, ...] = ()
    type_word = ""
    # Longest kind-word first so "text field" wins over "field"/"text".
    for kind in sorted(TYPE_SYNONYMS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(kind) + r"\b", rest):
            type_names = TYPE_SYNONYMS[kind]
            type_word = kind
            rest = re.sub(r"\b" + re.escape(kind) + r"\b", " ", rest)
            break
    base_words = tuple(w for w in re.findall(r"[a-z0-9']+", rest)
                       if w not in _STOP and len(w) > 1)
    return ParsedRef(ordinal=ordinal, type_names=type_names,
                     type_word=type_word, label_hint=label_hint,
                     container_hint=container_hint, base_words=base_words)


# Window furniture is never content: title-bar Minimize/Maximize/Close
# must not consume ordinal positions ("second button" means the second
# content button, not Alpha-after-Minimize). Name matching still finds
# them directly ("click Close" is unaffected).
_CHROME_NAMES = {"minimize", "maximize", "close", "restore", "system"}


def _without_chrome(pool: List[RankInput]) -> List[RankInput]:
    content = [c for c in pool
               if (c.name or "").strip().lower() not in _CHROME_NAMES]
    return content or pool


def reading_order_key(c: RankInput) -> Tuple[int, int]:
    """Top-to-bottom band, then left-to-right. Bands tolerate a few px
    of baseline wobble so same-row controls order by x."""
    return (int(c.y // 24), int(c.x))


def _ctype_of(c: RankInput) -> str:
    return ((c.ctype or "") + " " + (c.role or "")).lower()


def type_matches(c: RankInput, type_names: Tuple[str, ...]) -> bool:
    if not type_names:
        return True
    ct = _ctype_of(c)
    return any(t in ct for t in type_names)


def rank_controls(cands: List[RankInput], query: str
                  ) -> List[Tuple[float, Dict[str, Any], RankInput]]:
    """Score every candidate. Returns (score, evidence, cand) desc."""
    parsed = parse_reference(query)
    out = []
    for c in cands:
        if not c.enabled or not c.visible or c.offscreen:
            continue
        nm = (c.name or "").lower()
        score = 0.0
        ev: Dict[str, Any] = {}
        if parsed.base_words:
            hits = [w for w in parsed.base_words if w in nm]
            if len(hits) == len(parsed.base_words) and nm:
                score += 5.0
                ev["exact_base"] = True
            elif hits:
                score += min(3.0, float(len(hits)))
                ev["word_hits"] = hits
        if parsed.type_names and type_matches(c, parsed.type_names):
            score += 2.0
            ev["type_match"] = parsed.type_word
        if parsed.label_hint:
            hay = ((c.nearby_text or "") + " " +
                   (c.parent_name or "")).lower()
            if parsed.label_hint and parsed.label_hint in hay:
                score += 3.0
                ev["label_match"] = parsed.label_hint
        if parsed.container_hint:
            hay = ((c.parent_name or "") + " " +
                   (c.nearby_text or "")).lower()
            words = [w for w in parsed.container_hint.split() if len(w) > 2]
            if words and all(w in hay for w in words):
                score += 2.0
                ev["container_match"] = parsed.container_hint
        out.append((score, ev, c))
    out.sort(key=lambda t: (-t[0], reading_order_key(t[2])))
    return out


def select_control(cands: List[RankInput], query: str,
                   ) -> Tuple[Optional[RankInput], Dict[str, Any]]:
    """Pick one control or (None, evidence). Ordinal queries select by
    reading order among type-equivalent candidates; otherwise the
    top-scored candidate wins only with a clear margin."""
    parsed = parse_reference(query)
    live = [c for c in cands
            if c.enabled and c.visible and not c.offscreen]
    if not live:
        return None, {"reason": "no enabled visible candidates"}
    if parsed.ordinal is not None:
        pool = [c for c in live
                if type_matches(c, parsed.type_names)] if \
            parsed.type_names else list(live)
        if not pool:
            pool = list(live)
        pool = _without_chrome(pool)
        pool.sort(key=reading_order_key)
        idx = parsed.ordinal if parsed.ordinal >= 0 else len(pool) - 1
        if 0 <= idx < len(pool):
            pick = pool[idx]
            return pick, {"role": pick.ctype or pick.role,
                          "ordinal": parsed.ordinal if parsed.ordinal >= 0
                          else "last",
                          "ordinal_of": len(pool),
                          "parent": pick.parent_name,
                          "label_context": pick.nearby_text[:60],
                          "type_word": parsed.type_word,
                          "method": "ordinal+reading-order"}
        return None, {"reason": "ordinal out of range",
                      "pool_size": len(pool)}
    ranked = rank_controls(live, query)
    if not ranked or ranked[0][0] <= 0:
        return None, {"reason": "no positively scored candidate"}
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 2.0:
        return None, {"reason": "ambiguous",
                      "top_two": [ranked[0][2].name, ranked[1][2].name]}
    score, ev, pick = ranked[0]
    ev = dict(ev)
    ev.update({"role": pick.ctype or pick.role,
               "parent": pick.parent_name,
               "label_context": pick.nearby_text[:60],
               "score": round(score, 1),
               "method": "ranked"})
    return pick, ev
