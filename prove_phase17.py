"""Phase 17 live proof: context priming on a real runtime.

Real DUDE components (memory, job memory, procedure store, visual
state bus, capability bus, CognitiveFrontDoor) plus the two installed
sample packs. Shows: goal -> ranked relevant pack -> bounded context
bundle -> cognitive interpretation carrying it -> existing execution
path untouched. No fabricated task success, no file mutation.
"""
import os
import sys

sys.path.insert(0, r'E:\Dude\dude')
os.chdir(r'E:\Dude\dude')

import prove_phase13 as P
from core.config import get_config
from core.memory import Memory
from core.agent_state import AgentState
from core.capability_bus import CapabilityBus
from core.orchestrator.state import PerceptionLevel
from core.orchestrator.visual_state import VisualStateBus
from core.orchestrator.skill_packs import get_skill_pack_registry
from core.orchestrator.context_primer import ContextPrimer
from core.orchestrator import IntelligenceRouter
from core.orchestrator.goal_executor import create_cognitive_front_door
import core.job_memory as jm


def main():
    cfg = get_config()
    engine, _, store, _, memory = P.build()
    vbus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = vbus
    # Real job knowledge for this user's recurring work.
    jm.upsert_job(memory, "monthly business reporting",
                  purpose="compile the monthly report from source data",
                  confidence=0.8)
    jm.record_success(memory, "monthly business reporting")

    registry = get_skill_pack_registry(
        pack_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "dude_data", "skill_packs"),
        reload=True)
    print("SKILL PACK REGISTRY: %d installed [%s]" % (
        registry.count(), registry.describe()), flush=True)
    assert registry.count() >= 1, "no packs installed"

    cap = CapabilityBus()
    cap.attach(perception=engine.perception, action_executor=None,
               verification=None, recovery=None, memory=memory,
               visual_state_bus=vbus)
    primer = ContextPrimer(capability_bus=cap, memory=memory,
                           procedure_store=store, pack_registry=registry,
                           visual_bus=vbus)
    router = IntelligenceRouter(procedure_store=store, memory=memory)
    cfd = create_cognitive_front_door(capability_bus=cap,
                                      agent_state=AgentState(),
                                      intelligence_router=router,
                                      memory=memory)
    cfd.context_primer = primer

    # Live perception -> real visual state.
    snap = engine._observe_for_task(PerceptionLevel.LEVEL_2_UIA_TREE)
    print("VISUAL: app=%s title=%s" % (
        snap.active_app, snap.active_window.get("title", "")), flush=True)

    goal = ("prepare the monthly report from the source data and verify "
            "the totals before saving it")
    print("GOAL: %s" % goal, flush=True)

    it = cfd.process_turn(goal, snap, {})
    b = it.context_bundle
    assert b is not None, "no context bundle produced"
    print("SELECTED SKILL PACKS:", flush=True)
    for pid, score, why in b.skill_packs:
        print("   %s score=%.3f (%s)" % (pid, score, why), flush=True)
    assert any(pid == "sp.document_reporting" for pid, _, _ in
               b.skill_packs), "relevant pack not selected"
    print("JOBS:", [j["name"] for j in b.jobs], flush=True)
    assert any(j["name"] == "monthly business reporting" for j in b.jobs)
    print("PROCEDURES:", [p["goal"] for p in b.procedures], flush=True)
    print("MEMORY facts:", len(b.memory), flush=True)
    print("CAPABILITIES:", b.capabilities, flush=True)
    print("BUNDLE:", b.describe(), flush=True)
    print("COGNITIVE RESULT: mode=%s action=%s conf=%.2f bundle=%s" % (
        it.intent_kind.value, it.requires_computer_action,
        it.confidence, "attached" if it.context_bundle else "missing"),
        flush=True)
    # Existing authority unchanged: the front door still returns its
    # own interpretation; packs carried no execution.
    assert hasattr(it, "intent_kind")
    assert not hasattr(it, "skill_agent")
    print("PROOF PASS: goal -> pack -> bounded bundle -> existing flow",
          flush=True)


if __name__ == "__main__":
    main()
