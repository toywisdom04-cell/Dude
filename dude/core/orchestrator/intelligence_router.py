"""DUDE Intelligence Router - Provider-independent task planning.

Routes tasks to deterministic skills, verified procedures, local reasoning,
or temporary model fallback without the orchestrator knowing the provider.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List

from .state import (
    TaskState,
    TaskType,
    SubGoal,
    Action,
    TargetSpec,
    ExpectedResult,
    VerificationMethod,
    GroundingMethod,
    RiskLevel,
    Procedure,
    PerceptionSnapshot,
    MemoryBundle,
    Plan,
    parse_verification_method,
)
from .skills import Skill, SkillRegistry, get_skill_registry
from .procedure_store import ProcedureStore, get_procedure_store
from .intelligence import IntelligenceBackend, get_registry as get_intelligence_registry
from .parameter_extractor import get_parameter_extractor
from .parameter_binder import ParameterBinder
from core.memory import Memory

log = logging.getLogger(__name__)


# Phase 6: goal-understanding helpers. All generic (no per-app logic):
# sentence-aware clause splitting, known-location resolution, and a
# small cross-clause context (dirs, document, filename) so pronouns
# ("it", "inside it") and split parameters ("content here ... name
# there") bind to one task instead of perishing per clause.
# Locations resolve through the SHELL folders (OneDrive redirection
# aware): "my Desktop" is what Explorer shows, never assumed to be
# %USERPROFILE%\Desktop.
KNOWN_LOCATIONS = (
    "desktop",
    "documents",
    "downloads",
    "pictures",
    "music",
    "videos",
)

# Document kinds DUDE can author through the GUI, mapped to the
# application that edits them (extension-handler style knowledge).
DOCUMENT_KIND_APPS = {
    "text document": "notepad",
    "text file": "notepad",
    "note": "notepad",
    ".txt": "notepad",
}



_QUOTED_RE = re.compile(r'"[^"]*"|\'[^\']*\'')


# Frozen action-verb heuristic set. This list is an OPTIMIZATION AID ONLY:
# it may shortcut splitting/deferral, but it MUST NOT define what DUDE can
# do. Competence comes from composition + general planning + model
# reasoning. DO NOT EXTEND THIS LIST to make tests pass.
_COMPOUND_VERBS = ("open|close|type|write|save|create|delete|move|"
                   "copy|click|press|scroll|drag|select|calculate|compute|"
                   "verify|add|subtract|multiply|divide|run|launch|start|"
                   "send|search|find")

_WAKE_PREFIX_RE = re.compile(
    r"^(hey|hi|hello|dude|please|can you|could you|would you|will you)"
    r"[,.\s]+", re.IGNORECASE)


def _build_local_plan(task_id, objective, steps, confidence,
                      verification, reason):
    """Module-level plan constructor for general patterns. Local pattern
    code must never depend on closure-defined builders inside unrelated
    pattern branches (UnboundLocalError when those branches don't run)."""
    plan = Plan(
        task_id=task_id,
        objective=objective,
        steps=steps,
        confidence=confidence,
        source="local",
        required_perception=1,
        verification=verification,
        risk_level=RiskLevel.LOW,
    )

    class LocalResult:
        pass
    result = LocalResult()
    result.confidence = plan.confidence
    result.reason = reason
    result.plan = plan
    return result


def _mask_quoted(text):
    """Blank quoted spans (length-preserving) so separators inside
    quoted content never split the goal."""
    def _blank(m):
        return " " * (m.end() - m.start())
    return _QUOTED_RE.sub(_blank, text)


def _split_goal_units(intent):
    """Split a goal into plannable units: sentences, then/and-then,
    ", and", ", then", bare "and VERB", and newlines. Quoted content
    never splits.

    Bare ", verb" does NOT split: app-framing ("In Notepad, type ...")
    and same-clause verbs ("calculate 2 + 2, and verify ..." is split
    by its ", and", but "In X, verb" must stay whole for the app
    frame to bind). Each unit must be fully plannable on its own.

    Bare "and VERB" DOES split, but only when VERB is a general action
    verb (open/close/type/write/save/...): "fish and chips" stays whole
    because "chips" is not an action, while "open Word and write a
    sentence" becomes two units. This is a general composition rule,
    not a per-example list: any compound goal stays ONE goal made of
    plannable units, never one swallowed action.
    """
    masked = _mask_quoted(intent)
    # Wake prefix ("Dude, ...") addresses DUDE; it is never a task unit.
    _w = _WAKE_PREFIX_RE.match(masked)
    _voff = _w.end() if _w else 0
    spans = [(m.start(), m.end()) for m in re.finditer(
        r'\.\s+|;\s+|\s+and\s+then\s+|\s+then\s+|\n+|,\s+and\s+|,\s*then\s+',
        masked)]
    # Bare "and VERB" / comma VERB: split before the separator only when
    # the next word starts a new action clause (frozen heuristic set).
    # "In Notepad, type ..." stays whole: "type" after a comma is only a
    # split when it opens a fresh clause, and app-framing keeps it bound
    # because the frame ("In <app>,") is not itself an action clause.
    for m in re.finditer(
            r'\s+and\s+(?:' + _COMPOUND_VERBS + r')\b|'
            r',\s*(?:' + _COMPOUND_VERBS + r')\b', masked, re.IGNORECASE):
        if m.group(0).lstrip().startswith(","):
            # App-frame exemption (structural): "In <app>, <verb>..." is
            # one framed clause - the frame binds the verb's target.
            _head = masked[:m.start()].strip()
            if re.search(r'\b(in|inside|within|using|via)\s+[A-Za-z][\w. ]*$',
                         _head, re.IGNORECASE):
                continue
            spans.append((m.start(), m.end()))
        else:
            spans.append((m.start(), m.start() + 4))
    spans.sort()
    units = []
    prev = _voff
    for start, end in spans + [(len(intent), len(intent))]:
        if start < _voff:
            continue
        piece = intent[prev:start].strip().rstrip(" .;")
        if piece:
            units.append(piece)
        prev = max(prev, end)
    return units or [intent.strip()]


def _resolve_known_locations(text):
    """Rewrite locative known-location phrases to absolute paths:
    "on my Desktop" -> "in <shell-Desktop>" (the "in" form is what
    the directory patterns parse). Quote-aware. Shell-accurate:
    OneDrive redirection aware, never %USERPROFILE%-assumed."""
    names = "|".join(sorted(KNOWN_LOCATIONS, key=len, reverse=True))
    out = text
    try:
        from .locations import shell_known_folder as _shell_dir
    except Exception:
        _shell_dir = None
    masked = _mask_quoted(out)
    for m in reversed(list(re.finditer(
            r'\b(?:on|to|in|at|my)\s+(?:my\s+|the\s+)?(' + names + r')\b',
            masked, re.IGNORECASE))):
        key = m.group(1).lower()
        try:
            folder = _shell_dir(key) if _shell_dir else None
        except Exception:
            folder = None
        if not folder:
            import os as _os
            folder = _os.path.join(_os.path.expanduser("~"),
                                   key.capitalize())
        start, end = m.start(), m.end()
        out = out[:start] + "in " + folder + out[end:]
        masked = masked[:start] + " " * (end - start) + masked[end:]
    return out


def _fill_pronouns(clause, ctx):
    """Bind task pronouns to context slots ("Inside it" -> the folder
    the task just created). Quote-aware; no-op without context."""
    last_dir = (ctx or {}).get("last_dir")
    if not last_dir:
        return clause
    masked = _mask_quoted(clause)
    out = clause
    for m in reversed(list(re.finditer(
            r'\binside\s+it\b|\bin\s+it\b', masked, re.IGNORECASE))):
        start, end = m.start(), m.end()
        out = out[:start] + "in " + last_dir + out[end:]
        masked = masked[:start] + " " * (end - start) + masked[end:]
    return out


def _update_task_ctx(ctx, clause, plan):
    """Harvest cross-clause slots from a planned unit: created dir,
    document app/content, save filename. Pronouns in later units bind
    to these instead of perishing."""
    import os as _os
    if ctx is None:
        return
    m = re.search(
        r'create\s+(?:a\s+)?folder\s+(?:named\s+)?["\']?([\w\-.]+)["\']?'
        r'\s+in\s+([A-Za-z]:[\\/][^"\'.,;]+)', clause, re.IGNORECASE)
    if m:
        ctx["last_dir"] = _os.path.join(
            m.group(2).strip().rstrip(" .,"),
            m.group(1).strip()).rstrip("\\/")
    m = re.search(
        r'(?:containing|type|enter)\s*[:\"\']\s*([^"\']{1,200})',
        clause, re.IGNORECASE)
    if m:
        ctx["doc_content"] = m.group(1).strip()
    steps = getattr(plan, "steps", None) or []
    if any((getattr(s, "action_type", "") or "").lower() == "type_text"
           for s in steps):
        ctx["doc_ready"] = True
    m = re.search(r'save\s+(?:it\s+)?as\s+["\']?([^\s"\'.,;]+)',
                  clause, re.IGNORECASE)
    if m:
        ctx["save_name"] = m.group(1).strip()
        if ctx.get("last_dir"):
            ctx["last_file"] = _os.path.join(
                ctx["last_dir"], ctx["save_name"])
    for s in steps:
        if ((getattr(s, "action_type", "") or "").lower() == "open_app"
                and getattr(s, "target_description", "")):
            ctx["doc_app"] = s.target_description.strip().lower()
            break


class RouteDecision(Enum):
    """How the router decided to handle the task."""
    DETERMINISTIC_SKILL = "deterministic_skill"
    VERIFIED_PROCEDURE = "verified_procedure"
    LOCAL_REASONING = "local_reasoning"
    MODEL_FALLBACK = "model_fallback"
    NO_SOLUTION = "no_solution"


@dataclass
class RoutingResult:
    """Result of routing a task."""
    decision: RouteDecision
    confidence: float
    reason: str
    skill: Optional[Skill] = None
    procedure: Optional[Procedure] = None
    plan: Optional[Plan] = None
    fallback_reason: Optional[str] = None


class IntelligenceRouter:
    """Routes tasks to the appropriate solver."""

    def __init__(
        self,
        skill_registry: Optional[SkillRegistry] = None,
        procedure_store: Optional[ProcedureStore] = None,
        intelligence_registry: Optional[Any] = None,
        memory: Optional[Memory] = None,
        min_skill_confidence: float = 0.8,
        min_procedure_confidence: float = 0.6,
        min_local_confidence: float = 0.7,
    ):
        self.skills = skill_registry or get_skill_registry()
        self.procedures = procedure_store or get_procedure_store()
        self.intelligence = intelligence_registry or get_intelligence_registry()
        self.memory = memory

        self.min_skill_confidence = min_skill_confidence
        self.min_procedure_confidence = min_procedure_confidence
        self.min_local_confidence = min_local_confidence

        # Parameter extraction and binding
        self._parameter_extractor = get_parameter_extractor()
        self._parameter_binder = ParameterBinder()

        # Track routing stats
        self._stats = {
            RouteDecision.DETERMINISTIC_SKILL: 0,
            RouteDecision.VERIFIED_PROCEDURE: 0,
            RouteDecision.LOCAL_REASONING: 0,
            RouteDecision.MODEL_FALLBACK: 0,
            RouteDecision.NO_SOLUTION: 0,
        }

    def route(
        self,
        task_state: TaskState,
        perception: PerceptionSnapshot,
        user_intent: str,
    ) -> RoutingResult:
        """Route a task to the best available solver."""

        # 1. Try deterministic skill match
        skill_result = self._try_deterministic_skill(user_intent, perception)

        # 2. Try verified procedure match (before returning skill result)
        proc_result = self._try_verified_procedure(user_intent, perception, task_state)

        # Prefer verified procedure over skill when a verified procedure matches
        # This allows learned procedures to take precedence over generic skills
        if proc_result:
            self._stats[RouteDecision.VERIFIED_PROCEDURE] += 1
            return RoutingResult(
                decision=RouteDecision.VERIFIED_PROCEDURE,
                confidence=proc_result.confidence,
                reason=proc_result.reason,
                procedure=proc_result.procedure,
                plan=proc_result.plan,
            )

        if skill_result:
            self._stats[RouteDecision.DETERMINISTIC_SKILL] += 1
            return RoutingResult(
                decision=RouteDecision.DETERMINISTIC_SKILL,
                confidence=skill_result.confidence,
                reason=skill_result.reason,
                skill=skill_result.skill,
                plan=skill_result.plan,
            )

        # 3. Try local reasoning (simple pattern-based planning)
        local_result = self._try_local_reasoning(user_intent, perception, task_state)
        if local_result:
            self._stats[RouteDecision.LOCAL_REASONING] += 1
            _out = RoutingResult(
                decision=RouteDecision.LOCAL_REASONING,
                confidence=local_result.confidence,
                reason=local_result.reason,
                plan=local_result.plan,
            )
            # Partial composition flows: unknown remainder rides along for
            # general model planning instead of killing the whole goal.
            _out.unplanned_texts = list(
                getattr(local_result, "unplanned_texts", None) or [])
            return _out

        # 4. Fall back to model backend
        if self._has_model_backend():
            self._stats[RouteDecision.MODEL_FALLBACK] += 1
            return RoutingResult(
                decision=RouteDecision.MODEL_FALLBACK,
                confidence=0.5,  # Unknown confidence
                reason="No local solution; falling back to model backend",
                fallback_reason="no_local_match",
            )

        # 5. No solution available
        self._stats[RouteDecision.NO_SOLUTION] += 1
        return RoutingResult(
            decision=RouteDecision.NO_SOLUTION,
            confidence=0.0,
            reason="No solver available for this task",
        )

    def _try_deterministic_skill(
        self,
        intent: str,
        perception: PerceptionSnapshot
    ) -> Optional[object]:
        """Try to match intent to a deterministic skill."""
        # STRUCTURAL gate (not vocabulary): a deterministic skill fires
        # only for a single plannable unit. Multi-unit goals belong to
        # composition/general planning, never to one skill.
        try:
            if len(_split_goal_units(intent)) > 1:
                return None
        except Exception:
            pass
        # Check for complex multi-step intents that should use local reasoning instead
        intent_lower = intent.lower()
        # If intent contains multiple distinct action verbs, defer to local reasoning
        action_verbs = _COMPOUND_VERBS.split("|")
        verb_count = sum(1 for verb in action_verbs if verb in intent_lower)
        if verb_count > 1:
            return None  # Defer to local reasoning for multi-action tasks
        # A click that names its target needs local-reasoning parsing to
        # extract the control name/role; the skill entry cannot do that
        # (it would plan with the whole sentence as the target).
        import re as _re
        if _re.search(r'click\s+(the\s+)?["\']?\w', intent_lower):
            return None
        # Compound goals ("Open X and show Y", "Open X then click Z")
        # must NOT be swallowed whole as one app name — the remainder
        # would become a bogus target ("Windows Settings and show the
        # System settings page"). Defer to local reasoning/model, which
        # can split units. (Multi-verb intents already exit above; this
        # catches second verbs outside that list, e.g. "show".)
        import re as _re2
        if _re2.search(r'\b(and|then)\b.{0,12}\b(show|open|go|navigate|'
                       r'display|click|type|select|switch)\b', intent_lower):
            return None
        # GUI-planned operations must never be hijacked by direct-
        # filesystem skills: Explorer-framed goals, bare renames (no
        # rename skill exists; move_path would mistarget), dir-carrying
        # folder creation (the skill would swallow "X in DIR" into one
        # bogus folder name), and from/to moves all defer to local
        # reasoning, whose GUI patterns serve them or decline honestly.
        if 'explorer' in intent_lower or 'file explorer' in intent_lower:
            return None
        if 'rename' in intent_lower:
            return None
        if _re.search(r'create\s+(?:a\s+)?folder\s+(?:named\s+)?.+\s+in\s+[A-Za-z]:',
                      intent_lower):
            return None
        if _re.search(r'\bmove\b.+\bfrom\b.+\bto\b', intent_lower):
            return None
        # GUI-owned work must never be hijacked by direct-filesystem
        # skills: a located folder creation (local Explorer pattern
        # serves it), document creation with content (local open-type
        # pattern serves it), and every "save as" (local Save dialog
        # flow serves it; the write_file skill would bypass the GUI and
        # its verification). Bare location-less "create folder X" still
        # uses the legacy skill below.
        if _re.search(r'create\s+(?:a\s+)?folder\s+(?:named\s+)?.+?\s+'
                      r'(?:in\s+|on\s+)(?:my\s+|the\s+)?[A-Za-z]',
                      intent_lower):
            return None
        if ('create' in intent_lower and 'folder' in intent_lower
                and _re.search(r'[A-Za-z]:[\\/]', intent)):
            return None
        if _re.search(r'(text\s+document|text\s+file|document\s+containing|'
                      r'containing\s*:)', intent_lower):
            return None
        if _re.search(r'save\s+(?:it\s+)?as\s+', intent_lower):
            return None
        # A read that names a UI control (read the "X" control/editor/...)
        # is a focus-free UIA read, never a filesystem read: the
        # read_file skill would mistarget it (and die on CUSTOM
        # verification). Mirrors the local-reasoning read pattern exactly.
        if _re.search(r'read\s+(?:the\s+)?["\'][^"\']+["\']\s+'
                      r'(control|editor|field|document|button|item|tab)\b',
                      intent_lower):
            return None
        

        # Check for skill matches
        matches = self.skills.find_by_intent(intent)
        if not matches:
            return None

        # A filesystem read skill must never hijack a UI-control read
        # ("Read the first text field"): UI-kind words without any path
        # belong to the UI read-by-description pattern below.
        _skill_names = {getattr(s, 'name', '') for s in matches}
        if 'read_file' in _skill_names \
                and _re.search(r'\b(text\s+field|textfield|field|button|'
                               r'checkbox|tab|item|menu|dialog|editor|'
                               r'document|control|label)\b', intent_lower) \
                and not _re.search(r'[\\/]', intent):
            matches = [s for s in matches
                       if getattr(s, 'name', '') != 'read_file']
            if not matches:
                return None

        # Filter by perception availability
        viable = []
        for skill in matches:
            # Check if perception level is sufficient
            if perception.capture_method >= skill.required_perception:
                viable.append(skill)

        if not viable:
            return None

        # Pick best match (first for now, could be more sophisticated)
        skill = viable[0]
        confidence = self.min_skill_confidence

        # Build plan from skill
        # Extract target description from intent for specific skills
        target_description = intent
        if skill.name in ("create_folder", "create_folder", "create_directory", "make_folder"):
            import re
            folder_name = intent
            for prefix in ["create folder ", "make directory ", "mkdir ", "create a folder ", "create a directory "]:
                if intent.lower().startswith(prefix):
                    folder_name = intent[len(prefix):].strip()
                    break
            # Also try to extract from "named X" pattern after prefix removal
            if folder_name != intent:
                import re
                match = re.search(r'named\s+([a-zA-Z0-9_\-\.\s]+)', folder_name, re.IGNORECASE)
                if match:
                    folder_name = match.group(1).strip()
            # Also try to extract from "named X" pattern in original intent
            if folder_name == intent:
                match = re.search(r'named\s+([a-zA-Z0-9_\-\.\s]+)', intent, re.IGNORECASE)
                if match:
                    folder_name = match.group(1).strip()
            # Remove "on the desktop" or similar location suffixes
            folder_name = re.sub(r'\s+on\s+the\s+\w+', '', folder_name, flags=re.IGNORECASE)
            folder_name = re.sub(r'\s+on\s+\w+', '', folder_name, flags=re.IGNORECASE)
            folder_name = folder_name.strip()
            target_description = folder_name
        elif skill.name in ("open_app", "open_application", "launch_app", "start_app"):
            import re
            intent_lower = intent.lower()
            app_name = intent
            for prefix in ["open ", "launch ", "start ", "run "]:
                if intent_lower.startswith(prefix):
                    app_name = intent[len(prefix):].strip()
                    break
            # Also try to extract from "named X" pattern
            if app_name == intent:
                match = re.search(r'named\s+([a-zA-Z0-9_\-\.\s]+)', intent, re.IGNORECASE)
                if match:
                    app_name = match.group(1).strip()
            # Remove common suffixes
            app_name = re.sub(r'\s+on\s+the\s+\w+', '', app_name, flags=re.IGNORECASE)
            app_name = re.sub(r'\s+on\s+\w+', '', app_name, flags=re.IGNORECASE)
            app_name = app_name.strip()
            # Voice transcripts carry trailing punctuation ("Notepad.") and
            # extra spacing: collapse + strip so downstream matching and
            # the tool receive a clean app name.
            app_name = re.sub(r'\s+', ' ', app_name).strip().rstrip('.,!?;:')
            # Never swallow a compound goal into one app name ("Paint and
            # draw a line" must plan as app "Paint", not a bogus target).
            app_name = re.split(r'\s+(?:and|then)\s+', app_name, maxsplit=1)[0].strip()
            # Full-consumption guard (structural): the extracted target
            # must account for the WHOLE intent (minus wake words and the
            # verb itself). Leftover objectives ("launch Word, put
            # sentences...") decline to composition/general planning
            # instead of executing one truncated action as the whole goal.
            _rest = intent.lower()
            _rest = re.sub(r"^(hey|hi|hello|dude|please|can you|could you|"
                           r"would you|will you)[,.\s]+", "", _rest).strip()
            _pfx_ok = False
            for _pfx in ("open ", "launch ", "start ", "run "):
                if _rest.startswith(_pfx):
                    _rest = _rest[len(_pfx):].strip()
                    _pfx_ok = True
                    break
            _rest = re.sub(r'\s+', ' ', _rest).strip().rstrip('.,!?;:')
            if not _pfx_ok or _rest != app_name.lower():
                return None
            target_description = app_name

        plan = Plan(
            task_id="",
            objective=intent,
            steps=[SubGoal(
                description=f"Execute {skill.name}",
                intent=intent,
                action_type=skill.name,
                target_description=target_description,
                verification_method=skill.verification_methods[0] if skill.verification_methods else VerificationMethod.CUSTOM,
                risk_level=skill.risk_level,
            )],
            confidence=confidence,
            source="skill",
            required_perception=skill.required_perception,
            verification=skill.verification_methods,
            risk_level=skill.risk_level,
        )

        class SkillResult:
            pass
        result = SkillResult()
        result.skill = skill
        result.confidence = confidence
        result.reason = f"Matched deterministic skill: {skill.name}"
        result.plan = plan
        return result

    def _try_verified_procedure(
        self,
        intent: str,
        perception: PerceptionSnapshot,
        task_state: TaskState,
    ) -> Optional[object]:
        """Try to match intent to a verified procedure."""
        try:
            # Search by goal text - first try exact, then normalized
            procs = self.procedures.find_by_goal(intent, self.min_procedure_confidence)
            if not procs:
                # Try normalized goal matching (for parameterized procedures)
                procs = self.procedures.find_by_normalized_goal(intent, self.min_procedure_confidence)
            if not procs:
                # Try context-based search
                if task_state.relevant_memory and task_state.relevant_memory.facts:
                    context = {"facts": [f.get("fact", "") for f in task_state.relevant_memory.facts[:5]]}
                    procs = self.procedures.find_by_context(context, self.min_procedure_confidence)
        except Exception as e:
            # Database unavailable or other error - skip procedure matching
            log.warning(f"Procedure store unavailable, skipping procedure matching: {e}")
            return None

        if not procs:
            return None

        # Pick best procedure (could add scoring here for multiple matches)
        proc = procs[0]

        # Extract parameters from user intent
        from .parameter_extractor import get_parameter_extractor
        extractor = get_parameter_extractor()
        extraction = self._parameter_extractor.extract(intent, proc.parameters)

        # Check for missing required parameters
        missing_required = []
        for param in proc.parameters:
            if param.required and param.name not in extraction.parameters:
                if param.default is not None:
                    extraction.parameters[param.name] = param.default
                else:
                    # Missing required parameter - procedure cannot be used
                    return None

        # Bind parameters to procedure steps
        bound_steps = self._bind_procedure_steps(proc.steps, extraction.parameters)

        # Convert bound procedure steps to SubGoals
        steps = []
        for i, step_data in enumerate(bound_steps):
            action_type = step_data.get("action_type", "execute")
            # Infer verification method from action type
            verification_method = step_data.get("verification_method")
            verification_method = parse_verification_method(verification_method)

            if verification_method is None:
                if action_type in ("create_folder", "create_directory", "make_folder", "write_file", "create_file"):
                    verification_method = VerificationMethod.FILE_EXISTS
                elif action_type in ("open_app", "open_application"):
                    verification_method = VerificationMethod.WINDOW_APPEARED
                elif action_type in ("close_app", "close_application"):
                    verification_method = VerificationMethod.WINDOW_DISAPPEARED
                else:
                    verification_method = VerificationMethod.CUSTOM

            # Use extracted parameter for target_description if available
            target_description = step_data.get("target", "")
            if action_type in ("create_folder", "create_directory", "make_folder") and "folder_name" in extraction.parameters:
                target_description = extraction.parameters["folder_name"]
            elif action_type in ("write_file", "create_file") and "path" in extraction.parameters:
                target_description = extraction.parameters["path"]
            elif action_type in ("open_app", "open_application") and "app_name" in extraction.parameters:
                target_description = extraction.parameters["app_name"]
            else:
                target_description = step_data.get("target", "")

            steps.append(SubGoal(
                description=step_data.get("description", ""),
                intent=step_data.get("intent", ""),
                action_type=step_data.get("action_type", "execute"),
                target_description=target_description,
                expected_result=step_data.get("expected", ""),
                verification_method=verification_method,
                risk_level=RiskLevel.LOW,
                order=i,
            ))

        plan = Plan(
            task_id=task_state.task_id,
            objective=proc.goal,
            steps=steps,
            confidence=proc.confidence,
            source="procedure",
            required_perception=1,  # Will be adjusted by TaskEngine
            verification=proc.verification,
            risk_level=RiskLevel.LOW,
        )

        class ProcResult:
            pass
        result = ProcResult()
        result.procedure = proc
        result.confidence = proc.confidence
        result.reason = f"Matched verified procedure (v{proc.version}, confidence {proc.confidence:.2f})"
        result.plan = plan
        result.bound_parameters = extraction.parameters
        return result

    def _bind_procedure_steps(self, steps: list[dict], parameters: dict[str, Any]) -> list[dict]:
        """Bind parameters to procedure steps."""
        import copy
        import re
        bound_steps = []
        for step in steps:
            bound_step = copy.deepcopy(step)

            # Bind parameters in string fields
            for key, value in bound_step.items():
                if isinstance(value, str):
                    bound_step[key] = self._substitute(value, parameters)

            # Also substitute in nested parameters field
            if "parameters" in bound_step and isinstance(bound_step["parameters"], dict):
                for param_name, param_value in bound_step["parameters"].items():
                    if isinstance(param_value, str):
                        bound_step["parameters"][param_name] = self._substitute(param_value, parameters)

            bound_steps.append(bound_step)

        return bound_steps

    def _substitute(self, text: str, params: dict[str, Any]) -> str:
        """Substitute parameter placeholders in text.

        Placeholders use the format {param_name} or {{param_name}}.
        """
        if not isinstance(text, str):
            return text

        # First pass: {{param}} (double braces)
        result = re.sub(r'\{\{(\w+)\}\}', lambda m: str(params.get(m.group(1), m.group(0))), text)
        # Second pass: {param} (single braces)
        result = re.sub(r'\{(\w+)\}', lambda m: str(params.get(m.group(1), m.group(0))), result)
        return result

    def _try_local_reasoning(
        self,
        intent: str,
        perception: PerceptionSnapshot,
        task_state: TaskState,
        _ctx: Optional[dict] = None,
    ) -> Optional[object]:
        """Attempt simple local reasoning without a model."""
        import re
        # Known locations resolve before anything parses ("on my
        # Desktop" -> "in C:\Users\<you>\Desktop").
        intent = _resolve_known_locations(intent)
        # Check for simple patterns that can be solved locally
        intent_lower = intent.lower()

        # Composition: "do A. Do B, then do C" plans each unit with the
        # same pattern matchers below and concatenates the steps. A
        # shared context carries dirs, document state, and filenames
        # across units so pronouns ("it", "inside it") and split
        # parameters bind to one task. Units with no local plan are NOT
        # fatal: the plannable prefix executes first and the remainder is
        # returned for general (model) planning with full goal context.
        # Solver presence is never a hard boundary: partial plans flow.
        units = _split_goal_units(intent)
        if len(units) > 1:
            ctx = _ctx if _ctx is not None else {}
            sub_results = []
            unplanned = []
            for unit in units:
                clause = _fill_pronouns(unit.strip(), ctx)
                sub = self._try_local_reasoning(clause, perception,
                                                task_state, _ctx=ctx)
                if sub is None or not getattr(sub, 'plan', None):
                    unplanned.append(clause)
                    continue
                _update_task_ctx(ctx, clause, sub.plan)
                sub_results.append(sub)
            if not sub_results:
                return None
            steps = []
            verifications = []
            for sub in sub_results:
                steps.extend(sub.plan.steps)
                for v in sub.plan.verification or []:
                    if v not in verifications:
                        verifications.append(v)
            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=steps,
                confidence=min(s.confidence for s in sub_results),
                source="local",
                required_perception=1,
                verification=verifications,
                risk_level=RiskLevel.LOW,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = plan.confidence
            result.reason = ("Local reasoning: composed %d clauses" %
                             len(sub_results))
            if unplanned:
                result.reason += ("; %d unit(s) left for general planning: %s"
                                  % (len(unplanned), " | ".join(unplanned)[:160]))
                result.unplanned_texts = list(unplanned)
            result.plan = plan
            return result

        # Pattern: "what is X" or "who is X" -> knowledge query.
        # Exempt utterances naming a UI construct ("what is in the
        # second text field"): those belong to the UI read-by-description
        # pattern below, not to knowledge search.
        _ui_kind = re.search(
            r'\b(text\s+field|textfield|field|button|checkbox|'
            r'tab|item|menu|dialog|editor|document|control|label)\b',
            intent_lower)
        if any(p in intent_lower for p in ["what is", "who is", "define", "explain"]) \
                and not _ui_kind:
            query = intent_lower
            for p in ["what is", "who is", "define", "explain"]:
                query = query.replace(p, "").strip()

            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=[SubGoal(
                    description=f"Query knowledge for: {query}",
                    intent=intent,
                    action_type="search_knowledge",
                    target_description=query,
                    verification_method=VerificationMethod.CUSTOM,
                    risk_level=RiskLevel.LOW,
                )],
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.CUSTOM],
                risk_level=RiskLevel.LOW,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: knowledge query pattern"
            result.plan = plan
            return result

        # Pattern: "open X" -> open_app skill (already handled by skill matching)
        # Pattern: "create X" -> filesystem (direct tool). Explorer-framed
        # creation is handled by the GUI patterns further below instead.
        # A located creation (absolute path, resolved or stated) also
        # belongs to the GUI patterns: the direct tool cannot navigate,
        # ground, or verify through the window the task owns.
        if ("explorer" not in intent_lower
                and not re.search(r'[A-Za-z]:[\\/]', intent)
                and any(p in intent_lower
                        for p in ["create folder", "make directory",
                                  "mkdir"])):
            # Extract folder name from intent
            folder_name = intent
            for prefix in ["create folder ", "make directory ", "mkdir ", "create a folder ", "create a directory "]:
                if intent_lower.startswith(prefix):
                    folder_name = intent[len(prefix):].strip()
                    break
            # Also try to extract from "named X" pattern after prefix matching
            if folder_name != intent:
                import re
                match = re.search(r'named\s+([a-zA-Z0-9_\-\.\s]+)', folder_name, re.IGNORECASE)
                if match:
                    folder_name = match.group(1).strip()
            # Remove "on the desktop" or similar location suffixes
            folder_name = re.sub(r'\s+on\s+the\s+\w+', '', folder_name, flags=re.IGNORECASE)
            folder_name = re.sub(r'\s+on\s+\w+', '', folder_name, flags=re.IGNORECASE)
            folder_name = folder_name.strip()

            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=[SubGoal(
                    description=f"Create directory",
                    intent=intent,
                    action_type="create_folder",
                    target_description=folder_name,
                    verification_method=VerificationMethod.FILE_EXISTS,
                    risk_level=RiskLevel.LOW,
                )],
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.FILE_EXISTS],
                risk_level=RiskLevel.LOW,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: filesystem creation pattern"
            result.plan = plan
            return result

        # Pattern: "open X" or "launch X" or "start X" -> open_app skill.
        # Also triggers on "In <App>, ..." goals carrying type/save
        # content (e.g. a composition clause), where the app comes from
        # the leading "In" phrase instead of an open verb.
        _in_app = None
        # Single-word app names only ("In Notepad, ..."); multi-word ones
        # ("In File Explorer, ...") belong to the Explorer patterns below.
        _in_m = re.match(r"\s*in\s+([A-Za-z][\w\-.]*)\s*,", intent,
                         re.IGNORECASE)
        if _in_m:
            _in_app = _in_m.group(1).strip()
        # Phase 6: the app can also come from document-kind inference
        # ("create a text document" -> notepad) or from an earlier
        # composition clause ("Save it" after a Notepad clause). Both
        # are goal-level knowledge, never click instructions.
        _inf_app = None
        for _kind, _app in DOCUMENT_KIND_APPS.items():
            if _kind in intent_lower:
                _inf_app = _app
                break
        _ctx_app = ((_ctx or {}).get("doc_app") or "").strip() or None
        _doc_ready = bool((_ctx or {}).get("doc_ready"))
        
        # Phase 6: Check for Explorer-specific patterns FIRST (before generic open)
        # Explorer patterns include navigation with absolute paths, which need
        # special handling (address bar, navigation, etc.). Save-as clauses
        # also carry paths but belong to the open-type Save flow, so they
        # must NOT enter the Explorer block (its trailing decline would
        # kill them before the save pattern is reached).
        _has_dir_args = bool(re.search(r"[A-Za-z]:[\\/]", intent))
        _is_save_clause = bool(re.search(r'save\s+(?:it\s+)?as\s+', intent_lower))
        if (("explorer" in intent_lower or "file explorer" in intent_lower) or _has_dir_args) \
                and not _is_save_clause:
            import os as _os
            # Directory groups are greedy to end-of-clause (clauses are
            # pre-split, so no "then" follows); trailing sentence
            # punctuation is stripped by _clean_dir. A lazy group here
            # once truncated every path to a few characters.
            _dir_re = r"([A-Za-z]:[\\/][^\"']+)"
            _name_re = r"(?:\"([^\"]+)\"|([A-Za-z0-9_\-.]+))"

            def _clean_dir(raw):
                return raw.strip().rstrip(" .,")

            def _base(directory):
                return _os.path.basename(
                    directory.rstrip("\\/")) or directory

            def _clean_name(match, *groups):
                for grp in groups:
                    if match.group(grp):
                        return match.group(grp).strip()
                return ""

            def _nav_steps(directory, open_first=True):
                steps = []
                if open_first:
                    steps.append(SubGoal(
                        description="Open File Explorer",
                        intent=intent, action_type="open_app",
                        target_description="explorer",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW))
                steps.extend([
                    SubGoal(
                        description="Focus and select the address bar",
                        intent=intent, action_type="hotkey",
                        target_description="ctrl+l",
                        expected_result="Address Bar",
                        verification_method=VerificationMethod.FOCUSED_CONTROL,
                        risk_level=RiskLevel.LOW),
                    SubGoal(
                        description=f"Enter {directory}",
                        intent=intent, action_type="type_text",
                        target_description=directory,
                        expected_result=_base(directory),
                        verification_method=VerificationMethod.OCR_TEXT_APPEARED,
                        risk_level=RiskLevel.LOW),
                    SubGoal(
                        description=f"Go to {directory}",
                        intent=intent, action_type="hotkey",
                        target_description="enter",
                        expected_result=_base(directory),
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW),
                    # Focus stays in the address bar after Enter (proven:
                    # view-hotkeys then land in the edit box and die).
                    # Esc drops focus into the file list; it never harms
                    # selection state that later steps re-establish, and
                    # never touches the clipboard. The check asserts the
                    # focused control KIND (list/item, never an edit
                    # box): item names vary, kinds prove placement.
                    SubGoal(
                        description="Focus the file list",
                        intent=intent, action_type="hotkey",
                        target_description="esc",
                        expected_result="ctype:ListItemControl|"
                                        "ListControl|TreeItemControl|TextControl",
                        verification_method=VerificationMethod.FOCUSED_CONTROL,
                        risk_level=RiskLevel.LOW),
                ])
                return steps

            def _exp_plan(steps, reasons):
                plan = Plan(
                    task_id=task_state.task_id,
                    objective=intent,
                    steps=steps,
                    confidence=self.min_local_confidence,
                    source="local",
                    required_perception=1,
                    verification=[VerificationMethod.WINDOW_APPEARED,
                                  VerificationMethod.SCREEN_DELTA,
                                  VerificationMethod.FILE_EXISTS,
                                  VerificationMethod.CLIPBOARD_CONTENT,
                                  VerificationMethod.OCR_TEXT_APPEARED],
                    risk_level=RiskLevel.LOW,
                )

                class LocalResult:
                    pass
                result = LocalResult()
                result.confidence = self.min_local_confidence
                result.reason = reasons
                result.plan = plan
                return result

            m = re.search(r"open\s+(?:file\s+)?explorer\s+(?:at\s+)?" +
                          _dir_re, intent, re.IGNORECASE)
            if m:
                directory = _clean_dir(m.group(1))
                return _exp_plan(
                    _nav_steps(directory),
                    "Local reasoning: Explorer navigation pattern")

            m = re.search(r"create\s+(?:a\s+)?folder\s+(?:named\s+)?" +
                          _name_re + r"(?:\s+in\s+" + _dir_re + r")?",
                          intent, re.IGNORECASE)
            if m:
                name = _clean_name(m, 1, 2)
                directory = _clean_dir(m.group(3)) if m.group(3) else ""
                if not directory:
                    # Directory-first form: "In File Explorer at <DIR>,
                    # create a folder named <X>". The directory ends at
                    # the comma before the operation (greedy would swallow
                    # the rest of the clause).
                    pre = re.search(
                        r"in\s+file\s+explorer\s+at\s+"
                        r"([A-Za-z]:[\\/][^\"',]+?)(?:,|\s*$)",
                        intent, re.IGNORECASE)
                    if pre:
                        directory = _clean_dir(pre.group(1))
                if not name or not directory:
                    return None
                full = _os.path.join(directory, name)
                # The fresh entry opens in rename mode (an EditControl
                # owns focus); proving THAT proves the entry exists and
                # awaits the name, without assuming what the shell
                # called it ("New folder", "(2)", ...). Typing then
                # renames it in place; the confirm step's FILE_EXISTS
                # is the real check.
                return _exp_plan(
                    _nav_steps(directory) + [
                        SubGoal(
                            description="New folder entry",
                            intent=intent, action_type="hotkey",
                            target_description="ctrl+shift+n",
                            expected_result="ctype:EditControl",
                            verification_method=VerificationMethod.FOCUSED_CONTROL,
                            risk_level=RiskLevel.LOW),
                        # The rename field keeps its text selected
                        # (white-on-blue), which tesseract cannot read, so
                        # this step only asserts Explorer context; the
                        # confirm step's FILE_EXISTS is the real check.
                        SubGoal(
                            description=f"Name folder {name}",
                            intent=intent, action_type="type_text",
                            target_description=name,
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Confirm folder {name}",
                            intent=intent, action_type="hotkey",
                            target_description="enter",
                            expected_result=full,
                            verification_method=VerificationMethod.FILE_EXISTS,
                            risk_level=RiskLevel.LOW),
                    ],
                    "Local reasoning: Explorer folder-creation pattern")

            m = re.search(r"rename\s+" + _name_re + r"\s+to\s+" +
                          _name_re + r"(?:\s+in\s+" + _dir_re + r")?",
                          intent, re.IGNORECASE)
            if m:
                old = _clean_name(m, 1, 2)
                new = _clean_name(m, 3, 4)
                directory = _clean_dir(m.group(5)) if m.group(5) else ""
                if not directory:
                    pre = re.search(
                        r"in\s+file\s+explorer\s+at\s+"
                        r"([A-Za-z]:[\\/][^\"',]+?)(?:,|\s*$)",
                        intent, re.IGNORECASE)
                    if pre:
                        directory = _clean_dir(pre.group(1))
                if not old or not new or not directory:
                    return None
                # Explorer shows/hides known extensions: ground the item
                # by stem, but expect the renamed file to keep the old
                # extension when the new name carries none.
                old_stem, old_ext = _os.path.splitext(old)
                click_name = old_stem if old_ext else old
                new_full = (new if ("." in new or not old_ext)
                            else new + old_ext)
                full = _os.path.join(directory, new_full)
                from .naming import validate_filename as _validate_new
                _new_ok, _new_reason = _validate_new(new_full)
                return _exp_plan(
                    _nav_steps(directory) + [
                        SubGoal(
                            description=f"Select {old}",
                            intent=intent, action_type="click",
                            target_description=f"{click_name} | ListItemControl",
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description="Start rename",
                            intent=intent, action_type="hotkey",
                            target_description="f2",
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Enter {new}",
                            intent=intent, action_type="type_text",
                            target_description=new,
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Confirm rename to {new}" + (
                                f" [invalid name: {_new_reason}]"
                                if not _new_ok else ""),
                            intent=intent, action_type="hotkey",
                            target_description="enter",
                            expected_result=full,
                            verification_method=VerificationMethod.FILE_EXISTS,
                            risk_level=(RiskLevel.HIGH if not _new_ok
                                        else RiskLevel.LOW),
                        ),
                    ],
                    "Local reasoning: Explorer rename pattern")

            m = re.search(r"move\s+" + _name_re + r"\s+from\s+"
                          r"([A-Za-z]:[\\/].+?)\s+to\s+" + _dir_re,
                          intent, re.IGNORECASE)
            if m:
                name = _clean_name(m, 1, 2)
                src = _clean_dir(m.group(3))
                dst = _clean_dir(m.group(4))
                if not name or not src or not dst:
                    return None
