"""Phase 17: Skill Packs — local, reusable domain/work knowledge.

A Skill Pack is KNOWLEDGE about a class of work, not a command, not an
agent, not a provider prompt. Packs are plain local JSON/Markdown
metadata: no code executes from a pack, nothing goes to the network,
and nothing here routes intent or reaches the action system. Packs are
selected as CONTEXT for the existing CognitiveFrontDoor; the existing
router, TaskEngine, verification and recovery stay authoritative.

Relevance is computed from meaning-bearing text overlap (goal, app,
window, job knowledge, known capabilities) with contextual boosts
(goal similarity, stated applicability, required-capability
availability). There is deliberately no phrase map, keyword-to-action
table, or app-specific branch: a pack whose words merely appear in the
goal scores, but never routes, and relevance is only ever advisory
context.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

MAX_PACKS = 64
MAX_TEXT = 4000
MAX_LIST = 24
MIN_RELEVANCE = 0.12

_STOP = frozenset("""a an the and or of to in for on at by with from into
over under about is are be do does did this that these those it its as
my our your their his her them us we i you they he she please can could
would should will shall may might must make made get got give show me
what where which who how when why task job work""".split())


def _tokens(text: Any) -> list:
    """Meaning-bearing tokens (lowercase, stopwords removed)."""
    try:
        s = str(text or "").lower()
    except Exception:
        return []
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t and t not in _STOP and len(t) > 2]


def _clip(text: Any, limit: int = MAX_TEXT) -> str:
    try:
        return str(text or "")[:limit]
    except Exception:
        return ""


def _clip_list(items, limit: int = MAX_LIST) -> list:
    try:
        return [_clip(x, 200) for x in list(items or [])[:limit]]
    except Exception:
        return []


@dataclass
class SkillPack:
    """One local pack: knowledge about a class of work."""
    id: str = ""
    name: str = ""
    version: str = "0"
    purpose: str = ""
    domain: str = "general"
    summary: str = ""
    applicable_context: list = field(default_factory=list)
    knowledge: list = field(default_factory=list)
    prerequisites: list = field(default_factory=list)
    useful_capabilities: list = field(default_factory=list)
    verification_guidance: list = field(default_factory=list)
    recovery_guidance: list = field(default_factory=list)
    provenance: str = ""
    enabled: bool = True
    path: str = ""

    @classmethod
    def from_dict(cls, data: Any, path: str = "") -> Optional["SkillPack"]:
        """Strict, bounded construction. None when malformed."""
        try:
            if not isinstance(data, dict):
                return None
            pid = _clip(data.get("id"), 64).strip()
            name = _clip(data.get("name"), 120).strip()
            if not pid or not name:
                return None
            if data.get("enabled") is False:
                enabled = False
            else:
                enabled = bool(data.get("enabled", True))
            return cls(
                id=pid,
                name=name,
                version=_clip(data.get("version") or "0", 20),
                purpose=_clip(data.get("purpose"), 600),
                domain=_clip(data.get("domain") or "general", 60),
                summary=_clip(data.get("summary"), 800),
                applicable_context=_clip_list(
                    data.get("applicable_context")),
                knowledge=_clip_list(data.get("knowledge")),
                prerequisites=_clip_list(data.get("prerequisites")),
                useful_capabilities=_clip_list(
                    data.get("useful_capabilities")),
                verification_guidance=_clip_list(
                    data.get("verification_guidance")),
                recovery_guidance=_clip_list(
                    data.get("recovery_guidance")),
                provenance=_clip(
                    (data.get("provenance") or "")
                    + (f" local_file:{Path(path).name}" if path else ""),
                    200),
                enabled=enabled,
                path=path or _clip(data.get("path"), 300))
        except Exception as e:
            log.warning(f"malformed skill pack ignored ({path}): {e}")
            return None

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def knowledge_text(self) -> str:
        """All knowledge-bearing text (bounded) for relevance scoring."""
        parts = [self.name, self.purpose, self.summary, self.domain]
        parts += list(self.applicable_context) + list(self.knowledge)
        parts += list(self.verification_guidance) + list(
            self.recovery_guidance) + list(self.prerequisites)
        return " ".join(_clip(p, 300) for p in parts)

    def describe(self, max_chars: int = 240) -> str:
        return _clip(self.summary or self.purpose or self.name,
                     max_chars)


class SkillPackRegistry:
    """Deterministic local pack loading (no code execution, no network)."""

    def __init__(self, pack_dir: Optional[str] = None):
        self.pack_dir = Path(pack_dir) if pack_dir else None
        self._packs: dict = {}
        self.load_errors: list = []
        self.loaded_at = 0.0
        self.reload_count = 0

    # ---------------- loading ----------------

    def load_dir(self, pack_dir: Optional[str] = None) -> int:
        """Load *.json packs. Disabled packs are not registered."""
        d = Path(pack_dir) if pack_dir else self.pack_dir
        self.load_errors = []
        found: dict = {}
        if not d or not d.is_dir():
            self.loaded_at = time.time()
            self.reload_count += 1
            return 0
        try:
            files = sorted(d.glob("*.json"))[:MAX_PACKS * 2]
        except Exception as e:
            self.load_errors.append(f"scan failed: {e}")
            files = []
        for f in files:
            try:
                if f.stat().st_size > 256 * 1024:
                    self.load_errors.append(f"{f.name}: too large")
                    continue
                data = json.loads(f.read_text(encoding="utf-8"))
                pack = SkillPack.from_dict(data, path=str(f))
                if pack is None:
                    self.load_errors.append(f"{f.name}: malformed")
                    continue
                if not pack.enabled:
                    continue  # disabled packs are ignored entirely
                if pack.id in found:
                    # Deterministic: first by sorted filename wins.
                    self.load_errors.append(
                        f"{f.name}: duplicate id {pack.id!r} ignored")
                    continue
                found[pack.id] = pack
                if len(found) >= MAX_PACKS:
                    break
            except Exception as e:
                self.load_errors.append(f"{f.name}: {e}")
        self._packs = found
        self.loaded_at = time.time()
        self.reload_count += 1
        return len(found)

    # ---------------- access ----------------

    def all(self) -> list:
        try:
            return list(self._packs.values())
        except Exception:
            return []

    def get(self, pack_id: str) -> Optional[SkillPack]:
        try:
            return self._packs.get(pack_id)
        except Exception:
            return None

    def count(self) -> int:
        return len(self._packs)

    def describe(self) -> str:
        if not self._packs:
            return "none installed"
        return ", ".join(
            f"{p.id}@{p.version}" for p in sorted(
                self._packs.values(), key=lambda x: x.id))


_registry: Optional[SkillPackRegistry] = None


def get_skill_pack_registry(pack_dir: Optional[str] = None,
                            reload: bool = False) -> SkillPackRegistry:
    """Process-wide registry (load once unless reload is asked for)."""
    global _registry
    if _registry is None:
        _registry = SkillPackRegistry(pack_dir)
        _registry.load_dir()
    elif reload:
        _registry.load_dir(pack_dir)
    return _registry


# ---------------- selection (contextual, advisory) ----------------

def rank_packs(goal: str, packs: list, context: Optional[dict] = None,
               limit: int = 3) -> list:
    """Bounded ranked relevance with reasons. Advisory context only.

    context may carry: app, window, job_text, capabilities (list),
    procedure_goal (bool: a verified procedure exists for this goal).
    """
    out = []
    try:
        ctx = context or {}
        goal_tok = set(_tokens(goal))
        app_tok = set(_tokens(ctx.get("app", "")))
        win_tok = set(_tokens(ctx.get("window", "")))
        job_tok = set(_tokens(ctx.get("job_text", "")))
        caps = {str(c).lower() for c in (ctx.get("capabilities") or [])}
        if not goal_tok:
            return []
        for p in packs or []:
            try:
                if not p.enabled:
                    continue
                pack_tok = set(_tokens(p.knowledge_text()))
                if not pack_tok:
                    continue
                # Core = shared meaning between goal and pack knowledge.
                core = len(goal_tok & pack_tok) / max(
                    4.0, float(len(goal_tok)))
                # Contextual boosts (small, bounded).
                env = len((app_tok | win_tok | job_tok) & pack_tok) / 40.0
                useful = p.useful_capabilities or []
                cap_hit = sum(
                    1.0 for c in useful
                    if str(c).lower() in caps) / max(1.0, float(len(useful)))
                # A verified procedure for this exact goal is stronger
                # evidence of what the user wants than generic knowledge.
                proc = 0.05 if ctx.get("procedure_goal") else 0.0
                score = min(1.0, core + env + 0.10 * cap_hit + proc)
                if score < MIN_RELEVANCE:
                    continue
                reasons = ["goal-p knowledge overlap %.2f" % core]
                if env:
                    reasons.append("environment overlap %.2f" % env)
                if cap_hit:
                    reasons.append("capabilities available %.2f" % cap_hit)
                if proc:
                    reasons.append("verified procedure exists for goal")
                out.append((score, p, "; ".join(reasons)))
            except Exception:
                continue
        out.sort(key=lambda t: (-t[0], t[1].id))
        return out[:max(1, int(limit or 3))]
    except Exception as e:
        log.warning(f"rank_packs failed: {e}")
        return []
