"""DUDE work memory: jobs, not screenshots.

A job card captures a repeatable workflow (purpose, context, applications,
data, verified procedure, variations, verification, recovery, provenance).
Stored as ONE JSON blob in existing Memory state ("jobs_index") — no new
store, no schema migration, no screenshot warehouse.

The Brain also keeps its normal episodic/semantic facts untouched; job
cards are the durable procedural layer the cognitive loop retrieves.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

_STATE_KEY = "jobs_index"

_FIELDS = ("purpose", "context", "applications", "data", "procedure",
           "variations", "verification", "recovery", "last_success",
           "confidence", "provenance")


def _load(memory) -> Dict[str, Dict[str, Any]]:
    try:
        raw = memory.get_state(_STATE_KEY, "")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(memory, jobs: Dict[str, Dict[str, Any]]) -> None:
    try:
        memory.set_state(_STATE_KEY, json.dumps(jobs)[:20000])
    except Exception:
        pass


def blank_card(name: str) -> Dict[str, Any]:
    card: Dict[str, Any] = {"name": name}
    for f in _FIELDS:
        card[f] = [] if f in ("applications", "data", "procedure",
                              "variations") else ""
    return card


def upsert_job(memory, name: str, **fields) -> Dict[str, Any]:
    """Create or update a job card. Unknown fields are ignored."""
    name = (name or "").strip()
    if not name:
        raise ValueError("job name required")
    jobs = _load(memory)
    card = jobs.get(name, blank_card(name))
    for k, v in fields.items():
        if k in _FIELDS:
            card[k] = v
    jobs[name] = card
    _save(memory, jobs)
    return card


def get_job(memory, name: str) -> Optional[Dict[str, Any]]:
    return _load(memory).get((name or "").strip())


def find_jobs(memory, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Keyword match over name/purpose/context/applications. The Brain's
    semantic recall stays the fallback for anything fuzzier."""
    q = (query or "").lower()
    toks = [t for t in q.split() if len(t) > 2]
    scored = []
    for name, card in _load(memory).items():
        hay = " ".join([name, str(card.get("purpose", "")),
                        str(card.get("context", "")),
                        " ".join(card.get("applications", []) or [])]).lower()
        score = sum(1 for t in toks if t in hay)
        if score or not toks:
            scored.append((score, name, card))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [c for _, _, c in scored[:max(1, limit)]]


def record_success(memory, name: str, note: str = "") -> Optional[Dict[str, Any]]:
    """Fold a verified outcome back into the card (learn + improve)."""
    card = get_job(memory, name)
    if card is None:
        return None
    card["last_success"] = time.strftime("%Y-%m-%d %H:%M")
    if note:
        vars_ = card.get("variations") or []
        if note not in vars_:
            vars_.append(note[:200])
            card["variations"] = vars_[-8:]
    jobs = _load(memory)
    jobs[card["name"]] = card
    _save(memory, jobs)
    return card


def render_card(card: Dict[str, Any]) -> str:
    """One compact block suitable for prompt injection."""
    lines = [f"JOB: {card.get('name', '')}"]
    if card.get("purpose"):
        lines.append(f"PURPOSE: {card['purpose']}")
    if card.get("context"):
        lines.append(f"CONTEXT: {card['context']}")
    apps = card.get("applications") or []
    if apps:
        lines.append("KNOWN APPLICATIONS: " + ", ".join(apps))
    data = card.get("data") or []
    if data:
        lines.append("KNOWN DATA: " + ", ".join(data))
    proc = card.get("procedure") or []
    if proc:
        lines.append("VERIFIED PROCEDURE:")
        lines.extend(f"  {i + 1}. {s}" for i, s in enumerate(proc[:12]))
    if card.get("variations"):
        lines.append("KNOWN VARIATIONS: " + "; ".join(card["variations"][:4]))
    if card.get("verification"):
        lines.append(f"VERIFICATION: {card['verification']}")
    if card.get("recovery"):
        lines.append(f"RECOVERY: {card['recovery']}")
    if card.get("last_success"):
        lines.append(f"LAST SUCCESS: {card['last_success']}")
    return "\n".join(lines)[:1500]