# Combine all steps: navigate to src, click/cut, navigate to dst, paste
                all_steps = _nav_steps(src) + [
                    SubGoal(
                        description=f"Select {name}",
                        intent=intent, action_type="click",
                        target_description=f"{name} | ListItemControl",
                        expected_result=_base(src),
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW),
                    SubGoal(
                        description=f"Cut {name}",
                        intent=intent, action_type="hotkey",
                        target_description="ctrl+x",
                        expected_result=name,
                        verification_method=VerificationMethod.CLIPBOARD_CONTENT,
                        risk_level=RiskLevel.MEDIUM),
                ] + _nav_steps(dst, open_first=False) + [
                    SubGoal(
                        description=f"Paste into {dst}",
                        intent=intent, action_type="hotkey",
                        target_description="ctrl+v",
                        expected_result=_os.path.join(dst, name),
                        verification_method=VerificationMethod.FILE_EXISTS,
                        risk_level=RiskLevel.MEDIUM),
                ]
                m = re.search(r"move\s+" + _name_re + r"\s+from\s+"
                          r"([A-Za-z]:[\\/].+?)\s+to\s+" + _dir_re,
                          intent, re.IGNORECASE)
            if m:
                name = _clean_name(m, 1, 2)
                src = _clean_dir(m.group(3))
                dst = _clean_dir(m.group(4))
                if not name or not src or not dst:
                    return None
                # Combine all steps: navigate to src, click/cut, navigate to dst, paste
                all_steps = _nav_steps(src) + [
                    SubGoal(
                        description=f"Select {name}",
                        intent=intent, action_type="click",
                        target_description=f"{name} | ListItemControl",
                        expected_result=_base(src),
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW),
                    SubGoal(
                        description=f"Cut {name}",
                        intent=intent, action_type="hotkey",
                        target_description="ctrl+x",
                        expected_result=name,
                        verification_method=VerificationMethod.CLIPBOARD_CONTENT,
                        risk_level=RiskLevel.MEDIUM),
                ] + _nav_steps(dst, open_first=False) + [
                    SubGoal(
                        description=f"Paste into {dst}",
                        intent=intent, action_type="hotkey",
                        target_description="ctrl+v",
                        expected_result=_os.path.join(dst, name),
                        verification_method=VerificationMethod.FILE_EXISTS,
                        risk_level=RiskLevel.MEDIUM),
                ]
                return _exp_plan(
                    all_steps,
                    "Local reasoning: Explorer move pattern")
            # Explorer mentioned but no GUI pattern matched: decline rather
            # than fall through to a direct-filesystem skill or tool.
            return None

        _ctx_app = ((_ctx or {}).get("doc_app") or "").strip() or None
        _doc_ready = bool((_ctx or {}).get("doc_ready"))
        # A close-led unit is never open-type work, even when an earlier
        # clause opened an app: it belongs to the close pattern, not here.
        _is_close_unit = bool(re.match(r'^\s*(close|exit|quit)\b', intent,
                                       re.IGNORECASE))
        if (any(p in intent_lower for p in ["open ", "launch ", "start ", "run "])
                or _in_app or _inf_app or _ctx_app) \
                and not _is_close_unit:
            app_name = intent
            for prefix in ["open ", "launch ", "start ", "run "]:
                if intent_lower.startswith(prefix):
                    rest = intent[len(prefix):].strip()
                    # Stop at first comma, " type ", " save ", " and "
                    import re
                    app_name = re.split(r',|\s+type\s+|\s+save\s+|\s+and\s+', rest, maxsplit=1)[0].strip()
                    break
            if app_name == intent and _in_app:
                app_name = _in_app
            if app_name == intent and _inf_app:
                app_name = _inf_app
            if app_name == intent and _ctx_app:
                app_name = _ctx_app
            # Also try to extract from "named X" pattern
            if app_name == intent:
                match = re.search(r'named\s+([a-zA-Z0-9_\-\.\s]+)', intent, re.IGNORECASE)
                if match:
                    app_name = match.group(1).strip()
            # Remove common suffixes
            app_name = re.sub(r'\s+on\s+the\s+\w+', '', app_name, flags=re.IGNORECASE)
            app_name = re.sub(r'\s+on\s+\w+', '', app_name, flags=re.IGNORECASE)
            app_name = app_name.strip()

            # Check for typing and saving patterns in the intent
            import re
            type_text = ""
            # Match quoted: type "hello" or type 'hello'
            type_match = re.search(r"(?:type|enter)\s+['\"]([^'\"]+)['\"]", intent, re.IGNORECASE)
            if type_match:
                type_text = type_match.group(1)
            if not type_text:
                # Match unquoted: type hello world (capture until end or " and " or " then " or " in ")
                type_match = re.search(r"(?:type|enter)\s+([^'\"]+?)(?:\s+(?:and|then|in)\b|$)", intent, re.IGNORECASE)
                if type_match:
                    type_text = type_match.group(1).strip().rstrip(" .,")
            if not type_text:
                # "create a text document containing: <words>" carries
                # unquoted content to the end of its clause (quoted
                # separators never split clauses, so this is safe).
                # A trailing location tail is not content.
                contain_match = re.search(
                    r'contain(?:ing|s)?\s*:?\s*["\']?([^"\'\n]+?)["\']?\s*$',
                    intent, re.IGNORECASE)
                if contain_match:
                    type_text = re.sub(
                        r'\s+in\s+[A-Za-z]:[\\/][^"\'.,;]*$',
                        '', contain_match.group(1)).strip().rstrip(" .")

            # Check for save pattern (quoted 'name' preferred, else bare token)
            save_as_match = re.search(r"save\s+(?:it\s+)?as\s+['\"]([^'\"]+)['\"]", intent, re.IGNORECASE)
            if not save_as_match:
                save_as_match = re.search(r"save\s+(?:it\s+)?as\s+([^\s'\",]+)", intent, re.IGNORECASE)
            save_as = save_as_match.group(1).strip().rstrip(",.") if save_as_match else None

            # Save target: filename plus the directory it must land in, e.g.
            # "save it as DUDE_Phase3B_Test.txt in Desktop\DUDE_Phase3B_Test".
            # Both are entered through the real Save dialog (verified below);
            # nothing here writes to the filesystem.
            save_file = None
            save_dir = None
            save_name_risk = RiskLevel.LOW
            if save_as:
                save_file = save_as
                # Phase 5 (P10): validate the name BEFORE anything is
                # committed. An invalid name is never silently repaired
                # (renaming user intent is unsafe); the commit step is
                # flagged HIGH risk with the reason instead, and recovery
                # handles the dialog Windows will show.
                from .naming import validate_filename as _validate_name
                _name_ok, _name_reason = _validate_name(save_file)
                if not _name_ok:
                    save_name_risk = RiskLevel.HIGH
                # Commas only terminate when nothing follows (a path like
                # "...website, Socials\..." keeps its internal comma).
                dir_match = re.search(
                    r"\bin\s+([A-Za-z0-9_\\\/:.\- ]+?)"
                    r"(?:\s*\.\s*$|,(?=\s*$)|\s+$|\s*$)",
                    intent, re.IGNORECASE)
                if dir_match:
                    save_dir = dir_match.group(1).strip().rstrip(".,")
                if not save_dir:
                    # An earlier composition clause may own the
                    # destination ("Inside it ... Save it as ...").
                    save_dir = ((_ctx or {}).get("last_dir") or "").strip()
            elif "save" in intent_lower:
                # "save" without an explicit "save as <file>" cannot be
                # grounded to dialog controls -- decline rather than invent
                # an unexecutable step.
                return None

            steps = []
            # If we detected an app to open, add open step (open verbs,
            # the leading "In <App>," phrase, or kind/context inference).
            # Skipped when an earlier clause already opened the document:
            # reopening would disturb the tab the content was typed into.
            _reuse_doc = bool(_doc_ready and _ctx_app
                              and app_name.lower() == _ctx_app.lower())
            if (("open " in intent_lower or "launch " in intent_lower
                    or "start " in intent_lower or _in_app or _inf_app
                    or _ctx_app)
                    and not _reuse_doc):
                steps.append(SubGoal(
                    description=f"Open application",
                    intent=intent,
                    action_type="open_app",
                    target_description=app_name,
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))

            if type_text:
                # Fresh untitled tab first: typing must never land in one of
                # the user's restored documents. The Untitled title check
                # after each click proves the new tab is the active one.
                steps.append(SubGoal(
                    description="Open a new blank tab",
                    intent=intent,
                    action_type="click",
                    target_description="Add New Tab | ButtonControl",
                    expected_result="Untitled",
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))
                # Ground + focus the real editable control, then prove
                # the focus (typing goes to the focused control, so this
                # is what makes the following keystrokes trustworthy)...
                steps.append(SubGoal(
                    description="Focus the text editor",
                    intent=intent,
                    action_type="click",
                    target_description="Text editor",
                    expected_result="Text editor",
                    verification_method=VerificationMethod.FOCUSED_CONTROL,
                    risk_level=RiskLevel.LOW,
                ))
                # ...then type into the focused control.
                steps.append(SubGoal(
                    description="Type text",
                    intent=intent,
                    action_type="type_text",
                    target_description=type_text,
                    expected_result=type_text,
                    verification_method=VerificationMethod.OCR_TEXT_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))

            if save_file:
                # Summon the real Save As dialog...
                steps.append(SubGoal(
                    description="Open the Save As dialog",
                    intent=intent,
                    action_type="hotkey",
                    target_description="ctrl+shift+s",
                    expected_result="Save as",
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))
                # ...ground its filename field...
                steps.append(SubGoal(
                    description="Focus the filename field",
                    intent=intent,
                    action_type="click",
                    target_description="File name: | EditControl",
                    expected_result="File name:",
                    verification_method=VerificationMethod.FOCUSED_CONTROL,
                    risk_level=RiskLevel.LOW,
                ))
                # ...select any pre-filled text so typing replaces it instead
                # of appending (appending once produced a real "file name is
                # not valid" dialog during development)...
                steps.append(SubGoal(
                    description="Select the filename field contents",
                    intent=intent,
                    action_type="hotkey",
                    target_description="ctrl+a",
                    expected_result="File name:",
                    verification_method=VerificationMethod.FOCUSED_CONTROL,
                    risk_level=RiskLevel.LOW,
                ))
                # Human-like flow, enforced: navigate the dialog to the
                # destination directory FIRST (path typed + Enter), THEN
                # enter only the bare filename. Typing whole paths as the
                # name caused invalid-name failures; a slash in a *name*
                # can never be valid, while the same slash in a *path*
                # navigates.
                import os as _os
                if save_dir:
                    home = _os.path.expanduser("~")
                    if _os.path.isabs(save_dir):
                        nav_dir = save_dir
                        full_path = _os.path.join(save_dir, save_file)
                    else:
                        nav_dir = _os.path.join(home, save_dir)
                        full_path = _os.path.join(home, save_dir, save_file)
                    steps.append(SubGoal(
                        description=f"Enter directory {nav_dir}",
                        intent=intent,
                        action_type="type_text",
                        target_description=nav_dir,
                        expected_result="Save as",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW,
                    ))
                    steps.append(SubGoal(
                        description=f"Navigate to {nav_dir}",
                        intent=intent,
                        action_type="hotkey",
                        target_description="enter",
                        expected_result="Save as",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW,
                    ))
                    # Re-ground the field after navigation before naming.
                    steps.append(SubGoal(
                        description="Focus the filename field",
                        intent=intent,
                        action_type="click",
                        target_description="File name: | EditControl",
                        expected_result="File name:",
                        verification_method=VerificationMethod.FOCUSED_CONTROL,
                        risk_level=RiskLevel.LOW,
                    ))
                    steps.append(SubGoal(
                        description="Select the filename field contents",
                        intent=intent,
                        action_type="hotkey",
                        target_description="ctrl+a",
                        expected_result="File name:",
                        verification_method=VerificationMethod.FOCUSED_CONTROL,
                        risk_level=RiskLevel.LOW,
                    ))
                else:
                    full_path = save_file
                # The filename field's text is not OCR-readable in the
                # composited capture (proven empirically: cropped OCR of
                # the field returns empty), so this step only asserts the
                # dialog context; the Save click's FILE_EXISTS is the
                # load-bearing check (a misdirected entry fails there).
                steps.append(SubGoal(
                    description=f"Enter filename {save_file}",
                    intent=intent,
                    action_type="type_text",
                    target_description=save_file,
                    expected_result="Save as",
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))
                # ...and activate the real Save button. Only flux through
                # this dialog click may create the file; verification is an
                # independent existence check, never the action's own report.
                steps.append(SubGoal(
                    description="Activate the Save button" + (
                        f" [invalid name: {_name_reason}]"
                        if save_name_risk == RiskLevel.HIGH else ""),
                    intent=intent,
                    action_type="click",
                    target_description="Save | ButtonControl",
                    expected_result=full_path,
                    verification_method=VerificationMethod.FILE_EXISTS,
                    risk_level=save_name_risk,
                ))
                if (re.search(r'verif\w*', intent_lower)
                        and save_name_risk == RiskLevel.LOW):
                    # "...and verify the saved file": an independent
                    # existence check on the joined target, planned as
                    # its own step so verification is counted, not
                    # implied.
                    steps.append(SubGoal(
                        description=f"Verify saved file {full_path}",
                        intent=intent,
                        action_type="verify_file",
                        target_description=full_path,
                        expected_result=full_path,
                        verification_method=VerificationMethod.FILE_EXISTS,
                        risk_level=RiskLevel.LOW,
                    ))

            # Only claim the intent when there is compound content to plan
            # (text to type and/or a file to save). A bare "open X and ..."
            # falls through so later patterns (e.g. control-click) can own
            # it. A save without a destination directory is declined even
            # when typing is present: without a known dir the file could
            # land anywhere, which is never safe to automate blindly.
            # Also allow plans with just app-open steps, but ONLY when the unit
            # itself names the app (open verb, In phrase, or kind inference)
            # — never on context alone, or any unplannable clause
            # ("teleport to Mars") would claim reuse of whatever the
            # previous clause opened.
            _names_app_here = (
                "open " in intent_lower or "launch " in intent_lower
                or "start " in intent_lower or _in_app or _inf_app)
            if steps and (
    (type_text and not save_as)
    or (save_as and save_dir)
    or ((not type_text and not save_as and not save_file)
        and _names_app_here)
):
                plan = Plan(
                    task_id=task_state.task_id,
                    objective=intent,
                    steps=steps,
                    confidence=self.min_local_confidence,
                    source="local",
                    required_perception=1,
                    verification=[VerificationMethod.WINDOW_APPEARED, VerificationMethod.OCR_TEXT_APPEARED, VerificationMethod.FILE_EXISTS],
                    risk_level=RiskLevel.LOW,
                )

                class LocalResult:
                    pass
                result = LocalResult()
                result.confidence = self.min_local_confidence
                result.reason = "Local reasoning: grounded open-type-save-as dialog pattern"
                result.plan = plan
                return result

        # Pattern: verify a file exists, e.g. "verify the saved file"
        # (path from an earlier clause) or "verify C:\...\F.txt exists".
        # Verification-only: execution is trivial, the FILE_EXISTS check
        # in the verification phase is the load-bearing evidence.
        verify_match = re.search(
            r'verif\w*\s+(?:that\s+|the\s+)?(?:saved\s+)?'
            r'(?:file\s+)?([A-Za-z]:[\\/][^"\'\s.,;]+)',
            intent, re.IGNORECASE)
        verify_path = None
        if verify_match:
            verify_path = verify_match.group(1).strip()
        elif (re.search(r'verif\w*', intent_lower)
                and (_ctx or {}).get("last_file")):
            verify_path = _ctx.get("last_file")
        if verify_path and re.search(r'verif\w*', intent_lower):
            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=[SubGoal(
                    description=f"Verify file {verify_path}",
                    intent=intent,
                    action_type="verify_file",
                    target_description=verify_path,
                    expected_result=verify_path,
                    verification_method=VerificationMethod.FILE_EXISTS,
                    risk_level=RiskLevel.LOW,
                )],
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.FILE_EXISTS],
                risk_level=RiskLevel.LOW,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: file-existence verify pattern"
            result.plan = plan
            return result

        # Pattern: click a named control, e.g.  click the "Save" button
        # or: In Comet, click the "<profile>" button. The control is
        # grounded against the LIVE UIA tree at execution time; the click
        # is verified by an actual screen change (menu opens, window
        # opens/closes). Works for any application, not just Notepad.
        click_match = re.search(
            r'click\s+(?:the\s+)?["\']([^"\']+)["\']\s+'
            r'(button|btn|menu\s*item|tab|input|field|link|list\s*item|item)\b',
            intent, re.IGNORECASE)
        if click_match:
            ctrl_name = click_match.group(1).strip()
            role_word = re.sub(r'\s+', ' ', click_match.group(2).strip().lower())
            role_map = {
                'button': 'ButtonControl', 'btn': 'ButtonControl',
                'menu item': 'MenuItemControl', 'menuitem': 'MenuItemControl',
                'tab': 'TabItemControl', 'input': 'EditControl',
                'field': 'EditControl', 'link': 'HyperlinkControl',
                'list item': 'ListItemControl', 'item': 'ListItemControl',
            }
            # A bare "menu" names the popup itself, not a clickable item.
            if role_word == 'menu':
                return None
            steps = []
            app_m = re.match(r'\s*(open|launch|start|run)\s+([A-Za-z][\w\-]*)',
                             intent, re.IGNORECASE)
            if app_m:
                steps.append(SubGoal(
                    description=f"Open {app_m.group(2)}",
                    intent=intent,
                    action_type="open_app",
                    target_description=app_m.group(2),
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))
            target = ctrl_name
            if role_word in role_map:
                target = f"{ctrl_name} | {role_map[role_word]}"
            steps.append(SubGoal(
                description=f"Click {ctrl_name}",
                intent=intent,
                action_type="click",
                target_description=target,
                verification_method=VerificationMethod.SCREEN_DELTA,
                risk_level=RiskLevel.MEDIUM,
            ))
            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=steps,
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.SCREEN_DELTA],
                risk_level=RiskLevel.MEDIUM,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: grounded control-click pattern"
            result.plan = plan
            return result

        # Pattern: press a hotkey, e.g. press alt+f4. Verified by an
        # actual screen change, like the click pattern above.
        hotkey_match = re.search(
            r'press\s+([A-Za-z0-9]+(?:\+[A-Za-z0-9]+)+)', intent, re.IGNORECASE)
        if hotkey_match:
            combo = hotkey_match.group(1).lower()
            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=[SubGoal(
                    description=f"Press {combo}",
                    intent=intent,
                    action_type="hotkey",
                    target_description=combo,
                    verification_method=VerificationMethod.SCREEN_DELTA,
                    risk_level=RiskLevel.MEDIUM,
                )],
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.SCREEN_DELTA],
                risk_level=RiskLevel.MEDIUM,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: hotkey pattern"
            result.plan = plan
            return result

        # Pattern: File Explorer GUI operations. Every step below drives
        # the REAL Explorer window (address bar grounding + keyboard),
        # never the direct filesystem tools. Directory arguments must be
        # absolute Windows paths inside a dedicated test namespace.
        # The gate admits Explorer-framed goals AND dir-carrying rename /
        # move / mkdir goals (explicit absolute paths are exactly what the
        # direct skills cannot serve); bare "create folder X" still falls
        # through to the legacy direct tool below.
        # Supported (DIR = absolute path, X/Y = folder or file names):
        #   open File Explorer at <DIR>
        #   create a folder named <X> in <DIR>
        #   rename <X> to <Y> in <DIR>
        #   move <X> from <SRC> to <DIR>
        _has_dir_args = bool(re.search(r"[A-Za-z]:[\\/]", intent))
        if "explorer" in intent_lower or _has_dir_args:
            import os as _os
            # Directory groups are greedy to end-of-clause (clauses are
            # pre-split, so no "then" follows); trailing sentence
            # punctuation is stripped by _clean_dir. A lazy group here
            # once truncated every path to a few characters.
            _dir_re = r"([A-Za-z]:[\\/][^\"']+)"
            _name_re = r"(?:\"([^\"]+)\"|([A-Za-z0-9_\-.]+))"

            def _clean_dir(raw):
                return raw.strip().rstrip(" .,")

            def _base(directory):
                return _os.path.basename(
                    directory.rstrip("\\/")) or directory

            def _clean_name(match, *groups):
                for grp in groups:
                    if match.group(grp):
                        return match.group(grp).strip()
                return ""

            def _nav_steps(directory, open_first=True):
                # Ctrl+L focuses AND selects the address bar in one
                # keystroke (click+ctrl+a proved unverifiable: the
                # selection highlight does not survive composited screen
                # capture). The typed path is read back through OCR, and
                # arrival through the window title.
                # open_first=False reuses the window an earlier step
                # opened: one window per task, never a new one per
                # navigation (window reuse is the rule for every app).
                steps = []
                if open_first:
                    steps.append(SubGoal(
                        description="Open File Explorer",
                        intent=intent, action_type="open_app",
                        target_description="explorer",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW))
                steps.extend([
                    SubGoal(
                        description="Focus and select the address bar",
                        intent=intent, action_type="hotkey",
                        target_description="ctrl+l",
                        expected_result="Address Bar",
                        verification_method=VerificationMethod.FOCUSED_CONTROL,
                        risk_level=RiskLevel.LOW),
                    SubGoal(
                        description=f"Enter {directory}",
                        intent=intent, action_type="type_text",
                        target_description=directory,
                        expected_result=_base(directory),
                        verification_method=VerificationMethod.OCR_TEXT_APPEARED,
                        risk_level=RiskLevel.LOW),
                    SubGoal(
                        description=f"Go to {directory}",
                        intent=intent, action_type="hotkey",
                        target_description="enter",
                        expected_result=_base(directory),
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW),
                    # Focus stays in the address bar after Enter (proven:
                    # view-hotkeys then land in the edit box and die).
                    # Esc drops focus into the file list; it never harms
                    # selection state that later steps re-establish, and
                    # never touches the clipboard. The check asserts the
                    # focused control KIND (list/item, never an edit
                    # box): item names vary, kinds prove placement.
                    SubGoal(
                        description="Focus the file list",
                        intent=intent, action_type="hotkey",
                        target_description="esc",
                        expected_result=("ctype:ListItemControl|ListControl|"
                                         "TreeItemControl|TextControl"),
                        verification_method=VerificationMethod.FOCUSED_CONTROL,
                        risk_level=RiskLevel.LOW),
                ])
                return steps

            def _exp_plan(steps, reasons):
                plan = Plan(
                    task_id=task_state.task_id,
                    objective=intent,
                    steps=steps,
                    confidence=self.min_local_confidence,
                    source="local",
                    required_perception=1,
                    verification=[VerificationMethod.WINDOW_APPEARED,
                                  VerificationMethod.SCREEN_DELTA,
                                  VerificationMethod.FILE_EXISTS,
                                  VerificationMethod.CLIPBOARD_CONTENT,
                                  VerificationMethod.OCR_TEXT_APPEARED],
                    risk_level=RiskLevel.LOW,
                )

                class LocalResult:
                    pass
                result = LocalResult()
                result.confidence = self.min_local_confidence
                result.reason = reasons
                result.plan = plan
                return result

            m = re.search(r"open\s+(?:file\s+)?explorer\s+(?:at\s+)?" +
                          _dir_re, intent, re.IGNORECASE)
            if m:
                directory = _clean_dir(m.group(1))
                return _exp_plan(
                    _nav_steps(directory),
                    "Local reasoning: Explorer navigation pattern")

            m = re.search(r"create\s+(?:a\s+)?folder\s+(?:named\s+)?" +
                          _name_re + r"(?:\s+in\s+" + _dir_re + r")?",
                          intent, re.IGNORECASE)
            if m:
                name = _clean_name(m, 1, 2)
                directory = _clean_dir(m.group(3)) if m.group(3) else ""
                if not directory:
                    # Directory-first form: "In File Explorer at <DIR>,
                    # create a folder named <X>". The directory ends at
                    # the comma before the operation (greedy would swallow
                    # the rest of the clause).
                    pre = re.search(
                        r"in\s+file\s+explorer\s+at\s+"
                        r"([A-Za-z]:[\\/][^\"',]+?)(?:,|\s*$)",
                        intent, re.IGNORECASE)
                    if pre:
                        directory = _clean_dir(pre.group(1))
                if not name or not directory:
                    return None
                full = _os.path.join(directory, name)
                # The fresh entry opens in rename mode (an EditControl
                # owns focus); proving THAT proves the entry exists and
                # awaits the name, without assuming what the shell
                # called it ("New folder", "(2)", ...). Typing then
                # renames it in place; the confirm step's FILE_EXISTS
                # on the full target is the load-bearing check.
                return _exp_plan(
                    _nav_steps(directory) + [
                        SubGoal(
                            description="New folder entry",
                            intent=intent, action_type="hotkey",
                            target_description="ctrl+shift+n",
                            expected_result="ctype:EditControl",
                            verification_method=VerificationMethod.FOCUSED_CONTROL,
                            risk_level=RiskLevel.LOW),
                        # The rename field keeps its text selected
                        # (white-on-blue), which tesseract cannot read, so
                        # this step only asserts Explorer context; the
                        # confirm step's FILE_EXISTS is the real check.
                        SubGoal(
                            description=f"Name folder {name}",
                            intent=intent, action_type="type_text",
                            target_description=name,
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Confirm folder {name}",
                            intent=intent, action_type="hotkey",
                            target_description="enter",
                            expected_result=full,
                            verification_method=VerificationMethod.FILE_EXISTS,
                            risk_level=RiskLevel.LOW),
                    ],
                    "Local reasoning: Explorer folder-creation pattern")

            m = re.search(r"rename\s+" + _name_re + r"\s+to\s+" +
                          _name_re + r"(?:\s+in\s+" + _dir_re + r")?",
                          intent, re.IGNORECASE)
            if m:
                old = _clean_name(m, 1, 2)
                new = _clean_name(m, 3, 4)
                directory = _clean_dir(m.group(5)) if m.group(5) else ""
                if not directory:
                    pre = re.search(
                        r"in\s+file\s+explorer\s+at\s+"
                        r"([A-Za-z]:[\\/][^\"',]+?)(?:,|\s*$)",
                        intent, re.IGNORECASE)
                    if pre:
                        directory = _clean_dir(pre.group(1))
                if not old or not new or not directory:
                    return None
                # Explorer shows/hides known extensions: ground the item
                # by stem, but expect the renamed file to keep the old
                # extension when the new name carries none.
                old_stem, old_ext = _os.path.splitext(old)
                click_name = old_stem if old_ext else old
                new_full = (new if ("." in new or not old_ext)
                            else new + old_ext)
                full = _os.path.join(directory, new_full)
                from .naming import validate_filename as _validate_new
                _new_ok, _new_reason = _validate_new(new_full)
                return _exp_plan(
                    _nav_steps(directory) + [
                        SubGoal(
                            description=f"Select {old}",
                            intent=intent, action_type="click",
                            target_description=f"{click_name} | ListItemControl",
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        # Rename-mode highlight does not survive composited
                        # screen capture (same invisibility class as
                        # selection); this step only asserts Explorer
                        # context, the confirm step carries the proof.
                        SubGoal(
                            description="Start rename",
                            intent=intent, action_type="hotkey",
                            target_description="f2",
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Enter {new}",
                            intent=intent, action_type="type_text",
                            target_description=new,
                            expected_result=_base(directory),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Confirm rename to {new}" + (
                                f" [invalid name: {_new_reason}]"
                                if not _new_ok else ""),
                            intent=intent, action_type="hotkey",
                            target_description="enter",
                            expected_result=full,
                            verification_method=VerificationMethod.FILE_EXISTS,
                            risk_level=(RiskLevel.HIGH if not _new_ok
                                        else RiskLevel.LOW),
                        ),
                    ],
                    "Local reasoning: Explorer rename pattern")

            m = re.search(r"move\s+" + _name_re + r"\s+from\s+"
                          r"([A-Za-z]:[\\/].+?)\s+to\s+" + _dir_re,
                          intent, re.IGNORECASE)
            if m:
                name = _clean_name(m, 1, 2)
                src = _clean_dir(m.group(3))
                dst = _clean_dir(m.group(4))
                if not name or not src or not dst:
                    return None
                return _exp_plan(
                    _nav_steps(src) + [
                        SubGoal(
                            description=f"Select {name}",
                            intent=intent, action_type="click",
                            target_description=f"{name} | ListItemControl",
                            expected_result=_base(src),
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW),
                        SubGoal(
                            description=f"Cut {name}",
                            intent=intent, action_type="hotkey",
                            target_description="ctrl+x",
                            expected_result=name,
                            verification_method=VerificationMethod.CLIPBOARD_CONTENT,
                            risk_level=RiskLevel.MEDIUM),
                    # The second navigation reuses the window the first
                    # one opened (one window per task, never a new one per
                    # navigation). The cut item travels on the clipboard.
                    ] + _nav_steps(dst, open_first=False) + [
                        SubGoal(
                            description=f"Paste into {dst}",
                            intent=intent, action_type="hotkey",
                            target_description="ctrl+v",
                            expected_result=_os.path.join(dst, name),
                            verification_method=VerificationMethod.FILE_EXISTS,
                            risk_level=RiskLevel.MEDIUM),
                    ],
                    "Local reasoning: Explorer move pattern")
            # Explorer mentioned but no GUI pattern matched: decline rather
            # than fall through to a direct-filesystem skill or tool.
            return None

        # Pattern: verify visible text, e.g. "verify that the displayed
        # result is 4". Generic self-grounding check for any app showing
        # an expected value: the read grounds live against whatever
        # control shows that text (a calculator display, a status line),
        # and TEXT_READ proves the value. No control names, no pixels.
        _verify_text_m = re.search(
            r'verif\w*\s+(?:that\s+)?(?:the\s+)?'
            r'(?:displayed\s+|visible\s+)?'
            r'(?:result|text|content|value)(?:\s+is\s+|\s*:\s*)(.+?)\s*$',
            intent, re.IGNORECASE)
        if _verify_text_m:
            _expect = _verify_text_m.group(1).strip().rstrip(" .")
            # A bare filename check belongs to the file-existence
            # pattern below, not here.
            if _expect and not re.search(r'[\\/]', _expect):
                plan = Plan(
                    task_id=task_state.task_id,
                    objective=intent,
                    steps=[SubGoal(
                        description=f"Verify displayed text {_expect}",
                        intent=intent,
                        action_type="read_text",
                        target_description=_expect,
                        expected_result=_expect,
                        verification_method=VerificationMethod.TEXT_READ,
                        risk_level=RiskLevel.LOW,
                    )],
                    confidence=self.min_local_confidence,
                    source="local",
                    required_perception=1,
                    verification=[VerificationMethod.TEXT_READ],
                    risk_level=RiskLevel.LOW,
                )

                class LocalResult:
                    pass
                result = LocalResult()
                result.confidence = self.min_local_confidence
                result.reason = "Local reasoning: visible-text verify pattern"
                result.plan = plan
                return result

        # Pattern: read a UI control by plain description, e.g. "Read the
        # first text field", "Tell me what is in the second text field".
        # No quotes/coordinates/AutomationId required: the generic
        # disambiguator (ordinal + type + reading order + label context)
        # resolves the target at grounding time. Requires a UI-kind word
        # so file reads ("read ... /path", "read file X") never land here.
        _ui_read_m = re.search(
            r'(?:\bread\b|\bwhat(?:\'s| is)(?:\s+in)?\b|\btell me\b'
            r'(?:\s+what(?:\'s| is)?(?:\s+in)?)?)\s+(?:the\s+)?(.+?)\s*[.?!]*$',
            intent, re.IGNORECASE)
        if _ui_read_m:
            _desc = _ui_read_m.group(1).strip()
            _kind_m = re.search(
                r'(.+?)\s+(text\s+field|textfield|field|button|checkbox|'
                r'tab|item|menu|dialog|editor|document|control|label)\s*$',
                _desc, re.IGNORECASE) if _desc else None
            if _kind_m and not re.search(r'[\\/]', _desc):
                _kind = _kind_m.group(2).strip().lower().replace(' ', '')
                if _kind == 'textfield':
                    _kind = 'field'
                _role_map2 = {
                    'field': 'EditControl', 'button': 'ButtonControl',
                    'checkbox': 'CheckBoxControl', 'tab': 'TabItemControl',
                    'item': 'ListItemControl', 'editor': 'DocumentControl',
                    'document': 'DocumentControl',
                }
                _target = _desc
                if _kind in _role_map2:
                    _target = f"{_desc} | {_role_map2[_kind]}"
                plan = Plan(
                    task_id=task_state.task_id,
                    objective=intent,
                    steps=[SubGoal(
                        description=f"Read {_desc}",
                        intent=intent,
                        action_type="read_text",
                        target_description=_target,
                        verification_method=VerificationMethod.TEXT_READ,
                        risk_level=RiskLevel.LOW,
                    )],
                    confidence=self.min_local_confidence,
                    source="local",
                    required_perception=1,
                    verification=[VerificationMethod.TEXT_READ],
                    risk_level=RiskLevel.LOW,
                )

                class LocalResult:
                    pass
                result = LocalResult()
                result.confidence = self.min_local_confidence
                result.reason = "Local reasoning: UI read-by-description pattern"
                result.plan = plan
                return result

        # Pattern: read a UI control's text, e.g. read the "Text editor"
        # control. Focus-free UIA ValuePattern read through the production
        # path (the background-capable action); verified by the read
        # producing text, never by pixels.
        read_match = re.search(
            r'read\s+(?:the\s+)?["\']([^"\']+)["\']\s+'
            r'(control|editor|field|document|button|item|tab)\b',
            intent, re.IGNORECASE)
        if read_match:
            ctrl_name = read_match.group(1).strip()
            role_word = read_match.group(2).strip().lower()
            role_map = {
                'control': None, 'editor': 'DocumentControl',
                'field': 'EditControl', 'document': 'DocumentControl',
                'button': 'ButtonControl', 'item': 'ListItemControl',
                'tab': 'TabItemControl',
            }
            target = ctrl_name
            if role_word in role_map and role_map[role_word]:
                target = f"{ctrl_name} | {role_map[role_word]}"
            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=[SubGoal(
                    description=f"Read {ctrl_name}",
                    intent=intent,
                    action_type="read_text",
                    target_description=target,
                    verification_method=VerificationMethod.TEXT_READ,
                    risk_level=RiskLevel.LOW,
                )],
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.TEXT_READ],
                risk_level=RiskLevel.LOW,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: control-text read pattern"
            result.plan = plan
            return result

        # Pattern: close an app, optionally discarding unsaved work. GENERAL:
        # no app names, no workflows. The app resolves from the task
        # context ("it" = the app an earlier unit opened) or names itself.
        # Unsaved-work dismissal uses the standard Windows dialog button
        # ("Don't Save"), present in Word/Notepad/etc. alike. Verification
        # judges the real end state (window gone); a missing dialog fails
        # the click step and flows into normal diagnose/recover, never a
        # silent lie.
        _close_m = re.search(
            r'^(?:close|exit|quit)\s+(it|that|this|them|the\s+.+?|'
            r'[A-Za-z][\w. ]*?)?\s*(without\s+sav(?:ing|e)|do\s+not\s+'
            r'sav(?:ing|e)|don\'?t\s+sav(?:ing|e)|not\s+sav(?:ing|e)|'
            r'(?:and\s+)?discard(?:\s+changes)?)?'
            r'(?:\s+(?:it|that|this|them|changes))?\s*$',
            intent.strip(), re.IGNORECASE)
        if _close_m:
            _who = (_close_m.group(1) or "").strip()
            _nosave = bool(_close_m.group(2))
            _app = ""
            _first = _who.split()[0].lower() if _who else ""
            if _who and _first not in ("it", "that", "this", "them"):
                _app = re.sub(r'^the\s+', '', _who,
                              flags=re.IGNORECASE).strip()
                _app = re.sub(r'\s+(without|not|do not|dont|save|discard'
                              r'|and|then)\b.*$', '', _app,
                              flags=re.IGNORECASE).strip()
            elif isinstance(_ctx, dict):
                _app = str((_ctx.get("doc_app") or "")).strip()
            if _app:
                _steps = [SubGoal(
                    description=f"Close {_app}",
                    intent=intent,
                    action_type="close_app",
                    target_description=_app,
                    expected_result=_app,
                    verification_method=VerificationMethod.WINDOW_DISAPPEARED,
                    risk_level=RiskLevel.LOW,
                )]
                if _nosave:
                    _steps.append(SubGoal(
                        description="Dismiss save prompt without saving",
                        intent=intent,
                        action_type="click",
                        target_description="Don't Save | ButtonControl",
                        expected_result=_app,
                        verification_method=
                        VerificationMethod.WINDOW_DISAPPEARED,
                        risk_level=RiskLevel.LOW,
                    ))
                return _build_local_plan(
                    task_state.task_id, intent, _steps,
                    self.min_local_confidence,
                    [VerificationMethod.WINDOW_DISAPPEARED],
                    "Local reasoning: general close pattern")
        # content entry. This is a CAPABILITY composition primitive, not an
        # app handler: it types content into wherever the task context
        # points (the executor anchors keystrokes to the nearest preceding
        # open_app step's application and OCR-verifies the result; a bare
        # unit with no app context types into the foreground window and
        # the same OCR check judges it honestly). Filesystem destinations
        # ([A-Za-z]:\, /, .txt/.md/.docx) are NOT typed: file patterns or
        # the model own those. Never invents controls, never guesses apps.
        _write_m = re.search(
            r'^(?:write|type|enter)\s+(.+)$', intent.strip(),
            re.IGNORECASE)
        if _write_m:
            _content = _write_m.group(1).strip().rstrip(" .;")
            if len(_content) >= 2 and not re.search(
                    r'[A-Za-z]:[\\/]|/\S+|\.(txt|md|docx?|pdf|csv|xlsx?)\b',
                    _content, re.IGNORECASE):
                return _build_local_plan(
                    task_state.task_id, intent,
                    [SubGoal(
                        description="Type content",
                        intent=intent,
                        action_type="type_text",
                        target_description=_content,
                        expected_result=_content,
                        verification_method=VerificationMethod.OCR_TEXT_APPEARED,
                        risk_level=RiskLevel.LOW,
                    )],
                    self.min_local_confidence,
                    [VerificationMethod.OCR_TEXT_APPEARED],
                    "Local reasoning: general content-entry pattern")

        # Pattern: "read X" or "show X file"
        if any(p in intent_lower for p in ["read file", "show file", "cat file"]):
            plan = Plan(
                task_id=task_state.task_id,
                objective=intent,
                steps=[SubGoal(
                    description=f"Read file",
                    intent=intent,
                    action_type="read_file",
                    target_description=intent,
                    verification_method=VerificationMethod.CUSTOM,
                    risk_level=RiskLevel.LOW,
                )],
                confidence=self.min_local_confidence,
                source="local",
                required_perception=1,
                verification=[VerificationMethod.CUSTOM],
                risk_level=RiskLevel.LOW,
            )

            class LocalResult:
                pass
            result = LocalResult()
            result.confidence = self.min_local_confidence
            result.reason = "Local reasoning: file read pattern"
            result.plan = plan
            return result

        return None
        # Explicit end of _try_local_reasoning

    def _has_model_backend(self) -> bool:
        """Check if a model backend is available."""
        try:
            registry = self.intelligence
            backends = registry.list()
            return len(backends) > 0
        except Exception:
            return False

    def get_stats(self) -> dict:
        """Get routing statistics."""
        return {k.value: v for k, v in self._stats.items()}


def create_intelligence_router(
    skill_registry: Optional[SkillRegistry] = None,
    procedure_store: Optional[ProcedureStore] = None,
    intelligence_registry: Optional[Any] = None,
    memory: Optional[Memory] = None,
) -> IntelligenceRouter:
    """Factory to create an IntelligenceRouter."""
    return IntelligenceRouter(
        skill_registry=skill_registry,
        procedure_store=procedure_store,
        intelligence_registry=intelligence_registry,
        memory=memory,
    )


# Global instance
_intelligence_router: Optional['IntelligenceRouter'] = None


def get_intelligence_router(
    skill_registry: Optional[SkillRegistry] = None,
    procedure_store: Optional[ProcedureStore] = None,
    intelligence_registry: Optional[Any] = None,
    memory: Optional[Memory] = None,
) -> 'IntelligenceRouter':
    """Get the global intelligence router."""
    global _intelligence_router
    if _intelligence_router is None:
        _intelligence_router = IntelligenceRouter(
            skill_registry=skill_registry,
            procedure_store=procedure_store,
            intelligence_registry=intelligence_registry,
            memory=memory,
        )
    return _intelligence_router