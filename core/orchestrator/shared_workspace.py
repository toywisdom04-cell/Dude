"""Phase 19: Multimodal Shared Workspace.

ONE bounded, current semantic world model that correlates the evidence
DUDE's existing subsystems already own. It is a CONTEXT/EVIDENCE layer
only: it does not plan, choose actions, execute, or recover, and it is
not a second brain, router, agent, or task engine.

Design constraints honored here:
- It never polls the screen. Visual and hand evidence are read from the
  existing single-slot buses (VisualStateBus, HandStateBus), so there
  is exactly one current state per modality and no duplicate capture
  path.
- It is CURRENT state, not history: one workspace object per turn,
  bounded fields, no frames, no screenshots, no audio, no transcript
  archive, no new database.
- It ARBITRATES rather than merges blindly: every modality carries its
  own source, timestamp, freshness, and provenance; where two sources
  could disagree (active app/window) one authority is chosen and the
  choice is recorded. Cross-modal relations are only asserted when the
  underlying evidence is fresh AND mutually consistent; otherwise the
  relation is reported unavailable with a reason.
- Confidence is carried only where upstream honestly exposes it (never
  invented, never upgraded by counting fields).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

MAX_TEXT = 200
MAX_LIST = 5
MODALITIES = ("turn", "visual", "hand", "memory", "jobs", "procedures",
              "skills", "capabilities", "profile")

# Modality name -> workspace field name (kept explicit: a mismatch here
# once made status() report EMPTY while the payload was populated).
_MODALITY_FIELD = {
    "turn": "goal",
    "visual": "visual",
    "hand": "hand",
    "memory": "memory",
    "jobs": "jobs",
    "procedures": "procedures",
    "skills": "skill_packs",
    "capabilities": "capabilities",
    "profile": "profile_summary",
}

# Grammatical deixis: a CLOSED-CLASS function-word set in every language
# (not a task phrase map, not app vocabulary). It never selects an
# action and never picks a target on its own - it only unlocks the
# evidence-gated resolver below, which refuses to guess when the
# available evidence is ambiguous. "it" is deliberately excluded: it is
# anaphoric (refers to a prior mention), not spatial deixis, so the
# front door keeps owning that judgement.
_DEIXIS = ("this", "that", "these", "those", "here", "there")
CONTINUITY_TTL_SECONDS = 600.0  # 10 minutes: same work session, not history


@dataclass
class ModalityEvidence:
    """One modality's contribution, with honest freshness + provenance."""
    source: str = ""            # e.g. "VisualStateBus", "HandStateBus"
    present: bool = False
    fresh: bool = False
    age_s: Optional[float] = None
    provenance: str = ""        # upstream provenance string
    summary: str = ""           # bounded semantic summary (no pixels)
    confidence: Optional[float] = None  # only if upstream exposes it
    unavailable_reason: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _clip(text: Any, limit: int = MAX_TEXT) -> str:
    try:
        return str(text or "")[:limit]
    except Exception:
        return ""


def _deixis_tokens(text: Any) -> list:
    """Closed-class deictic words present in the text (whole words)."""
    import re
    try:
        s = str(text or "").lower()
    except Exception:
        return []
    found = []
    for w in _DEIXIS:
        if re.search(r"\b%s\b" % re.escape(w), s):
            found.append(w)
    return found


