#!/usr/bin/env python3
"""
Clean startup trace of the exact DUDE startup sequence
"""

import sys
import time

print("=" * 80)
print("CLEAN STARTUP TRACE")
print("=" * 80)

# 1. FIRST import pattern (like dude.py line 716)
print("\n1. FIRST import pattern (like dude.py line 716)")
print("-" * 50)

# Import core.tools first (like in dude.py)
import core.tools
from core.tools import init_screentree, init_ui_cmd
print(f"1. Imported init_screentree and init_ui_cmd from core.tools")
print(f"   core.tools module: {core.tools}")
print(f"   core.tools._SCREENTREE: {core.tools._SCREENTREE}")

# Save the function object
init_screentree_func = init_screentree
print(f"   init_screentree function id: {id(init_screentree_func)}")

# 2. Create ScreenMap (like in dude.py line 721)
print("\n2. Create ScreenMap (like dude.py line 721)")
print("-" * 50)

from core.screentree import ScreenMap
print(f"2. Imported ScreenMap from core.screentree")

screentree = ScreenMap()
print(f"   ScreenMap created: {screentree}")
print(f"   ScreenMap._thread: {screentree._thread}")
print(f"   ScreenMap._thread.is_alive(): {screentree._thread.is_alive()}")

# 3. Call init_screentree (like in dude.py line 722)
print("\n3. Call init_screentree(screentree) (like dude.py line 722)")
print("-" * 50)

print(f"   Before call: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"   screentree is core.tools._SCREENTREE: {screentree is core.tools._SCREENTREE}")

# Call init_screentree
init_screentree_func(screentree)

print(f"   After call: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"   screentree is core.tools._SCREENTREE: {screentree is core.tools._SCREENTREE}")
print(f"   _SCREENTREE id: {id(core.tools._SCREENTREE)}")
print(f"   screentree id: {id(screentree)}")

# 4. Simulate the nested import (like in dude.py lines 733-741)
print("\n4. Simulate nested import (like dude.py lines 733-741)")
print("-" * 50)

# This is what happens in dude.py
print("   Simulating: if not args.no_ui:")
print("     from core.tools import init_ui_cmd")

# Import init_ui_cmd from core.tools
from core.tools import init_ui_cmd

print(f"   After import:")
print(f"     core.tools module: {core.tools}")
print(f"     core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"     screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")
print(f"     Same _SCREENTREE object: {core.tools._SCREENTREE is screentree}")

# 5. Check which init_ui_cmd function we're using
print("\n5. Check which init_ui_cmd function")
print("-" * 50)

print(f"   Current init_ui_cmd id: {id(core.tools.init_ui_cmd)}")
print(f"   Earlier init_ui_cmd id: {id(init_ui_cmd)}")
print(f"   Same function: {core.tools.init_ui_cmd is init_ui_cmd}")

# 6. Final state check
print("\n6. FINAL STATE CHECK")
print("-" * 50)

print(f"   First import init_screentree id: {id(init_screentree_func)}")
print(f"   Current init_screentree id: {id(core.tools.init_screentree)}")
print(f"   Same init_screentree: {init_screentree_func is core.tools.init_screentree}")

print(f"\n   Initial _SCREENTREE (None): {core.tools._SCREENTREE is None}")
print(f"   Final _SCREENTREE is screentree: {core.tools._SCREENTREE is screentree}")

if core.tools._SCREENTREE is screentree:
    print("\n✓ SUCCESS: _SCREENTREE correctly set to ScreenMap instance")
else:
    print("\n✗ FAILURE: _SCREENTREE not set to ScreenMap instance")
    print(f"  _SCREENTREE is None: {core.tools._SCREENTREE is None}")
    print(f"  _SCREENTREE is some other object: {core.tools._SCREENTREE is not None}")

print("\n" + "=" * 80)
print("TRACE COMPLETE")
print("=" * 80)