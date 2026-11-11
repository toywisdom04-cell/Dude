"""Phase 17: Context Priming — one bounded, structured context object.

ContextPrimer assembles, BEFORE reasoning/planning, only what is
relevant to the current goal: environment + fresh visual state summary,
bounded memory, bounded job cards, bounded verified procedures, ranked
Skill Packs, and the capabilities actually available right now.

Every source is optional and independently guarded: a missing memory,
a dead procedure store, an unwired capability, stale visual state, or
zero packs simply yields less context. Priming can never block or
break ordinary DUDE operation, never executes anything, and never
replaces the CognitiveFrontDoor or the execution system.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

MAX_FACTS = 5
MAX_JOBS = 2
MAX_PROCEDURES = 2
MAX_PACKS = 3


@dataclass
class ContextBundle:
    """Bounded, structured context for the cognitive layer."""
    goal: str = ""
    active_app: str = ""
    window_title: str = ""
    visual_summary: str = ""
    visual_age: Optional[float] = None
    visual_fresh: bool = False
    memory: list = field(default_factory=list)
    profile_summary: str = ""
    jobs: list = field(default_factory=list)
    procedures: list = field(default_factory=list)
    skill_packs: list = field(default_factory=list)  # (id, score, reason)
    capabilities: list = field(default_factory=list)
    recent_failures: list = field(default_factory=list)
    captured_at: float = field(default_factory=time.time)
    provenance: dict = field(default_factory=dict)
    sources_ok: dict = field(default_factory=dict)
    # Phase 19: the coherent multimodal layer over everything above.
    workspace: Optional[Any] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def describe(self) -> str:
        """Human-readable debugging line (observability only)."""
        try:
            procs = ", ".join(
                str(p.get("goal", ""))[:40] for p in self.procedures) or "none"
            packs = ", ".join(
                f"{pid}({sc:.2f})" for pid, sc, _ in self.skill_packs
            ) or "none"
            jobs = ", ".join(str(j.get("name", ""))[:30]
                             for j in self.jobs) or "none"
            vis = ("fresh" if self.visual_fresh else
                   (f"stale {self.visual_age:.1f}s"
                    if self.visual_age is not None else "unavailable"))
            caps = ", ".join(self.capabilities) or "none"
            return (
                f"Context primed: goal={self.goal[:60] or '-'} "
                f"app={self.active_app or '-'} "
                f"visual={vis} "
                f"skills=[{packs}] procedures=[{procs}] job=[{jobs}] "
                f"memory={len(self.memory)}f "
                f"profile={'yes' if self.profile_summary else 'no'} "
                f"capabilities=[{caps}]"
                + (f" | {self.workspace.describe(220)}"
                   if getattr(self, "workspace", None) else ""))
        except Exception as e:
            log.warning(f"describe failed: {e}")
            return "Context primed: (describe unavailable)"


class ContextPrimer:
    """Assembles a bounded ContextBundle from live local sources."""

    def __init__(self, capability_bus=None, memory=None,
                 procedure_store=None, pack_registry=None,
                 visual_bus=None, hand_bus=None, hand_tracker=None):
        self.capability_bus = capability_bus
        self.memory = memory
        self.procedure_store = procedure_store
        self.packs = pack_registry
        self.visual_bus = visual_bus
        # Phase 19: hand evidence is read from the existing bus/tracker;
        # the primer never opens the camera.
        self.hand_bus = hand_bus
        self.hand_tracker = hand_tracker
        self.last_prime_at = 0.0
        self.prime_count = 0

    # ---------------- single integration entry ----------------

    def prime(self, goal: str, active_goal: Optional[dict] = None,
              perception: Any = None) -> ContextBundle:
        """Build the bundle. Every source is optional and guarded."""
        b = ContextBundle(goal=(goal or "")[:200])
        b.captured_at = time.time()
        try:
            self._prime_environment(b, perception)
            self._prime_visual(b)
            self._prime_profile(b)
            self._prime_memory(b)
            self._prime_jobs(b)
            self._prime_procedures(b)
            self._prime_packs(b, active_goal)
            self._prime_capabilities(b)
            self._prime_workspace(b, active_goal)
        except Exception as e:
            # Priming is best effort by design: a partial bundle is
            # always better than breaking the turn.
            log.warning(f"context priming degraded: {e}")
        finally:
            self.last_prime_at = b.captured_at
            self.prime_count += 1
        return b

    # ---------------- sources ----------------

    def _prime_environment(self, b: ContextBundle, perception) -> None:
        try:
            if perception is not None:
                b.active_app = str(getattr(perception, "active_app",
                                           ""))[:80]
                win = getattr(perception, "active_window", "") or {}
                b.window_title = str(
                    win.get("title", "") if isinstance(win, dict)
                    else getattr(win, "title", ""))[:120]
        except Exception:
            pass

    def _prime_visual(self, b: ContextBundle) -> None:
        """Fresh visual summary only; stale state is never presented as
        current fact."""
        try:
            if self.visual_bus is None:
                b.sources_ok["visual"] = False
                return
            st = None
            try:
                st = self.visual_bus.get_fresh()
            except Exception:
                st = None
            if st is None:
                try:
                    st = self.visual_bus.get()
                except Exception:
                    st = None
            if st is None:
                b.sources_ok["visual"] = False
                return
            try:
                b.visual_age = round(st.age_seconds(), 2)
                ttl = getattr(self.visual_bus, "stale_after_seconds", 5.0)
                b.visual_fresh = not st.is_stale(float(ttl))
            except Exception:
                b.visual_fresh = False
            if b.visual_fresh:
                b.visual_summary = (
                    f"{st.active_app} / {st.window_title} / "
                    f"focus:{st.focused_name or '-'}")
                b.provenance["visual"] = st.source or "visual_state_bus"
            b.sources_ok["visual"] = b.visual_fresh
        except Exception as e:
            b.sources_ok["visual"] = False
            log.warning(f"visual prime failed: {e}")

    def _prime_memory(self, b: ContextBundle) -> None:
        """Phase 18: CURRENT, confidence-ranked human facts (superseded
        beliefs excluded), falling back to legacy recall."""
        try:
            if self.memory is None:
                b.sources_ok["memory"] = False
                return
            rows = []
            try:
                rows = self.memory.recall_semantic(b.goal, limit=MAX_FACTS)
            except Exception:
                rows = []
            # Semantic recall is a strict SQL substring match, which is
            # too literal for natural goals ("where do I work"). Broaden
            # with the same bounded token overlap the profile uses, and
            # keep only the best-scoring active facts.
            if not rows:
                try:
                    from .skill_packs import _tokens
                    goal_tok = set(_tokens(b.goal))
                    if goal_tok:
                        pool = self.memory.recall_semantic(
                            query="", limit=60)
                        scored = []
                        for r in pool:
                            overlap = len(goal_tok & set(
                                _tokens(r.get("fact", ""))))
                            if overlap:
                                scored.append((overlap, r))
                        scored.sort(
                            key=lambda t: (-t[0], -float(
                                t[1].get("confidence") or 0.0)))
                        rows = [r for _, r in scored[:MAX_FACTS]]
                except Exception:
                    rows = []
            if rows:
                b.memory = [
                    {"fact": str(r.get("fact", ""))[:200],
                     "confidence": r.get("confidence"),
                     "source": r.get("source", ""),
                     "category": r.get("category", "")}
                    for r in rows[:MAX_FACTS]]
                b.provenance["memory"] = "memory.facts(active)"
                b.sources_ok["memory"] = True
                return
            facts = self.memory.recall_facts(b.goal, limit=MAX_FACTS)
            b.memory = [str(f)[:200] for f in (facts or [])][:MAX_FACTS]
            b.provenance["memory"] = "Memory.recall_facts(legacy)"
            b.sources_ok["memory"] = True
        except Exception as e:
            b.sources_ok["memory"] = False
            log.warning(f"memory prime failed: {e}")

    def _prime_profile(self, b: ContextBundle) -> None:
        """Phase 18: who DUDE works for (bounded profile highlights)."""
        try:
            if self.memory is None:
                b.sources_ok["profile"] = False
                return
            from core import human_memory as hm
            profile = hm.cached_profile(self.memory)
            if not profile:
                profile = hm.build_profile(self.memory)
            summary = hm.profile_summary(profile, max_chars=400)
            if summary:
                b.profile_summary = summary
                b.provenance["profile"] = "human_memory.profile"
                b.sources_ok["profile"] = True
            else:
                b.sources_ok["profile"] = False
        except Exception as e:
            b.sources_ok["profile"] = False
            log.warning(f"profile prime failed: {e}")

    def _prime_jobs(self, b: ContextBundle) -> None:
        try:
            if self.memory is None:
                b.sources_ok["jobs"] = False
                return
            from core import job_memory as jm
            jobs = jm.find_jobs(self.memory, b.goal, limit=MAX_JOBS)
            out = []
            for j in (jobs or []):
                out.append({
                    "name": str(j.get("name", ""))[:80],
                    "purpose": str(j.get("purpose", ""))[:160],
                    "confidence": j.get("confidence"),
                    "last_success": j.get("last_success"),
                })
            b.jobs = out[:MAX_JOBS]
            b.provenance["jobs"] = "job_memory.jobs_index"
            b.sources_ok["jobs"] = True
        except Exception as e:
            b.sources_ok["jobs"] = False
            log.warning(f"job prime failed: {e}")

    def _prime_procedures(self, b: ContextBundle) -> None:
        try:
            if self.procedure_store is None:
                b.sources_ok["procedures"] = False
                return
            procs = self.procedure_store.find_by_goal(b.goal, 0.5) or []
            # A verified procedure is stored in NORMALIZED form
            # ("create a folder called {folder_name}"), so an exact
            # lookup misses a concrete goal. Use the same normalized
            # matching the intelligence router already relies on.
            if not procs:
                try:
                    procs = (self.procedure_store.find_by_normalized_goal(
                        b.goal, 0.5) or [])
                except Exception:
                    procs = []
            out = []
            for p in procs[:MAX_PROCEDURES]:
                out.append({
                    "id": getattr(p, "id", None),
                    "goal": str(getattr(p, "goal", ""))[:120],
                    "confidence": float(getattr(p, "confidence", 0.0)),
                    "success_count": int(getattr(p, "success_count", 0)),
                    "source": str(getattr(p, "source", ""))[:40],
                })
            b.procedures = out[:MAX_PROCEDURES]
            try:
                b.recent_failures = [
                    {"reason": str(f.get("reason", ""))[:120]}
                    for f in (self.procedure_store.recent_failures(
                        b.goal, limit=2) or [])]
            except Exception:
                pass
            b.provenance["procedures"] = "procedure_store"
            b.sources_ok["procedures"] = True
        except Exception as e:
            b.sources_ok["procedures"] = False
            log.warning(f"procedure prime failed: {e}")

    def _prime_packs(self, b: ContextBundle,
                     active_goal: Optional[dict]) -> None:
        try:
            if self.packs is None:
                b.sources_ok["packs"] = False
                return
            from .skill_packs import rank_packs
            ctx = {
                "app": b.active_app,
                "window": b.window_title,
                "job_text": " ".join(
                    str(j.get("purpose", "")) for j in b.jobs),
                "capabilities": list(b.capabilities),
                "procedure_goal": bool(b.procedures),
            }
            ranked = rank_packs(b.goal, self.packs.all(), ctx, MAX_PACKS)
            b.skill_packs = [(p.id, round(s, 3), why)
                             for s, p, why in ranked]
            b.provenance["packs"] = f"skill_packs@{self.packs.count()}"
            b.sources_ok["packs"] = True
        except Exception as e:
            b.sources_ok["packs"] = False
            log.warning(f"pack prime failed: {e}")

    def _prime_workspace(self, b: ContextBundle,
                         active_goal: Optional[dict]) -> None:
        """Phase 19: compose the shared multimodal workspace.

        The workspace is the coherent layer ON TOP of what this primer
        already gathered: it arbitrates app/window authority, attaches
        visual/hand freshness with provenance, and decides whether the
        hand->visual relation is defensible. It never re-queries
        perception, memory, or the router.
        """
        try:
            from .shared_workspace import (
                compose_workspace, publish_workspace, last_workspace)
            ws = compose_workspace(
                bundle=b, visual_bus=self.visual_bus,
                hand_bus=self.hand_bus, hand_tracker=self.hand_tracker,
                goal=b.goal, transcript=b.goal, active_goal=active_goal,
                previous=last_workspace())
            b.workspace = ws
            publish_workspace(ws)
            b.provenance["workspace"] = "shared_workspace"
            b.sources_ok["workspace"] = True
            self.last_workspace = ws
        except Exception as e:
            b.sources_ok["workspace"] = False
            log.warning(f"workspace compose failed: {e}")

    def _prime_capabilities(self, b: ContextBundle) -> None:
        """Real availability only; self-model reported caps are used as
        an advisory hint, never claimed as guaranteed."""
        try:
            if self.capability_bus is None:
                b.sources_ok["capabilities"] = False
                return
            caps = []
            for name in ("perception", "action_executor", "verification",
                         "recovery", "memory", "visual_state_bus",
                         "spatial_workspace", "hand_tracker"):
                try:
                    if getattr(self.capability_bus, name, None) is not None:
                        caps.append(name)
                except Exception:
                    continue
            b.capabilities = caps
            b.provenance["capabilities"] = "CapabilityBus"
            b.sources_ok["capabilities"] = True
        except Exception as e:
            b.sources_ok["capabilities"] = False
            log.warning(f"capability prime failed: {e}")


_primer: Optional[ContextPrimer] = None


def get_context_primer(capability_bus=None, memory=None,
                      procedure_store=None, pack_registry=None,
                      visual_bus=None) -> ContextPrimer:
    global _primer
    if _primer is None:
        _primer = ContextPrimer(
            capability_bus=capability_bus, memory=memory,
            procedure_store=procedure_store, pack_registry=pack_registry,
            visual_bus=visual_bus)
    return _primer


def last_context_primer() -> Optional[ContextPrimer]:
    return _primer