@dataclass
class SharedWorkspace:
    """The current multimodal world model (bounded, single instance/turn)."""
    goal: str = ""
    transcript: str = ""
    active_app: str = ""
    active_window: str = ""
    app_window_authority: str = ""   # which source decided app/window
    visual: ModalityEvidence = field(default_factory=ModalityEvidence)
    hand: ModalityEvidence = field(default_factory=ModalityEvidence)
    focused_element: str = ""
    spatial_target: dict = field(default_factory=dict)
    pointed_element: dict = field(default_factory=dict)
    profile_summary: str = ""
    memory: list = field(default_factory=list)
    jobs: list = field(default_factory=list)
    procedures: list = field(default_factory=list)
    skill_packs: list = field(default_factory=list)
    capabilities: list = field(default_factory=list)
    task_progress: dict = field(default_factory=dict)
    # Phase 19 follow-up: a referent the workspace can DEFEND (deixis).
    # None when the evidence is missing or ambiguous - the front door
    # still decides what to do with it.
    referent: Optional[dict] = None
    referent_ambiguous: bool = False
    # Bounded same-session continuity (never a store, never history).
    continuity: dict = field(default_factory=dict)
    correlations: list = field(default_factory=list)
    degraded: list = field(default_factory=list)
    captured_at: float = field(default_factory=time.time)
    sources: dict = field(default_factory=dict)

    # ---------------- relations ----------------

    def add_correlation(self, name: str, valid: bool, detail: str = "",
                        evidence: Optional[dict] = None) -> bool:
        """Record a cross-modal relation ONLY when it is defensible."""
        entry = {
            "name": _clip(name, 60),
            "valid": bool(valid),
            "detail": _clip(detail, 160),
            "at": time.time(),
        }
        if evidence:
            entry["evidence"] = {
                k: evidence.get(k) for k in
                ("hand_state_key", "visual_state_key", "target",
                 "target_kind", "target_provenance")
                if k in evidence}
        self.correlations = [c for c in self.correlations
                             if c.get("name") != entry["name"]]
        self.correlations.append(entry)
        self.correlations = self.correlations[-8:]  # bounded
        return bool(valid)

    def correlation(self, name: str) -> Optional[dict]:
        for c in reversed(self.correlations or []):
            if c.get("name") == name:
                return c
        return None

    # ---------------- referent resolution ----------------

    def resolve_referent(self, text: str) -> Optional[dict]:
        """Resolve a spatial deictic ("this", "that", "here") ONLY from
        evidence the workspace can defend.

        Rules (evidence-gated, never phrase-mapped):
        - the turn must actually contain a deictic marker;
        - a candidate must be backed by FRESH evidence and name its
          source;
        - if more than one candidate class is available the referent is
          reported AMBIGUOUS and nothing is chosen;
        - if none is available the referent stays None.
        This never selects an action and never invents a target.
        """
        try:
            toks = set(_deixis_tokens(text))
            self.referent = None
            self.referent_ambiguous = False
            if not toks:
                return None
            # Candidates are EVIDENCE TIERS, not an unordered pile:
            # a validated hand point is a direct act of indicating and
            # outranks weaker fallbacks; ambiguity is only meaningful
            # WITHIN the weakest available tier.
            direct = None
            weaker = []
            # tier 1: what the hand is currently pointing at (needs the
            #    already-validated hand->visual relation)
            rel = self.correlation("hand->visual")
            if rel and rel.get("valid") and self.pointed_element:
                direct = {
                    "kind": "pointed_element",
                    "name": _clip(self.pointed_element.get("element"), 80),
                    "target_kind": self.pointed_element.get("kind"),
                    "provenance": self.pointed_element.get("provenance", ""),
                    "evidence": "hand+visual",
                }
            # tier 2/3: attention and scope fallbacks
            if self.focused_element and self.visual.present \
                    and self.visual.fresh:
                weaker.append({
                    "kind": "focused_control",
                    "name": _clip(self.focused_element, 80),
                    "target_kind": "uia",
                    "provenance": self.visual.provenance
                    or "VisualStateBus",
                    "evidence": "visual",
                })
            if self.active_window and self.visual.fresh:
                weaker.append({
                    "kind": "active_window",
                    "name": _clip(self.active_window, 80),
                    "target_kind": "window",
                    "provenance": self.visual.provenance
                    or "VisualStateBus",
                    "evidence": "visual",
                })
            if direct is not None:
                candidates = [direct]
            else:
                candidates = weaker
            if not candidates:
                self.add_correlation("deixis_referent", False,
                                     "deictic turn but no fresh evidence")
                return None
            if len(candidates) > 1:
                # Ambiguity is reported, not resolved by guessing.
                self.referent_ambiguous = True
                self.add_correlation(
                    "deixis_referent", False,
                    "ambiguous: %d candidate targets" % len(candidates),
                    {"target": ",".join(c["kind"] for c in candidates)})
                return None
            ref = candidates[0]
            ref["deixis"] = sorted(toks)
            self.referent = ref
            self.add_correlation("deixis_referent", True,
                                 "resolved from %s" % ref["evidence"], ref)
            return ref
        except Exception as e:
            log.warning(f"referent resolution failed: {e}")
            return None

    # ---------------- reporting ----------------

    def status(self) -> dict:
        """Honest per-modality availability (never class-existence)."""
        out = {"shared_workspace": "AVAILABLE", "sources": {}, "degraded": []}
        for m in MODALITIES:
            if m == "turn":
                out["sources"]["turn"] = (
                    "AVAILABLE" if self.goal or self.transcript else "EMPTY")
                continue
            ev = getattr(self, m, None)
            if isinstance(ev, ModalityEvidence):
                if not ev.present:
                    out["sources"][m] = "UNAVAILABLE"
                elif not ev.fresh:
                    out["sources"][m] = "STALE"
                else:
                    out["sources"][m] = "AVAILABLE"
            else:
                # Some modality names differ from the field name; resolve
                # explicitly so status never contradicts the payload.
                items = getattr(self, _MODALITY_FIELD.get(m, m), None) or []
                out["sources"][m] = "AVAILABLE" if items else "EMPTY"
        out["degraded"] = list(self.degraded or [])
        out["correlations"] = sum(1 for c in (self.correlations or [])
                                  if c.get("valid"))
        out["last_update"] = self.captured_at
        return out

    def describe(self, max_chars: int = 600) -> str:
        """Concise observability line (never a user-facing canned reply)."""
        try:
            parts = [f"goal={_clip(self.goal, 60) or '-'}",
                     f"app={self.active_app or '-'}/"
                     f"{_clip(self.active_window, 40) or '-'}"]
            vis = ("fresh" if self.visual.fresh else
                   ("stale" if self.visual.present else "none"))
            hand = ("fresh" if self.hand.fresh else
                    ("stale" if self.hand.present else "none"))
            parts.append(f"visual={vis}")
            parts.append(f"hand={hand}")
            parts.append(f"memory={len(self.memory)}")
            parts.append(f"jobs={len(self.jobs)}")
            parts.append(f"procedures={len(self.procedures)}")
            parts.append(f"skills={len(self.skill_packs)}")
            rel = self.correlation("hand->visual")
            if rel is not None:
                parts.append("hand->visual=%s" % (
                    "valid" if rel.get("valid") else "unavailable"))
            if self.referent is not None:
                parts.append("referent=%s(%s)" % (
                    self.referent.get("name"), self.referent.get("kind")))
            elif self.referent_ambiguous:
                parts.append("referent=ambiguous")
            if self.continuity.get("continued"):
                parts.append("continued=yes")
            if self.degraded:
                parts.append("degraded=%s" % ",".join(self.degraded[:3]))
            return ("Shared Workspace: " + " | ".join(parts))[:max_chars]
        except Exception as e:
            log.warning(f"workspace describe failed: {e}")
            return "Shared Workspace: (describe unavailable)"

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("visual", "hand"):
            d[k] = getattr(self, k).to_dict()
        return d


