"""Phase 18 live proof: human semantic memory + jobs on the real DB.

Real Memory (the live dude_data/memory.db), real job_memory, real
ContextPrimer, real CognitiveFrontDoor. Proves:
  1. a stated fact is remembered with provenance + confidence
  2. an updated statement SUPERSEDES the old one (no stale answer)
  3. history is preserved, current belief is single
  4. job confidence moves only with verified outcomes
  5. a session later re-primes and receives the CURRENT human context
Uses a clearly namespaced probe subject; no user files are touched.
"""
import os
import sys

sys.path.insert(0, r'E:\Dude\dude')
os.chdir(r'E:\Dude\dude')

from core.memory import Memory
from core import human_memory as hm
from core import job_memory as jm
from core.capability_bus import CapabilityBus
from core.orchestrator.context_primer import ContextPrimer

PROBE = "phase18_probe_home_city"


def main():
    mem = Memory()
    print("DB: %s" % mem.cfg.db_path(), flush=True)
    bus = CapabilityBus()
    bus.attach(memory=mem)

    # 1. remember an initial belief
    r = bus.request("HUMAN_MEMORY", {
        "op": "remember", "fact": "phase18 probe: works in Rome",
        "category": "personal", "subject": PROBE, "confidence": 0.6})
    print("REMEMBER-1: %s" % r, flush=True)
    assert r["success"]

    rows = hm.recall_about_human(mem, "phase18 probe")
    print("RECALL-1: %s" % [(x["fact"], x["confidence"]) for x in rows],
          flush=True)
    assert len(rows) == 1 and "Rome" in rows[0]["fact"]

    # 2. the human's situation changes
    r2 = bus.request("HUMAN_MEMORY", {
        "op": "remember", "fact": "phase18 probe: works in Oslo",
        "category": "personal", "subject": PROBE, "confidence": 0.9})
    print("REMEMBER-2: %s" % r2, flush=True)
    rows2 = hm.recall_about_human(mem, "phase18 probe")
    print("RECALL-2: %s" % [(x["fact"], x["confidence"]) for x in rows2],
          flush=True)
    assert len(rows2) == 1, "stale belief still presented!"
    assert "Oslo" in rows2[0]["fact"], "current belief not updated!"

    # 3. history retained
    hist = hm.fact_history(mem, PROBE)
    print("HISTORY: %s" % [(h["fact"], h["status"]) for h in hist], flush=True)
    assert len(hist) >= 2
    assert any("superseded" in h["status"] for h in hist)

    # 4. job confidence only moves on verified outcomes
    jm.upsert_job(mem, "phase18 probe job", purpose="probe job",
                  confidence=0.4)
    c0 = jm.get_job(mem, "phase18 probe job")["confidence"]
    c1 = jm.record_outcome(mem, "phase18 probe job", True)["confidence"]
    c2 = jm.record_outcome(mem, "phase18 probe job", False)["confidence"]
    print("JOB CONFIDENCE: start=%s +success=%s +failure=%s" % (
        c0, c1, c2), flush=True)
    assert c1 > c0 and c2 < c1

    # 5. the job index is still valid JSON after the writes
    import json
    json.loads(mem.get_state(jm._STATE_KEY, "{}"))
    print("JOB INDEX: valid JSON", flush=True)

    # 6. a fresh priming pass (as a later session would do) receives the
    #    CURRENT human context, not the superseded one.
    primer = ContextPrimer(memory=mem)
    b = primer.prime("phase18 probe works")
    facts = " ".join(str(m.get("fact", "")) for m in b.memory)
    print("PRIMED FACTS: %s" % facts, flush=True)
    assert "Oslo" in facts and "Rome" not in facts, \
        "primer presented a superseded belief!"
    prof = hm.build_profile(mem)
    print("PROFILE SECTIONS: %s" % {
        k: len(v) for k, v in prof["sections"].items()}, flush=True)
    print("PROOF PASS: remember -> supersede -> recall current -> prime",
          flush=True)


if __name__ == "__main__":
    main()
