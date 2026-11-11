"""Perceptual Memory + Screen-Memory Lifecycle (Phase 10).

Thin explicit lifecycle over EXISTING stores — no new databases, no new
retrieval engine, no screenshot warehouse:

    LIVE PERCEPTION   current SemanticObservation / LiveSceneState
    SHORT-TERM        ObservationLog (bounded, TTL, deduped)
    PERSISTENT        Memory facts + KnowledgeBase notes (explicit promote only)
    TRANSIENT         FrameRing (bounded count + TTL, never persisted)

Promotion rule: only TASK_RELEVANT / REUSABLE_CANDIDATE observations with
sufficient confidence become persistent facts. Everything else stays
transient or short-term and expires. Duplicates are suppressed before
writing (plus the facts-table UNIQUE constraint as backstop).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class PerceptualMemoryPolicy:
    """Explicit lifecycle policy (deterministic, bounded, testable)."""
    # Transient visual data
    frame_max: int = 4
    frame_ttl_seconds: float = 60.0
    # Short-term semantic state
    short_term_max: int = 64
    short_term_ttl_seconds: float = 300.0
    # Promotion gate
    persist_classes: tuple = ("task_relevant", "reusable_candidate")
    min_persist_confidence: float = 0.5
    # Compaction: near-duplicate window for persistent writes
    dedupe_lookup_chars: int = 60


@dataclass
class LayerDecision:
    """Where one observation landed and why."""
    layer: str  # live | short_term | persistent | ignored | secret
    reason: str = ""
    persisted: bool = False


class PerceptualMemory:
    """Facade over PerceptionService + Memory + KnowledgeBase."""

    def __init__(self, perception_service, memory,
                 knowledge=None,
                 policy: Optional[PerceptualMemoryPolicy] = None):
        self.service = perception_service
        self.memory = memory
        self._knowledge = knowledge
        self.policy = policy or PerceptualMemoryPolicy()

    def _kb(self):
        if self._knowledge is not None:
            return self._knowledge
        try:
            from core.knowledge import get_knowledge
            self._knowledge = get_knowledge()
            return self._knowledge
        except Exception:
            return None

    def record_observation(self, obs, category: str = "screen_state") -> LayerDecision:
        """Route one observation to exactly one layer."""
        try:
            from .perception_service import ObservationClass
        except Exception:
            ObservationClass = None

        try:
            cls, reason = self.service.classify_observation(obs)
            cls_name = cls.value if hasattr(cls, "value") else str(cls)
        except Exception as e:
            return LayerDecision(layer="ignored", reason=f"classify failed: {e}")

        if cls_name == "secret_sensitive":
            return LayerDecision(layer="secret", reason=reason)

        # Short-term working state (bounded + TTL + dedup inside the log)
        try:
            self.service.observations.push(obs)
        except Exception as e:
            return LayerDecision(layer="ignored", reason=f"short-term push failed: {e}")

        if cls_name not in self.policy.persist_classes:
            return LayerDecision(layer="short_term", reason=reason)

        if (obs.confidence or 0.0) < self.policy.min_persist_confidence:
            return LayerDecision(
                layer="short_term",
                reason=f"{reason}; confidence below persist threshold")

        ok = self.persist(obs, category=category)
        if ok:
            return LayerDecision(layer="persistent", reason=reason, persisted=True)
        return LayerDecision(layer="short_term",
                             reason="duplicate or write skipped; kept short-term only")

    def persist(self, obs, category: str = "screen_state") -> bool:
        """Persist one observation as a semantic fact (dedupe-checked)."""
        try:
            fact = obs.to_fact()
        except Exception:
            return False
        # Compaction at write time: skip near-duplicates already stored.
        try:
            probe = fact[:self.policy.dedupe_lookup_chars]
            for existing in self.memory.recall_facts(probe, limit=4):
                if fact[:70].lower() in str(existing).lower():
                    return False
        except Exception:
            pass
        try:
            tagged = self._tag_provenance(fact, obs)
            written = self.memory.remember_fact(tagged, category=category)
        except Exception:
            return False
        if not written:
            return False  # UNIQUE backstop caught an exact duplicate
        try:
            kb = self._kb()
            if kb is not None:
                today = datetime.now().strftime("%Y-%m-%d")
                kb.append_daily_note(f"perceptual-memory-{today}", [tagged])
        except Exception:
            pass
        return True

    @staticmethod
    def _tag_provenance(fact: str, obs) -> str:
        """Retain observed/action/result/verified provenance in the record."""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        kind = "observed"
        try:
            if (obs.dialog_kind or "none") not in ("none", ""):
                kind = "observed-dialog"
        except Exception:
            pass
        return f"[percept {stamp} kind={kind}] {fact}"

    def sweep(self) -> dict:
        """Expire transient + short-term entries. Returns expiry counts."""
        out = {"frames_expired": 0, "observations_expired": 0,
               "frames_live": 0, "observations_live": 0}
        try:
            out["frames_expired"] = self.service._frames.sweep()
            out["frames_live"] = self.service._frames.live_count()
        except Exception:
            pass
        try:
            out["observations_expired"] = self.service.observations.sweep()
            out["observations_live"] = len(self.service.observations)
        except Exception:
            pass
        return out

    def retrieve_relevant(self, query: str, limit: int = 5,
                          categories: tuple = ("screen_state",
                                               "observation_workflow")) -> list[str]:
        """Retrieve persistent perceptual facts relevant to a query."""
        out: list[str] = []
        try:
            for f in self.memory.recall_facts(query, limit=limit * 2):
                out.append(str(f))
                if len(out) >= limit:
                    break
        except Exception:
            pass
        if len(out) < limit:
            # Fallback scans categories but still filters by query tokens,
            # so unrelated/noisy observations never dominate the result.
            toks = [t.lower() for t in str(query).split() if len(t) > 2]
            try:
                for cat in categories:
                    for r in self.memory.facts_by_category(cat, limit=limit * 2):
                        txt = r.get("fact", "") if isinstance(r, dict) else str(r)
                        low = txt.lower()
                        if txt and txt not in out and any(t in low for t in toks):
                            out.append(txt)
                        if len(out) >= limit:
                            break
            except Exception:
                pass
        return out[:limit]

    def frame_status(self) -> dict:
        """Bounded-buffer evidence for the no-warehouse requirement."""
        try:
            return {
                "live": self.service._frames.live_count(),
                "seen": self.service._counters.get("frames_seen", 0),
                "expired_deleted": self.service._frames.expired_deleted,
                "max": self.service._frames._frames.maxlen,
                "ttl": self.service._frames._ttl,
            }
        except Exception:
            return {}
