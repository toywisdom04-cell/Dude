#!/usr/bin/env python3
"""
Simple analysis of UI Automation issue
"""

import sys
import time
import threading

print("=" * 80)
print("UI AUTOMATION ANALYSIS")
print("=" * 80)

# 1. CHECK core.tools._SCREENTREE
print("\n1. CHECK core.tools._SCREENTREE")
print("-" * 50)

import core.tools
print(f"core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"core.tools._UI_CMD: {core.tools._UI_CMD}")

# 2. CHECK init_screentree
print("\n\n2. CHECK init_screentree()")
print("-" * 50)

original_init = core.tools.init_screentree
call_count = 0

def traced_init(map_):
    global call_count
    call_count += 1
    print(f"init_screentree() call #{call_count}")
    print(f"  Map: {map_}")
    print(f"  Before _SCREENTREE: {core.tools._SCREENTREE}")

    result = original_init(map_)

    print(f"  After _SCREENTREE: {core.tools._SCREENTREE}")
    print(f"  Map is _SCREENTREE: {map_ is core.tools._SCREENTREE}")

    return result

core.tools.init_screentree = traced_init

# 3. CREATE ScreenMap
print("\n\n3. CREATE ScreenMap")
print("-" * 50)

from core.screentree import ScreenMap

print("Creating ScreenMap...")
screentree = ScreenMap()

# Check if thread is alive
if hasattr(screentree, '_thread') and screentree._thread:
    thread = screentree._thread
    print(f"Thread alive: {thread.is_alive()}")

    # Test functionality
    print("Testing current_view()...")
    try:
        view = screentree.current_view(limit=5)
        print(f"current_view() SUCCESS")
        print(f"  View length: {len(view)}")
        print(f"  Has 'ACTIVE APP:': {'ACTIVE APP:' in view}")
        print(f"  Controls count: {len(screentree._rows) if hasattr(screentree, '_rows') else 'N/A'}")

    except Exception as e:
        print(f"current_view() FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

# 4. FINAL DIAGNOSIS
print("\n\n4. FINAL DIAGNOSIS")
print("-" * 50)

print(f"init_screentree() calls: {call_count}")
print(f"core.tools._SCREENTREE final value: {core.tools._SCREENTREE}")
print(f"ScreenMap thread alive: {screentree._thread.is_alive() if hasattr(screentree, '_thread') and screentree._thread else 'N/A'}")

if screentree._thread and screentree._thread.is_alive():
    try:
        view = screentree.current_view(limit=3)
        if view and "ACTIVE APP:" in view:
            print(f"UI Automation: WORKING (thread alive, data returned)")
        else:
            print(f"UI Automation: PARTIAL (thread alive but may not be fully functional)")
    except Exception as e:
        print(f"UI Automation: ISSUE (thread alive but throwing): {e}")
else:
    print(f"UI Automation: NOT WORKING (thread not alive)")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)