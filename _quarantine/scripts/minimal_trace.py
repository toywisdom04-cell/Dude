#!/usr/bin/env python3
"""
Minimal trace to identify _SCREENTREE issue
"""

print("=" * 80)
print("MINIMAL TRACE - UI AUTOMATION DIAGNOSTIC")
print("=" * 80)

# 1. CHECK INITIAL STATE
print("\n1. INITIAL STATE")
print("-" * 50)
import core.tools
print(f"core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"core.tools._UI_CMD: {core.tools._UI_CMD}")

# 2. CREATE SCREENMAP
print("\n2. CREATE SCREENMAP")
print("-" * 50)
from core.screentree import ScreenMap
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")
print(f"available(): {screentree.available()}")
print(f"_built_at: {screentree._built_at}")

# 3. CALL init_screentree
print("\n3. CALL init_screentree()")
print("-" * 50)
print(f"Before: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

core.tools.init_screentree(screentree)

print(f"After: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

# 4. TEST ui_scan
print("\n4. TEST ui_scan()")
print("-" * 50)
print(f"Before ui_scan(): core.tools._SCREENTREE = {core.tools._SCREENTREE}")

from core.tools import ui_scan
result = ui_scan(None, {})

print(f"ui_scan() result: {result[:100] if len(result) > 100 else result}...")

# 5. FINAL STATE
print("\n5. FINAL STATE")
print("-" * 50)
print(f"core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")
print(f"screentree.available(): {screentree.available()}")
print(f"screentree._thread.is_alive(): {screentree._thread.is_alive()}")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)