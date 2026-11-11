"""Phase 19 live proof (v2): closes the four reported limitations.

Real runtime only. Proves, with real evidence:
 1. real human memory fact is stored and primed (slot non-empty)
 2. a REAL verified procedure is learned by executing twice, then
    primed back (slot non-empty, not fabricated)
 3. a REAL skill pack is selected for a matching goal
 4. a LIVE hand -> visual correlation when a hand is present; honest
    degradation when it is not (never a fake pass)
 5. deictic referent resolution and ambiguity handling
 6. same-session continuity across turns
 7. stale evidence is rejected
 8. the CognitiveFrontDoor receives the unified workspace

Usage:
  python prove_phase19.py            # core (no hand required)
  python prove_phase19.py hand       # also proves live hand (needs a hand)
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')
os.chdir(r'E:\Dude\dude')

import prove_phase13 as P
from core.capability_bus import CapabilityBus
from core.agent_state import AgentState
from core.orchestrator.state import PerceptionLevel
from core.orchestrator.visual_state import VisualStateBus
from core.orchestrator.hand_tracking import (
    Gesture, HandStateBus, HandPerception, HandTracker)
from core.orchestrator.skill_packs import get_skill_pack_registry
from core.orchestrator.context_primer import ContextPrimer
from core.orchestrator.shared_workspace import last_workspace
from core.orchestrator import IntelligenceRouter
from core.orchestrator.goal_executor import create_cognitive_front_door
from core import human_memory as hm
from core import job_memory as jm

PROBE = "phase19_probe_work_focus"


def section(n, t):
    print("\n%s. %s" % (n, t), flush=True)


def main():
    want_hand = "hand" in sys.argv[1:]
    engine, _, store, _, memory = P.build()

    # ---------- 1. real human semantic memory ----------
    section(1, "REAL HUMAN MEMORY (CapabilityBus HUMAN_MEMORY)")
    cap = CapabilityBus()
    cap.attach(memory=memory)
    r = cap.request("HUMAN_MEMORY", {
        "op": "remember",
        "fact": "phase19 probe: prefers verified results before reports",
        "category": "preferences", "subject": PROBE, "confidence": 0.8})
    print("   stored=%s" % r["success"], flush=True)
    assert r["success"]

    # ---------- 2. REAL verified procedure (execute twice) ----------
    section(2, "REAL VERIFIED PROCEDURE (two real verified executions)")
    for name in ("phase19-probe-alpha", "phase19-probe-beta"):
        st = asyncio.run(engine.run("create a folder called %s" % name))
        ok = [s for s in (st.completed_steps or []) if s.success]
        print("   %s -> ok_steps=%d" % (name, len(ok)), flush=True)
        assert ok
    procs = store.find_by_goal("folder", 0.0)
    print("   promoted procedures: %s" % [
        (p.goal[:40], p.confidence) for p in procs], flush=True)
    assert procs, "no real procedure was promoted"

    # ---------- 3. real job card ----------
    jm.upsert_job(memory, "phase19 live probe job",
                  purpose="verify the shared workspace wiring", confidence=0.5)
    jm.record_outcome(memory, "phase19 live probe job", True)

    # ---------- 4. live visual + workspace ----------
    section(3, "LIVE VISUAL STATE -> SHARED WORKSPACE")
    vbus = VisualStateBus(stale_after_seconds=30.0)
    engine.visual_state_bus = vbus
    snap = engine._observe_for_task(PerceptionLevel.LEVEL_2_UIA_TREE)
    registry = get_skill_pack_registry(
        pack_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "dude_data", "skill_packs"), reload=True)
    cap.attach(perception=engine.perception, memory=memory,
               visual_state_bus=vbus, verification=None, recovery=None,
               action_executor=None)
    primer = ContextPrimer(capability_bus=cap, memory=memory,
                           procedure_store=store, pack_registry=registry,
                           visual_bus=vbus, hand_bus=HandStateBus())
    goal = ("organize my project files into a tidy structure and verify "
            "the results before reporting")
    b = primer.prime(goal, perception=snap)
    ws = b.workspace
    print("   %s" % ws.describe(), flush=True)
    print("   SOURCES: %s" % ws.status()["sources"], flush=True)
    print("   memory=%d jobs=%d procedures=%d skills=%s" % (
        len(ws.memory), len(ws.jobs), len(ws.procedures),
        [p[0] for p in ws.skill_packs]), flush=True)
    assert ws.memory, "memory slot empty"
    assert ws.jobs, "jobs slot empty"
    assert ws.skill_packs, "no skill pack selected"
    assert any("file_organization" in p[0] or "document" in p[0]
               for p in ws.skill_packs), \
        "relevant pack not selected: %s" % ws.skill_packs

    # procedure slot: prime with the EXACT learned goal shape
    b2 = primer.prime("create a folder called phase19-probe-gamma",
                      perception=snap)
    print("   procedure-goal prime: procedures=%s" % [
        p["goal"][:40] for p in b2.workspace.procedures], flush=True)
    assert b2.workspace.procedures, "procedure slot empty"

    # ---------- 5. deictic referent + ambiguity ----------
    section(4, "DEICTIC REFERENT (what is this?)")
    b3 = primer.prime("what is this on screen", perception=snap)
    ref, corr = b3.workspace.referent, b3.workspace.correlation(
        "deixis_referent")
    print("   referent=%s ambiguous=%s valid=%s" % (
        ref, b3.workspace.referent_ambiguous,
        corr and corr.get("valid")), flush=True)
    assert corr is not None, "deixis correlation missing"
    if ref is None:
        assert b3.workspace.referent_ambiguous, \
            "neither resolved nor reported ambiguous"

    # ---------- 6. continuity across turns ----------
    section(5, "SAME-SESSION CONTINUITY")
    print("   continuity=%s" % b3.workspace.continuity, flush=True)
    assert b3.workspace.continuity.get("continued"), "continuity lost"

    # ---------- 7. live hand correlation (optional) ----------
    section(6, "LIVE HAND -> VISUAL CORRELATION")
    hand_ok = False
    if want_hand:
        tracker = HandTracker(bus=HandStateBus(), camera_index=1)
        if tracker.start_camera():
            primer.hand_bus = tracker.bus
            primer.hand_tracker = tracker
            print("   camera started; SHOW AN OPEN HAND, then POINT at the "
                  "screen for ~60s", flush=True)
            # Sample the tracker cheaply and only pay for priming when a
            # stable point exists: pointing windows are short, and a full
            # prime per sample is slow enough to miss them.
            t0 = time.time()
            seen_point = False
            while time.time() - t0 < 150:
                g, votes = tracker.stable()
                if g == Gesture.POINTING and votes >= 3:
                    if not seen_point:
                        print("   stable pointing observed (votes=%d); "
                              "capturing relation" % votes, flush=True)
                        seen_point = True
                    snap_live = engine._observe_for_task(
                        PerceptionLevel.LEVEL_2_UIA_TREE)
                    bb = primer.prime("what is this", perception=snap_live)
                    w = bb.workspace
                    rel = w.correlation("hand->visual")
                    if rel and rel.get("valid"):
                        print("   LIVE: pointing votes=%d target=%r kind=%s "
                              "prov=%s referent=%s" % (
                                  votes, w.pointed_element.get("element"),
                                  w.pointed_element.get("kind"),
                                  w.pointed_element.get("provenance"),
                                  (w.referent or {}).get("name")), flush=True)
                        hand_ok = True
                        break
                time.sleep(0.2)
            if not hand_ok:
                print("   no VALID live hand->visual correlation in the "
                      "window (pointing_seen=%s)" % seen_point, flush=True)
            if not hand_ok:
                print("   no stable live hand point observed in the window",
                      flush=True)
            tracker.shutdown()
        else:
            print("   camera unavailable: %s" % tracker.last_error,
                  flush=True)
    else:
        print("   skipped (pass 'hand' to prove live hand correlation)",
              flush=True)

    # ---------- 8. stale rejection ----------
    section(7, "STALE EVIDENCE REJECTED")
    vbus.stale_after_seconds = 0.001
    time.sleep(0.02)
    bs = primer.prime(goal, perception=snap)
    print("   visual source=%s degraded=%s" % (
        bs.workspace.status()["sources"]["visual"],
        bs.workspace.degraded), flush=True)
    assert bs.workspace.status()["sources"]["visual"] != "AVAILABLE"
    assert bs.workspace.correlation("hand->visual") is None or \
        not bs.workspace.correlation("hand->visual")["valid"]
    vbus.stale_after_seconds = 30.0

    # ---------- 9. front door ----------
    section(8, "COGNITIVE FRONT DOOR RECEIVES WORKSPACE")
    router = IntelligenceRouter(procedure_store=store, memory=memory)
    cfd = create_cognitive_front_door(capability_bus=cap,
                                      agent_state=AgentState(),
                                      intelligence_router=router,
                                      memory=memory)
    cfd.context_primer = primer
    it = cfd.process_turn(goal, snap, {})
    print("   mode=%s workspace=%s referent=%s" % (
        it.intent_kind.value,
        "attached" if getattr(it.context_bundle, "workspace", None)
        else "missing",
        (it.context_bundle.workspace.referent or {}).get("name")
        if it.context_bundle and it.context_bundle.workspace else None),
        flush=True)
    assert it.context_bundle is not None
    assert it.context_bundle.workspace is not None
    sm = cfd._self_model.get("phase19", {})
    print("   self-model phase19=%s" % sm, flush=True)
    assert "shared_workspace" in cfd._self_model.get(
        "capabilities_available", [])
    r = cap.request("SHARED_WORKSPACE", {"op": "describe"})
    print("   capability: %s" % r["data"]["describe"][:160], flush=True)
    assert r["success"]

    for n in ("phase19-probe-alpha", "phase19-probe-beta",
              "phase19-probe-gamma"):
        shutil.rmtree(os.path.join(os.path.expanduser("~"), "Desktop", n),
                      ignore_errors=True)
    print("\nPROOF PASS%s" % (
        " (incl. live hand)" if hand_ok else
        " (hand correlation not observed live)"), flush=True)


if __name__ == "__main__":
    main()
