#!/usr/bin/env python3
"""
Simple final test to verify the ScreenMap.available() fix
"""

import sys
import time

print("=" * 80)
print("SIMPLE FINAL TEST - SCREENMAP.AVAILABLE() FIX")
print("=" * 80)

# Import core.tools and ScreenMap
from core import tools
from core.screentree import ScreenMap

# Create ScreenMap
print("\nCreating ScreenMap...")
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"available(): {screentree.available()} (expected: False)")

# Initialize
from core.tools import init_screentree
init_screentree(screentree)
print(f"After init_screentree: available() = {tools._SCREENTREE.available()}")

# Wait for thread to start working
print("\nWaiting 3 seconds for thread initialization...")
time.sleep(3)

print(f"After wait: available() = {screentree.available()} (expected: True)")

# Test ui_scan
print("\nTesting ui_scan()...")
from core.tools import ui_scan
result = ui_scan(None, {})

print(f"ui_scan() result (first 200 chars): {result[:200]}...")

if "UI Automation screen map is not active" in result:
    print("FAILED: ui_scan() still returns error")
    sys.exit(1)
else:
    print("SUCCESS: ui_scan() returns UI data")

print("\n" + "=" * 80)
print("SIMPLE FINAL TEST PASSED - FIX VERIFIED")
print("=" * 80)