def _carry_continuity(ws: SharedWorkspace, previous: Optional[
        SharedWorkspace]) -> None:
    """Carry bounded SAME-SESSION continuity (never durable history).

    If the previous workspace is still inside the continuity window,
    the new one keeps a compact pointer to what was being worked on so
    multi-turn work ("continue that", "same report") has continuity
    without the workspace becoming a store. Freshness is explicit.
    """
    try:
        if previous is None:
            return
        age = time.time() - float(previous.captured_at or 0)
        if age > CONTINUITY_TTL_SECONDS or age < 0:
            return
        ws.continuity = {
            "from_goal": _clip(previous.goal, 120),
            "from_app": _clip(previous.active_app, 80),
            "from_task": _clip(
                (previous.task_progress or {}).get("task", ""), 120),
            "age_s": round(age, 1),
            "continued": bool(previous.goal or previous.task_progress),
        }
    except Exception as e:
        log.warning(f"continuity carry failed: {e}")


def compose_workspace(bundle: Any = None, visual_bus: Any = None,
                      hand_bus: Any = None, hand_tracker: Any = None,
                      goal: str = "", transcript: str = "",
                      active_goal: Optional[dict] = None,
                      previous: Optional[SharedWorkspace] = None,
                      ) -> SharedWorkspace:
    """Build the current workspace from already-gathered evidence.

    `bundle` is the Phase 17/18 ContextBundle (bounded retrievals already
    done). Visual/hand evidence come from the existing single-slot buses.
    Nothing here re-queries perception, memory, or the router.
    """
    ws = SharedWorkspace(goal=_clip(goal or getattr(bundle, "goal", ""), 200),
                         transcript=_clip(transcript, 400))
    ws.captured_at = time.time()

    # ---- turn / task ----
    ws.sources["turn"] = "cognitive_front_door"
    if active_goal:
        try:
            ws.task_progress = {
                k: _clip(active_goal.get(k), 120)
                for k in ("task", "step", "progress", "step_index")
                if active_goal.get(k) is not None}
        except Exception:
            pass

    # ---- visual: the bus is the single authority for the screen ----
    try:
        if visual_bus is not None:
            vstate = None
            try:
                vstate = visual_bus.get_fresh()
            except Exception:
                vstate = None
            if vstate is not None:
                ttl = float(getattr(visual_bus, "stale_after_seconds", 5.0))
                age = round(float(vstate.age_seconds()), 2)
                fresh = (not vstate.is_stale(ttl)) if hasattr(
                    vstate, "is_stale") else True
                ws.visual = ModalityEvidence(
                    source="VisualStateBus", present=True, fresh=fresh,
                    age_s=age,
                    provenance=_clip(getattr(vstate, "source", "")
                                     or "visual_state_bus", 80),
                    summary=_clip(
                        f"{vstate.active_app} / {vstate.window_title}", 200),
                    confidence=round(float(vstate.confidence), 3)
                    if getattr(vstate, "confidence", None) else None)
                ws.sources["visual"] = ws.visual.provenance or "visual_state_bus"
                if fresh:
                    # Authoritative app/window for this turn.
                    ws.active_app = _clip(vstate.active_app, 80)
                    ws.active_window = _clip(vstate.window_title, 120)
                    ws.app_window_authority = "VisualStateBus"
                    ws.focused_element = _clip(
                        getattr(vstate, "focused_name", ""), 100)
                else:
                    ws.degraded.append("visual_stale")
            else:
                ws.visual = ModalityEvidence(
                    source="VisualStateBus", present=False, fresh=False,
                    unavailable_reason="no fresh visual state")
                ws.degraded.append("visual_stale")
        else:
            ws.degraded.append("visual_unwired")
    except Exception as e:
        ws.degraded.append("visual_error")
        log.warning(f"workspace visual failed: {e}")

    # ---- app/window fallback: perception is NOT authoritative; it is a
    #      clearly-labelled fallback when the bus has nothing fresh ----
    if not ws.active_app:
        try:
            b_app = _clip(getattr(bundle, "active_app", ""), 80)
            b_win = _clip(getattr(bundle, "window_title", ""), 120)
            if b_app or b_win:
                ws.active_app, ws.active_window = b_app, b_win
                ws.app_window_authority = "context_bundle(fallback)"
        except Exception:
            pass

    # ---- hand: evidence only ----
    try:
        hstate = None
        stale_raw = None
        if hand_bus is not None:
            try:
                hstate = hand_bus.get_fresh()
            except Exception:
                hstate = None
            if hstate is None:
                # Distinguish "stale evidence" from "never seen": the
                # distinction matters for honest reporting.
                try:
                    raw = hand_bus.get()
                    if raw is not None and raw.is_stale(
                            float(getattr(hand_bus, "ttl_seconds", 1.0))):
                        stale_raw = raw
                except Exception:
                    stale_raw = None
        if hstate is not None:
            ttl = float(getattr(hand_bus, "ttl_seconds", 1.0))
            age = round(float(hstate.age_seconds()), 2)
            fresh = (not hstate.is_stale(ttl)) if hasattr(
                hstate, "is_stale") else True
            prim = hstate.primary() if hasattr(hstate, "primary") else None
            ws.hand = ModalityEvidence(
                source="HandStateBus", present=hstate.count() > 0,
                fresh=fresh, age_s=age, provenance="hand_state_bus",
                summary=_clip(
                    "%d hand(s)" % hstate.count(), 60),
                # honest only: the handedness score is signal quality, not
                # detector confidence (Phase 16 decision, preserved here).
                confidence=round(float(prim.handedness_score), 3)
                if prim is not None else None)
            ws.sources["hand"] = "hand_state_bus"
            if not fresh:
                ws.degraded.append("hand_stale")
        elif stale_raw is not None:
            # Stale is not the same as absent: keep the timestamp, mark
            # the relation unavailable, and never present it as current.
            ws.hand = ModalityEvidence(
                source="HandStateBus", present=stale_raw.count() > 0,
                fresh=False,
                age_s=round(float(stale_raw.age_seconds()), 2),
                provenance="hand_state_bus",
                unavailable_reason="hand state is stale")
            ws.sources["hand"] = "hand_state_bus"
            ws.degraded.append("hand_stale")
        else:
            ws.hand = ModalityEvidence(
                source="HandStateBus", present=False, fresh=False,
                unavailable_reason="no fresh hand state")
            if hand_bus is None:
                ws.degraded.append("hand_unwired")
    except Exception as e:
        ws.degraded.append("hand_error")
        log.warning(f"workspace hand failed: {e}")

    # ---- bounded retrievals carried from the bundle (no re-query) ----
    try:
        ws.memory = list(getattr(bundle, "memory", []) or [])[:MAX_LIST]
        ws.jobs = list(getattr(bundle, "jobs", []) or [])[:MAX_LIST]
        ws.procedures = list(getattr(bundle, "procedures", [])
                             or [])[:MAX_LIST]
        ws.skill_packs = list(getattr(bundle, "skill_packs", [])
                              or [])[:MAX_LIST]
        ws.capabilities = list(getattr(bundle, "capabilities", [])
                               or [])[:12]
        ws.profile_summary = _clip(
            getattr(bundle, "profile_summary", ""), 400)
        ws.sources["memory"] = (getattr(bundle, "provenance", {})
                                .get("memory", "memory"))
        ws.sources["jobs"] = (getattr(bundle, "provenance", {})
                              .get("jobs", "job_memory"))
        ws.sources["procedures"] = (getattr(bundle, "provenance", {})
                                   .get("procedures", "procedure_store"))
        ws.sources["skills"] = (getattr(bundle, "provenance", {})
                                .get("packs", "skill_packs"))
        ws.sources["profile"] = (getattr(bundle, "provenance", {})
                                 .get("profile", "human_memory"))
    except Exception as e:
        log.warning(f"workspace carry failed: {e}")

    # ---- cross-modal arbitration: hand -> visual target ----
    try:
        _hand_target = None
        if hand_tracker is not None and hand_bus is not None:
            try:
                _hand_target = hand_tracker.pointed_element(visual_bus)
            except Exception:
                _hand_target = None
        if isinstance(_hand_target, dict):
            ws.pointed_element = dict(_hand_target)
        # A relation is valid only when BOTH sides are fresh AND the hand
        # evidence belongs to the same window the visual state shows.
        if ws.pointed_element:
            same_window = True
            try:
                if ws.visual.present and ws.visual.fresh and visual_bus:
                    cur = visual_bus.get()
                    cur_key = tuple(cur.key()) if cur is not None else None
                    tgt_window = tuple(ws.pointed_element.get("window", ()))
                    if cur_key and tgt_window and tuple(tgt_window) != cur_key:
                        same_window = False
            except Exception:
                pass
            if ws.hand.fresh and ws.visual.fresh and same_window:
                ws.add_correlation("hand->visual", True,
                                   "stable hand target on fresh visual state",
                                   ws.pointed_element)
            else:
                ws.add_correlation("hand->visual", False,
                                   "hand/visual evidence not synchronized",
                                   ws.pointed_element)
                ws.degraded.append("hand_visual_unsynchronized")
    except Exception as e:
        log.warning(f"workspace correlation failed: {e}")

    # ---- same-session continuity + deictic referent ----
    try:
        _carry_continuity(ws, previous)
    except Exception:
        pass
    try:
        ws.resolve_referent(transcript or goal or ws.goal)
    except Exception:
        pass

    return ws


# ---------------- current-state holder (single slot, not a store) -------

_last_workspace: Optional[SharedWorkspace] = None


def publish_workspace(ws: SharedWorkspace) -> SharedWorkspace:
    """Keep the CURRENT workspace for observability/capability reads."""
    global _last_workspace
    try:
        _last_workspace = ws
    except Exception:
        pass
    return ws


def last_workspace() -> Optional[SharedWorkspace]:
    try:
        return _last_workspace
    except Exception:
        return None
