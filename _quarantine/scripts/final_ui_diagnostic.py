#!/usr/bin/env python3
"""
Final UI Automation diagnostic
Answers the core questions:
1. Which module owns _SCREENTREE?
2. Is the ScreenMap worker actually operational?
"""

import sys
import time
import threading
import traceback

print("=" * 80)
print("FINAL UI AUTOMATION DIAGNOSTIC")
print("=" * 80)

# 1. FIND THE MODULE THAT OWNS _SCREENTREE
print("\n1. FIND THE MODULE THAT OWNS _SCREENTREE")
print("-" * 50)

# Get core.tools module directly
core_tools_module = sys.modules['core.tools']
print(f"Core.tools module: {core_tools_module}")
print(f"Module file: {getattr(core_tools_module, '__file__', 'N/A')}")
print(f"_SCREENTREE in core.tools: {core_tools_module._SCREENTREE}")
print(f"_UI_CMD in core.tools: {core_tools_module._UI_CMD}")

# Check if there are other modules that might have _SCREENTREE
print("\nChecking for _SCREENTREE in other modules:")
for module_name, module_obj in sys.modules.items():
    if hasattr(module_obj, '_SCREENTREE'):
        print(f"  Module {module_name}:")
        print(f"    _SCREENTREE: {module_obj._SCREENTREE}")
        print(f"    Same as core.tools: {module_obj is core_tools_module}")

# 2. TRACE init_screentree()
print("\n\n2. TRACE init_screentree()")
print("-" * 50)

# Monkey-patch to trace calls
original_init_screentree = core_tools_module.init_screentree
call_count = 0

def traced_init_screentree(map_):
    global call_count
    call_count += 1

    print(f"  init_screentree() CALL #{call_count}")
    print(f"    Thread: {threading.current_thread().name}")
    print(f"    Map object: {map_}")
    print(f"    Map type: {type(map_).__name__}")
    print(f"    Map has _rows: {hasattr(map_, '_rows')}")
    print(f"    Map has _thread: {hasattr(map_, '_thread')}")
    print(f"    Before: _SCREENTREE = {core_tools_module._SCREENTREE}")

    result = original_init_screentree(map_)

    print(f"    After: _SCREENTREE = {core_tools_module._SCREENTREE}")
    print(f"    Same object: {map_ is core_tools_module._SCREENTREE}")

    return result

core_tools_module.init_screentree = traced_init_screentree

# 3. CREATE ScreenMap AND TRACE
print("\n\n3. CREATE ScreenMap AND TRACE")
print("-" * 50)

# Import and create ScreenMap
from core.screentree import ScreenMap

print("Creating ScreenMap...")
screentree_start = time.time()
screentree = ScreenMap()
creation_time = time.time() - screentree_start
print(f"ScreenMap created in {creation_time:.4f} seconds")

# Check thread status
print(f"\nScreenMap thread status:")
if hasattr(screentree, '_thread') and screentree._thread:
    thread = screentree._thread
    print(f"  Thread: {thread.name}")
    print(f"  Thread ident: {thread.ident}")
    print(f"  Thread is_alive: {thread.is_alive()}")
    print(f"  Thread daemon: {thread.daemon}")

    # Let thread work for a bit
    print(f"\nWaiting 3 seconds for thread to initialize...")
    time.sleep(3)

    print(f"After wait:")
    print(f"  Thread is_alive: {thread.is_alive()}")

    # Check internal state
    print(f"\nInternal ScreenMap state:")
    print(f"  _rows count: {len(screentree._rows) if hasattr(screentree, '_rows') else 'N/A'}")
    print(f"  _app: {screentree._app}")
    print(f"  _title: {screentree._title}")
    print(f"  _built_at: {screentree._built_at}")

    # Test actual functionality
    print(f"\nTesting actual functionality:")
    try:
        view = screentree.current_view(limit=5)
        print(f"  current_view() SUCCESS:")
        print(f"    View length: {len(view)}")
        print(f"    Contains 'ACTIVE APP:': {'ACTIVE APP:' in view}")
        print(f"    First 200 chars: {view[:200]}...")

        rows_count = len(screentree._rows) if hasattr(screentree, '_rows') else 0
        print(f"    Controls found: {rows_count}")

    except Exception as e:
        print(f"  current_view() FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()

else:
    print(f"ERROR: ScreenMap has no _thread attribute")

# 4. FINAL ANALYSIS
print("\n\n4. FINAL ANALYSIS")
print("-" * 50)

print(f"init_screentree() was called {call_count} times")
print(f"\nKey findings:")
print(f"1. _SCREENTREE module: core.tools")
print(f"   _SCREENTREE value: {core_tools_module._SCREENTREE}")
print(f"2. ScreenMap thread alive: {screentree._thread.is_alive() if hasattr(screentree, '_thread') and screentree._thread else 'N/A'}")

if hasattr(screentree, '_thread') and screentree._thread:
    thread = screentree._thread
    is_alive = thread.is_alive()

    if is_alive:
        print(f"   ✓ Thread is alive")
        # Try one more test
        try:
            view = screentree.current_view(limit=3)
            if view and "ACTIVE APP:" in view:
                print(f"   ✓ Thread is functional (get controls: {len(screentree._rows) if hasattr(screentree, '_rows') else 0})")
            else:
                print(f"   ⚠ Thread alive but may not be fully functional")
        except Exception as e:
            print(f"   ✗ Thread alive but throwing exceptions: {type(e).__name__}: {e}")
    else:
        print(f"   ✗ Thread is NOT alive")

print(f"\nConclusion:")
if hasattr(screentree, '_thread') and screentree._thread and screentree._thread.is_alive():
    try:
        view = screentree.current_view(limit=3)
        if view and "ACTIVE APP:" in view:
            print(f"UI Automation is operational - ScreenMap worker is working")
        else:
            print(f"UI Automation may have issues - ScreenMap thread is alive but not returning data")
    except Exception as e:
        print(f"UI Automation has issues - ScreenMap thread is alive but throwing: {e}")
else:
    print(f"UI Automation is NOT operational - ScreenMap thread is not alive")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)