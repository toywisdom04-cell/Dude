#!/usr/bin/env python3
"""
Test the fix for ScreenMap.available()
"""

import sys
import time

print("=" * 80)
print("TESTING SCREENMAP.AVAILABLE() FIX")
print("=" * 80)

# Import ScreenMap
from core.screentree import ScreenMap

print("\n1. Creating ScreenMap")
print("-" * 50)
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")
print(f"available(): {screentree.available()}")
print(f"_built_at: {screentree._built_at}")

print("\n2. Waiting for thread initialization")
print("-" * 50)
time.sleep(3)

print(f"After wait:")
print(f"available(): {screentree.available()}")
print(f"_built_at: {screentree._built_at}")
print(f"Thread alive: {screentree._thread.is_alive()}")

print("\n3. Testing current_view()")
print("-" * 50)
try:
    view = screentree.current_view(limit=5)
    print(f"current_view() SUCCESS:")
    print(f"  View length: {len(view)}")
    print(f"  Has 'ACTIVE APP:': {'ACTIVE APP:' in view}")
    print(f"  Controls: {len(screentree._rows) if hasattr(screentree, '_rows') else 'N/A'}")

    if "ACTIVE APP:" in view:
        print("\n✓ SUCCESS: ScreenMap is working correctly!")
        print("  The fix ensures available() only returns True when the thread is actually working")
    else:
        print("\n⚠ WARNING: View doesn't contain expected content")
except Exception as e:
    print(f"current_view() FAILED: {type(e).__name__}: {e}")

print("\n4. Testing ui_scan()")
print("-" * 50)

# Test ui_scan
from core.tools import ui_scan
result = ui_scan(None, {})

print(f"ui_scan() result: {result[:200] if len(result) > 200 else result}...")

if "UI Automation screen map is not active" in result:
    print("ERROR: ui_scan() still failing")
else:
    print("SUCCESS: ui_scan() returned UI data")

print("\n" + "=" * 80)
print("TEST COMPLETE")
print("=" * 80)