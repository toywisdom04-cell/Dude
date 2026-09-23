#!/usr/bin/env python3
"""
Simple diagnostic to answer the core questions:
1. Which module owns _SCREENTREE?
2. Is the ScreenMap worker actually operational?
"""

import sys
import time
import threading

print("=" * 80)
print("SIMPLE UI AUTOMATION DIAGNOSTIC")
print("=" * 80)

# 1. FIND MODULE OWNING _SCREENTREE
print("\n1. FIND MODULE OWNING _SCREENTREE")
print("-" * 50)

# Import core.tools first
import core.tools

print(f"Core.tools module: {core.tools}")
print(f"Module file: {getattr(core.tools, '__file__', 'N/A')}")
print(f"_SCREENTREE in core.tools: {core.tools._SCREENTREE}")
print(f"_UI_CMD in core.tools: {core.tools._UI_CMD}")

# Check for other modules with _SCREENTREE
print("\nOther modules with _SCREENTREE:")
for name, module in sys.modules.items():
    if hasattr(module, '_SCREENTREE') and module is not core.tools:
        print(f"  {name}: _SCREENTREE = {module._SCREENTREE}")

# 2. TRACE init_screentree()
print("\n\n2. TRACE init_screentree()")
print("-" * 50)

original_init = core.tools.init_screentree
call_count = 0

def traced_init(map_):
    global call_count
    call_count += 1
    print(f"  Call #{call_count} to init_screentree()")
    print(f"    Thread: {threading.current_thread().name}")
    print(f"    Map: {map_}")
    print(f"    Before _SCREENTREE: {core.tools._SCREENTREE}")

    result = original_init(map_)

    print(f"    After _SCREENTREE: {core.tools._SCREENTREE}")
    print(f"    Map is _SCREENTREE: {map_ is core.tools._SCREENTREE}")

    return result

core.tools.init_screentree = traced_init

# 3. CREATE ScreenMap
print("\n\n3. CREATE ScreenMap")
print("-" * 50)

from core.screentree import ScreenMap

print("Creating ScreenMap...")
screentree_start = time.time()
screentree = ScreenMap()
creation_time = time.time() - screentree_start
print(f"ScreenMap created in {creation_time:.4f} seconds")

# Check if thread is alive and working
if hasattr(screentree, '_thread') and screentree._thread:
    thread = screentree._thread
    print(f"\nThread info:")
    print(f"  Name: {thread.name}")
    print(f"  Ident: {thread.ident}")
    print(f"  Is alive: {thread.is_alive()}")
    print(f"  Is daemon: {thread.daemon}")

    # Let it work
    print(f"\nWaiting 2 seconds for thread initialization...")
    time.sleep(2)

    print(f"After wait:")
    print(f"  Is alive: {thread.is_alive()}")

    # Check actual functionality
    print(f"\nTesting functionality:")
    try:
        view = screentree.current_view(limit=5)
        print(f"  current_view() SUCCESS:")
        print(f"    View length: {len(view)}")
        print(f"    Has 'ACTIVE APP:': {'ACTIVE APP:' in view}")
        print(f"    Controls count: {len(screentree._rows) if hasattr(screentree, '_rows') else 'N/A'}")

    except Exception as e:
        print(f"  current_view() FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

else:
    print(f"ERROR: No thread in ScreenMap")

# 4. FINAL DIAGNOSIS
print("\n\n4. FINAL DIAGNOSIS")
print("-" * 50)

print("\nAnswers to core questions:")
print(f"1. Which module owns _SCREENTREE?")
print(f"   → core.tools module")
print(f"   _SCREENTREE value: {core.tools._SCREENTREE}")

print(f"\n2. Is the ScreenMap worker operational?")
if hasattr(screentree, '_thread') and screentree._thread and screentree._thread.is_alive():
    try:
        view = screentree.current_view(limit=3)
        if view and "ACTIVE APP:" in view:
            print(f"   → YES - Thread is alive and returning data (controls: {len(screentree._rows) if hasattr(screentree, '_rows') else 0})")
        else:
            print(f"   → PARTIAL - Thread is alive but may not be fully functional")
    except Exception as e:
        print(f"   → NO - Thread is alive but throwing: {e}")
else:
    print(f"   → NO - Thread is not alive or doesn't exist")

print(f"\nConclusion:")
print(f"- _SCREENTREE is owned by core.tools module")
print(f"- The ScreenMap worker thread is {'alive' if (hasattr(screentree, '_thread') and screentree._thread and screentree._thread.is_alive()) else 'NOT alive'}")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)