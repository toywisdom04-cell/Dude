"""Phase 18: Human Semantic Memory + Jobs.

A bounded, session-independent model of WHO DUDE works for, built on
the EXISTING stores only:

- semantic facts: the existing `facts` table, extended additively with
  subject / confidence / source / status (superseded) columns
- jobs: the existing job-card index, now structurally bounded (never
  JSON-sliced), with evidence-driven confidence

Nothing new is invented as a store, nothing executes, and no inference
happens here: callers state facts with a subject, DUDE keeps the
current one, remembers the old one as history, and recalls the current
set with provenance and confidence. The existing Memory, job_memory,
associations and procedure layers remain the only memory systems.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Human-facing categories that make up the working profile.
PROFILE_CATEGORIES = (
    ("identity", ("CORE_IDENTITY", "personal")),
    ("preferences", ("preferences",)),
    ("work", ("work", "workflow", "knowledge")),
    ("context", ("insight", "screen_learning", "workflow_candidate")),
)

MAX_PROFILE_FACTS = 4
MAX_PROFILE_JOBS = 3


def remember_about_human(memory, fact: str, category: str = "personal",
                         subject: str = "", confidence: float = 0.7,
                         source: str = "user_stated") -> bool:
    """Store one statement about the human with provenance + confidence.

    If `subject` matches an existing active statement, the old one is
    superseded (kept as history) so DUDE stops answering with a belief
    the human has moved on from.
    """
    try:
        if memory is None:
            return False
        return bool(memory.remember_semantic(
            fact, category=category, subject=subject,
            confidence=confidence, source=source, replaces=True))
    except Exception as e:
        log.warning(f"remember_about_human failed: {e}")
        return False


def recall_about_human(memory, query: str = "", limit: int = 8,
                       include_superseded: bool = False) -> List[Dict]:
    """Current, confidence-ranked statements about the human."""
    try:
        if memory is None:
            return []
        return memory.recall_semantic(
            query=query, limit=limit,
            include_superseded=include_superseded)
    except Exception as e:
        log.warning(f"recall_about_human failed: {e}")
        return []


def fact_history(memory, subject: str, limit: int = 5) -> List[Dict]:
    try:
        if memory is None:
            return []
        return memory.fact_history(subject, limit=limit)
    except Exception:
        return []


def build_profile(memory, limit_per_section: int = MAX_PROFILE_FACTS,
                  job_limit: int = MAX_PROFILE_JOBS) -> Dict[str, Any]:
    """Assemble the bounded human profile (cached in existing state).

    Sections: identity, preferences, work, learned context, and the
    user's active jobs with their evidence-based confidence. Every item
    keeps its provenance; nothing is invented and nothing is unbounded.
    """
    profile: Dict[str, Any] = {
        "built_at": time.time(),
        "sections": {},
        "jobs": [],
        "counts": {},
    }
    if memory is None:
        return profile
    try:
        for section, cats in PROFILE_CATEGORIES:
            items: List[Dict] = []
            seen = set()
            for cat in cats:
                try:
                    rows = memory.recall_semantic(
                        category=cat, limit=limit_per_section * 2)
                except Exception:
                    rows = []
                for r in rows:
                    key = str(r.get("fact", "")).strip().lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append(r)
                    if len(items) >= limit_per_section:
                        break
                if len(items) >= limit_per_section:
                    break
            items.sort(key=lambda r: (-float(r.get("confidence") or 0.0),
                                      str(r.get("ts", ""))),
                       reverse=False)
            profile["sections"][section] = items
            profile["counts"][section] = len(items)
    except Exception as e:
        log.warning(f"profile sections failed: {e}")
    try:
        from core import job_memory as jm
        jobs = jm._load(memory)  # bounded index already
        ranked = sorted(
            jobs.items(),
            key=lambda kv: (float(kv[1].get("confidence") or 0.0),
                            str(kv[1].get("last_success", "") or "")),
            reverse=True)
        for name, card in ranked[:job_limit]:
            try:
                profile["jobs"].append({
                    "name": str(name)[:120],
                    "purpose": str(card.get("purpose", ""))[:200],
                    "confidence": round(float(
                        card.get("confidence") or 0.0), 3),
                    "last_success": str(card.get("last_success", ""))[:40],
                })
            except Exception:
                continue
    except Exception as e:
        log.warning(f"profile jobs failed: {e}")
    try:
        memory.set_state("human_profile", _dump(profile))
    except Exception:
        pass
    return profile


def cached_profile(memory) -> Dict[str, Any]:
    """Last built profile (no rebuild cost on the hot path)."""
    try:
        raw = memory.get_state("human_profile", "") if memory else ""
        if not raw:
            return {}
        import json
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def profile_summary(profile: Dict[str, Any], max_chars: int = 700) -> str:
    """Compact human-readable block for context injection (observability
    and reasoning only; never a user-facing canned reply)."""
    try:
        lines = []
        secs = (profile or {}).get("sections") or {}
        for section in ("identity", "preferences", "work", "context"):
            items = secs.get(section) or []
            if not items:
                continue
            facts = "; ".join(
                str(i.get("fact", ""))[:90] for i in items[:3])
            lines.append(f"{section.upper()}: {facts}")
        jobs = (profile or {}).get("jobs") or []
        if jobs:
            lines.append("JOBS: " + ", ".join(
                f"{j.get('name','')} (conf {j.get('confidence', 0)})"
                for j in jobs[:3]))
        return "\n".join(lines)[:max_chars]
    except Exception:
        return ""


def _dump(profile: Dict[str, Any]) -> str:
    import json
    try:
        return json.dumps(profile)[:8000]
    except Exception:
        return "{}"
