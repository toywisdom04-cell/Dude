#!/usr/bin/env python3
"""
Final UI Diagnostic - trace _SCREENTREE lifecycle
"""

import sys
import time

print("=" * 80)
print("UI AUTOMATION DIAGNOSTIC - _SCREENTREE LIFECYCLE")
print("=" * 80)

# Import and trace
import core.tools
from core.screentree import ScreenMap

print("\n1. INITIAL STATE")
print("-" * 50)
print(f"core.tools._SCREENTREE initial: {core.tools._SCREENTREE}")

print("\n2. CREATE SCREENMAP")
print("-" * 50)
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")
print(f"available(): {screentree.available()}")

print("\n3. CALL init_screentree()")
print("-" * 50)
print(f"Before: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

core.tools.init_screentree(screentree)

print(f"After: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

print("\n4. TEST ui_scan()")
print("-" * 50)
print(f"Before ui_scan(): core.tools._SCREENTREE = {core.tools._SCREENTREE}")

from core.tools import ui_scan
result = ui_scan(None, {})

print(f"ui_scan() result: {result[:100] if len(result) > 100 else result}")

if "UI Automation screen map is not active" in result:
    print("ERROR: ui_scan() failed - _SCREENTREE is None or not available")

    if core.tools._SCREENTREE is None:
        print("  DIAGNOSIS: _SCREENTREE was reset to None after init_screentree()")
    elif not screentree.available():
        print("  DIAGNOSIS: ScreenMap.available() returns False")
    else:
        print("  DIAGNOSIS: _SCREENTREE is set but ui_scan() still fails")
else:
    print("SUCCESS: ui_scan() returned UI data")

print("\n5. FINAL STATE")
print("-" * 50)
print(f"core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")
print(f"screentree.available(): {screentree.available()}")
print(f"screentree._thread.is_alive(): {screentree._thread.is_alive()}")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)