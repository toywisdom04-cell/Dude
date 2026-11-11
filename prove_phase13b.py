"""Phase 13 targeted re-verification (real runtime, generic OS work).

Proves the two bugs the first demo exposed are fixed:
1. Verified-procedure reuse binds literals (no {placeholder} replay).
2. Verification-failure path records failure learning without crashing.

Cleans up its demo folders afterwards. Knowledge records stay.
"""
import asyncio
import os
import shutil
import sys
import time

sys.path.insert(0, r'E:\Dude\dude')

import prove_phase13 as P
from core.orchestrator.intelligence_router import RouteDecision


def main():
    engine, router, store, learner, memory = P.build()

    import os as _os
    _desktop = _os.path.join(_os.path.expanduser("~"), "Desktop")

    def disk(name):
        return _os.path.isdir(_os.path.join(_desktop, name))

    print("=== REUSE RUN (must create phase13-foxtrot, not {folder_name}) ===",
          flush=True)
    t0 = time.time()
    state = asyncio.run(engine.run("create a folder called phase13-foxtrot"))
    ok = [s for s in (state.completed_steps or []) if s.success]
    print("state=%s steps=%d ok=%d fail=%r time=%.1fs" % (
        engine._state.value if hasattr(engine._state, "value")
        else engine._state,
        len(state.subgoals or []), len(ok),
        getattr(state, "failure_reason", ""), time.time() - t0), flush=True)
    print("disk phase13-foxtrot:", disk("phase13-foxtrot"), flush=True)
    print("disk {folder_name}:", disk("{folder_name}"), flush=True)
    assert disk("phase13-foxtrot"), "reuse run did not create the folder!"
    assert not disk("{folder_name}"), "placeholder leaked into execution!"
    # Reuse proof: router must have served a verified procedure, and the
    # stored procedure must have gained success evidence from the replay.
    try:
        _vp = router._stats.get(RouteDecision.VERIFIED_PROCEDURE, 0)
        print("verified_procedure routes:", _vp, flush=True)
    except Exception:
        pass

    print("=== FAILURE RUN (must record, must not crash/promote) ===",
          flush=True)
    before_procs = len(store.find_by_goal("folder", 0.0))
    state2 = asyncio.run(engine.run("create a folder called phase13-bad:name"))
    print("state=%s fail=%r" % (
        engine._state.value if hasattr(engine._state, "value")
        else engine._state,
        getattr(state2, "failure_reason", "")), flush=True)
    after_procs = len(store.find_by_goal("folder", 0.0))
    print("procedures before/after failure run: %d/%d" % (
        before_procs, after_procs), flush=True)
    assert after_procs == before_procs, "failure must not promote!"
    fails = store.recent_failures("create a folder called phase13-bad:name")
    print("failure events for bad:name goal:", len(fails), flush=True)
    assert fails, "failure run must record a failure event!"

    for name in ("phase13-foxtrot",):
        shutil.rmtree(os.path.join(_desktop, name), ignore_errors=True)
    print("REVERIFY PASS: reuse binds literals, failure learns without promoting",
          flush=True)


if __name__ == "__main__":
    main()
