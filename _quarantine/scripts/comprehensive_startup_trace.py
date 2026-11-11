#!/usr/bin/env python3
"""
Comprehensive trace of _SCREENTREE lifecycle during normal DUDE startup
This script follows the exact instructions to diagnose the UI Automation issue
"""

import sys
import time
import threading

print("=" * 80)
print("COMPREHENSIVE STARTUP TRACE - UI AUTOMATION DIAGNOSTIC")
print("=" * 80)

# STEP 1: FIND ALL _SCREENTREE ASSIGNMENTS
print("\n1. FIND ALL _SCREENTREE ASSIGNMENTS")
print("-" * 50)

assignments = []

# Search core/tools.py
with open('E:\Dude\dude\core\tools.py', 'r') as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if '_SCREENTREE =' in line and not line.strip().startswith('#'):
            assignments.append({
                'file': 'core/tools.py',
                'line_num': i + 1,
                'line': line.strip(),
                'type': 'ASSIGNMENT'
            })

print(f"Found {len(assignments)} _SCREENTREE assignments in production code:")
for a in assignments:
    print(f"  Line {a['line_num']}: {a['line']}")

# STEP 2: TRACE NORMAL STARTUP
print("\n\n2. TRACE NORMAL DUDE STARTUP")
print("-" * 50)

print("Simulating normal DUDE startup sequence...")
print("Step 1: Import core.tools")
print("Step 2: Import ScreenMap and create instance")
print("Step 3: Call init_screentree(screenmap)")
print("Step 4: Check _SCREENTREE value")
print("Step 5: Test ui_scan()")

# Import and trace
import core.tools
from core.screentree import ScreenMap

print(f"\n1. After importing core.tools:")
print(f"   core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"   Type: {type(core.tools._SCREENTREE)}")

print(f"\n2. Creating ScreenMap...")
screentree = ScreenMap()
print(f"   ScreenMap created: {screentree}")
print(f"   Thread alive: {screentree._thread.is_alive()}")
print(f"   Thread ident: {screentree._thread.ident}")
print(f"   ScreenMap.available(): {screentree.available()}")
print(f"   ScreenMap._built_at: {screentree._built_at}")

print(f"\n3. Calling init_screentree(screenmap)...")
print(f"   Before: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"   screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

core.tools.init_screentree(screentree)

print(f"   After: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"   screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")
print(f"   Same object: {core.tools._SCREENTREE is screentree}")

# STEP 3: CHECK ui_scan() BEHAVIOR
print("\n\n3. CHECK ui_scan() BEHAVIOR")
print("-" * 50)

print(f"4. Testing ui_scan()...")
print(f"   Before ui_scan(): core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"   Before ui_scan(): screentree._built_at = {screentree._built_at}")

# Call ui_scan
from core.tools import ui_scan
result = ui_scan(None, {})

print(f"   ui_scan() result: {result[:200] if len(result) > 200 else result}...")

if "UI Automation screen map is not active" in result:
    print(f"\n   ERROR: ui_scan() returned: {result}")
    print(f"   This indicates _SCREENTREE is None or not available")
else:
    print(f"\n   SUCCESS: ui_scan() returned UI data")

# STEP 4: DETAILED STATE ANALYSIS
print("\n\n4. DETAILED STATE ANALYSIS")
print("-" * 50)

print(f"Final state:")
print(f"   core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"   core.tools._SCREENTREE is None: {core.tools._SCREENTREE is None}")
print(f"   screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")
print(f"   screentree.available(): {screentree.available()}")
print(f"   screentree._thread.is_alive(): {screentree._thread.is_alive()}")\nif screentree._thread.is_alive():
    print(f"   Thread ident: {screentree._thread.ident}")
    print(f"   Thread daemon: {screentree._thread.daemon}")

# Try one more time to see if thread works
print(f"\n5. Testing current_view() directly...")
try:
    view = screentree.current_view(limit=5)
    print(f"   current_view() SUCCESS:")
    print(f"     View length: {len(view)}")
    print(f"     Has 'ACTIVE APP:': {'ACTIVE APP:' in view}")
    print(f"     Controls: {len(screentree._rows) if hasattr(screentree, '_rows') else 'N/A'}")
except Exception as e:
    print(f"   current_view() FAILED: {type(e).__name__}: {e}")

# STEP 5: IDENTIFY THE FAILURE
print("\n\n5. IDENTIFY THE FAILURE")
print("-" * 50)

if core.tools._SCREENTREE is None:
    print(f"ISSUE IDENTIFIED: _SCREENTREE is None!")
    print(f"  This means init_screentree() did not set it correctly")
    print(f"  Or something reset it to None after init_screentree()")
else:
    print(f"ISSUE IDENTIFIED: _SCREENTREE is set but ui_scan() still fails")
    print(f"  _SCREENTREE is screentree: {core.tools._SCREENTREE is screentree}")
    print(f"  screentree.available(): {screentree.available()}")
    print(f"  screentree._thread.is_alive(): {screentree._thread.is_alive()}")

    if not screentree._thread.is_alive():
        print(f"  THREAD DIAGNOSIS: Thread is not alive")
    elif screentree.available() is False:
        print(f"  THREAD DIAGNOSIS: ScreenMap.available() returns False")
    else:
        print(f"  THREAD DIAGNOSIS: Both thread and available() look OK")

# STEP 6: FINAL DIAGNOSIS
print("\n\n6. FINAL DIAGNOSIS")
print("-" * 50)

print(f"SUMMARY:")
print(f"  Production code _SCREENTREE assignments: {len(assignments)}")
print(f"  _SCREENTREE initial value: None")
print(f"  _SCREENTREE after init_screentree(): {core.tools._SCREENTREE}")
print(f"  ui_scan() result: {'UI Automation screen map is not active' if 'UI Automation screen map is not active' in result else 'Success'}")

if core.tools._SCREENTREE is None:
    print(f"  CONCLUSION: _SCREENTREE was reset to None after init_screentree()")
elif not screentree.available():
    print(f"  CONCLUSION: ScreenMap.available() returns False - thread not properly initialized")
else:
    print(f"  CONCLUSION: Need to investigate further - both _SCREENTREE and ScreenMap appear valid")

print("\n" + "=" * 80)
print("TRACE COMPLETE")
print("=" * 80)