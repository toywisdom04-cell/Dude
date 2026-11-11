#!/usr/bin/env python3
"""
Test the ScreenMap.available() fix with more detailed diagnostics
"""

import sys
import time
import threading

print("=" * 80)
print("TESTING SCREENMAP.AVAILABLE() FIX - DETAILED DIAGNOSTICS")
print("=" * 80)

# Import all required modules
print("\n1. Importing modules")
print("-" * 50)

# Check if uiautomation is available
try:
    import uiautomation as auto
    print("✓ uiautomation imported successfully")
    _UIA = True
except Exception as e:
    print(f"✗ uiautomation import failed: {e}")
    _UIA = False

# Import core.tools
from core import tools
print(f"✓ core.tools imported successfully")
print(f"  Initial _SCREENTREE: {tools._SCREENTREE}")

# Import ScreenMap
from core.screentree import ScreenMap
print(f"✓ ScreenMap imported successfully")

# 2. Create ScreenMap (as in dude.py:721)
print("\n2. Creating ScreenMap")
print("-" * 50)
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")
print(f"available(): {screentree.available()}")
print(f"_built_at: {screentree._built_at}")

# 3. Call init_screentree (as in dude.py:722)
print("\n3. Calling init_screentree")
print("-" * 50)
print(f"Before init_screentree: core.tools._SCREENTREE = {tools._SCREENTREE}")
print(f"Before init_screentree: screentree is _SCREENTREE = {screentree is tools._SCREENTREE}")

from core.tools import init_screentree
init_screentree(screentree)

print(f"After init_screentree: core.tools._SCREENTREE = {tools._SCREENTREE}")
print(f"After init_screentree: screentree is _SCREENTREE = {screentree is tools._SCREENTREE}")
print(f"After init_screentree: _SCREENTREE.available() = {tools._SCREENTREE.available() if tools._SCREENTREE else 'None'}")

# 4. Test ui_scan after waiting
print("\n4. Testing after waiting")
print("-" * 50)
print("Waiting 3 seconds for thread initialization...")
time.sleep(3)

print(f"After wait:")
print(f"  screentree.available(): {screentree.available()}")
print(f"  screentree._built_at: {screentree._built_at}")
print(f"  core.tools._SCREENTREE.available(): {tools._SCREENTREE.available() if tools._SCREENTREE else 'None'}")

# 5. Test ui_scan
print("\n5. Testing ui_scan()")
print("-" * 50)
from core.tools import ui_scan

result = ui_scan(None, {})
print(f"ui_scan() result: {result[:200] if len(result) > 200 else result}...")

if "UI Automation screen map is not active" in result:
    print("ERROR: ui_scan() still failing")
    print(f"  _SCREENTREE is None: {tools._SCREENTREE is None}")
    print(f"  _SCREENTREE.available(): {tools._SCREENTREE.available() if tools._SCREENTREE else 'N/A'}")
else:
    print("SUCCESS: ui_scan() returned UI data")

print("\n" + "=" * 80)
print("TEST COMPLETE")
print("=" * 80)