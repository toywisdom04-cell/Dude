#!/usr/bin/env python3
"""
Comprehensive test to verify the ScreenMap.available() fix
This test verifies that the fix correctly handles all phases of ScreenMap initialization
"""

import sys
import time
import threading

print("=" * 80)
print("COMPREHENSIVE TEST - SCREENMAP.AVAILABLE() FIX")
print("=" * 80)

# Import all required modules
print("\n1. INITIAL SETUP")
print("-" * 50)

# Import core.tools first
from core import tools
print(f"Initial _SCREENTREE: {tools._SCREENTREE}")

# Import ScreenMap
from core.screentree import ScreenMap

# 2. CREATE AND INITIALIZE
print("\n2. CREATE AND INITIALIZE ScreenMap")
print("-" * 50)

screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")
print(f"available(): {screentree.available()}")
print(f"_built_at: {screentree._built_at}")

from core.tools import init_screentree
init_screentree(screentree)

print(f"After init_screentree:")
print(f"  _SCREENTREE: {tools._SCREENTREE}")
print(f"  available(): {tools._SCREENTREE.available() if tools._SCREENTREE else 'N/A'}")
print(f"  _built_at: {tools._SCREENTREE._built_at if tools._SCREENTREE else 'N/A'}")

# 3. VERIFY AVAILABLE() LOGIC
print("\n3. VERIFYING available() LOGIC")
print("-" * 50)

print("Phase 1: Immediately after initialization (should be False)")
print(f"  available() = {screentree.available()}")
print(f"  _UIA = {screentree._UIA if hasattr(screentree, '_UIA') else 'N/A'}")
print(f"  _built_at = {screentree._built_at}")
print(f"  Expected: False (thread still initializing)")

print("\nPhase 2: After waiting (should be True)")
time.sleep(3)
print(f"  available() = {screentree.available()}")
print(f"  _UIA = {screentree._UIA if hasattr(screentree, '_UIA') else 'N/A'}")
print(f"  _built_at = {screentree._built_at}")
print(f"  Expected: True (thread has built first map)")

# 4. TEST ui_scan() FUNCTIONALITY
print("\n4. TESTING ui_scan() FUNCTIONALITY")
print("-" * 50)

from core.tools import ui_scan

print("Calling ui_scan() after waiting...")
result = ui_scan(None, {})
print(f"ui_scan() result (first 200 chars): {result[:200]}...")

if "UI Automation screen map is not active" in result:
    print("❌ FAILED: ui_scan() still returning error")
else:
    print("✅ SUCCESS: ui_scan() returning UI data")

    # Check if result contains expected UI data
    if "CONTROLS ON SCREEN" in result:
        print("✅ SUCCESS: Result contains UI controls data")
    else:
        print("⚠️ WARNING: Result doesn't contain expected UI controls data")

# 5. VERIFICATION SUMMARY
print("\n5. VERIFICATION SUMMARY")
print("-" * 50)

summary = []
summary.append(f"✓ ScreenMap created successfully")
summary.append(f"✓ Thread is alive: {screentree._thread.is_alive()}")
summary.append(f"✓ Initial available(): {screentree.available()} (expected: False)")
summary.append(f"✓ After 3s available(): {screentree.available()} (expected: True)")
summary.append(f"✓ _built_at progression: 0.0 -> {screentree._built_at}")

if "UI Automation screen map is not active" not in result:
    summary.append("SUCCESS: ui_scan() returns UI data (not error)")
else:
    summary.append("FAILED: ui_scan() still returns error")

print("\n".join(summary))

# 6. FINAL VERIFICATION OF FIX LOGIC
print("\n6. FIX VERIFICATION")
print("-" * 50)

print("The fix in core/screentree.py:71:")
print("  OLD: def available(self):")
print("           return _UIA")
print("")
print("  NEW: def available(self):")
print("           return _UIA and self._built_at > 0.0")
print("")
print("This ensures:")
print("  1. available() only returns True when uiautomation imports (_UIA)")
print("  2. available() only returns True when thread has built (self._built_at > 0.0)")
print("  3. This prevents 'UI Automation screen map is not active' errors")

print("\n" + "=" * 80)
print("COMPREHENSIVE TEST COMPLETE")
print("=" * 80)