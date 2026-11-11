"""Phase 13 live learning demonstration (real runtime, generic OS work).

Run A: novel folder goal  -> plan, execute, verify, candidate pending.
Run B: similar folder goal -> second success -> PROMOTION to procedure.
Run C: third similar goal -> VERIFIED_PROCEDURE retrieval + reuse proof.
Run D: impossible folder name -> verified failure path: failure event
       recorded, nothing promoted.

No app-specific content, no faked inserts: every record originates
from a real execution result. Cleans up its demo folders afterwards.
"""
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')
os.chdir(r'E:\Dude\dude')

DEMO_DIR = r'E:\Dude\phase13-demo'
os.makedirs(DEMO_DIR, exist_ok=True)
os.chdir(DEMO_DIR)

from core.memory import Memory
from core.brain import Brain
from core.observer import Observer
from core.screentree import ScreenMap
from core.tracker import Tracker
from core.experience import ExperienceLearner
from core.capability_bus import CapabilityBus
from core.config import get_config
from core.orchestrator.brain_adapter import create_brain_adapter
from core.orchestrator.perception import PerceptionEngine
from core.orchestrator.action_executor import ActionExecutor
from core.orchestrator.verification import VerificationEngine
from core.orchestrator.recovery import RecoveryEngine
from core.orchestrator.intelligence_router import IntelligenceRouter, RouteDecision
from core.orchestrator.task_engine import TaskEngine
from core.orchestrator.procedure_store import get_procedure_store
from core.orchestrator.procedure_learner import get_procedure_learner
from core.orchestrator.procedure_adaptation import get_adaptation_manager


def build():
    cfg = get_config()
    memory = Memory()
    ask_user = lambda prompt: ""  # noqa: E731 -- autonomous demo, no prompts
    brain = Brain(memory, ask_user)
    observer = Observer(memory, brain)
    screentree = ScreenMap()
    tracker = Tracker(memory)
    learner_exp = ExperienceLearner(memory, cfg.data_dir)

    from PIL import Image, ImageGrab
    from core.tools import _capture_screen_composite
    from core.ocr import ocr_image

    def _ocr_fn():
        try:
            img, _ = _capture_screen_composite()
            if img is None:
                return None
            text = ocr_image(img)
            if not text:
                return {"text": "", "regions": []}
            return {"text": text, "regions": []}
        except Exception:
            return None

    def _capture_fn():
        try:
            img, _ = _capture_screen_composite()
            return img
        except Exception:
            return None

    perception = PerceptionEngine(
        get_observer=lambda: observer,
        get_experience=lambda: learner_exp,
        get_screentree=lambda: screentree,
        get_tracker=lambda: tracker,
        get_ocr_fn=_ocr_fn,
        get_capture_fn=_capture_fn,
    )
    store = get_procedure_store()
    router = IntelligenceRouter(procedure_store=store, memory=memory)
    action_executor = ActionExecutor(perception=perception, memory=memory,
                                     min_confidence=0.7)
    verification = VerificationEngine(perception=perception)
    recovery = RecoveryEngine(max_retries=3, retry_delays=(1.0, 3.0, 10.0))
    brain_adapter = create_brain_adapter(brain)
    learner = get_procedure_learner()
    adapt_mgr = get_adaptation_manager(procedure_store=store)
    cap_bus = CapabilityBus()
    cap_bus.attach(memory=memory, observer=observer, brain=brain,
                   screentree=screentree)
    engine = TaskEngine(
        intelligence=brain_adapter,
        perception=perception,
        action_executor=action_executor,
        verification=verification,
        recovery=recovery,
        permission_gate=None,
        voice=None,
        memory=memory,
        max_retries=2,
        perception_freshness_seconds=5.0,
        intelligence_router=router,
        procedure_learner=learner,
        adaptation_manager=adapt_mgr,
        use_real_execution=True,
    )
    engine.cap_bus = cap_bus
    return engine, router, store, learner, memory


def snapshot(store, learner, label):
    procs = store.find_by_goal("", 0.0)
    evs = store.recent_failures("", limit=1000)
    cands = {k: v.success_count for k, v in learner._candidates.items()}
    print("--- %s ---" % label, flush=True)
    print("procedures: %d" % len(procs), flush=True)
    for p in procs:
        print("  goal=%r conf=%.2f ok=%d fail=%d src=%s" % (
            p.goal[:80], p.confidence, p.success_count,
            p.failure_count, p.source), flush=True)
    print("events: %d  candidates: %s" % (len(evs), cands), flush=True)
    return procs, evs, cands


def run_goal(engine, goal):
    t0 = time.time()
    state = asyncio.run(engine.run(goal))
    dt = time.time() - t0
    ok_steps = [s for s in (state.completed_steps or []) if s.success]
    print("goal=%r" % goal, flush=True)
    print("  state=%s steps=%d ok=%d fail_reason=%r time=%.1fs" % (
        engine._state.value if hasattr(engine._state, "value")
        else engine._state,
        len(state.subgoals or []), len(ok_steps),
        getattr(state, "failure_reason", ""), dt), flush=True)
    return state


def main():
    engine, router, store, learner, memory = build()
    print("learner enabled:", learner.enable_learning, flush=True)
    print("router stats start:", router.stats() if hasattr(router, "stats") else "?",
          flush=True)
    snapshot(store, learner, "BEFORE")

    base_stats = dict(router._stats)
    a = run_goal(engine, "create a folder called phase13-alpha")
    s1 = {k.value: v for k, v in router._stats.items()}
    snapshot(store, learner, "AFTER RUN A (expect: candidate pending, no procedure)")

    b = run_goal(engine, "create a folder called phase13-beta")
    s2 = {k.value: v for k, v in router._stats.items()}
    snapshot(store, learner, "AFTER RUN B (expect: promoted procedure)")

    c = run_goal(engine, "create a folder called phase13-gamma")
    s3 = {k.value: v for k, v in router._stats.items()}
    snapshot(store, learner, "AFTER RUN C (expect: verified-procedure reuse)")

    d = run_goal(engine, "create a folder called phase13-bad:name")
    snapshot(store, learner, "AFTER RUN D (expect: failure event, no promotion)")

    print("router stats deltas:", flush=True)
    for k in sorted(set(list(s1) + list(s2) + list(s3))):
        print("  %s: A=%s B=%s C=%s" % (k, s1.get(k), s2.get(k), s3.get(k)),
              flush=True)

    # Filesystem truth (independent of DUDE's own verification).
    for name in ("phase13-alpha", "phase13-beta", "phase13-gamma"):
        print("disk check %s: %s" % (
            name, os.path.isdir(os.path.join(DEMO_DIR, name))), flush=True)
    print("disk check phase13-bad:name absent: %s" % (
        not os.path.exists(os.path.join(DEMO_DIR, "phase13-bad:name"))),
        flush=True)

    report = {
        "runs": {
            "A": {"goal": "phase13-alpha"},
            "B": {"goal": "phase13-beta"},
            "C": {"goal": "phase13-gamma"},
            "D": {"goal": "phase13-bad:name"},
        },
        "router_stats": {"A": s1, "B": s2, "C": s3},
    }
    with open(os.path.join(DEMO_DIR, "phase13_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    # Cleanup demo artifacts (knowledge stays — that IS the learning).
    for name in ("phase13-alpha", "phase13-beta", "phase13-gamma"):
        shutil.rmtree(os.path.join(DEMO_DIR, name), ignore_errors=True)
    print("cleanup done", flush=True)


if __name__ == "__main__":
    main()
