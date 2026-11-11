#!/usr/bin/env python3
"""
Trace the import issue in core.tools
"""

import sys

print("=" * 80)
print("TRACE IMPORT ISSUE")
print("=" * 80)

# 1. Check initial state
print("\n1. Initial state before imports")
print("-" * 50)

# Check if core.tools exists in sys.modules
if 'core.tools' in sys.modules:
    print(f"core.tools already in sys.modules")
    print(f"Module: {sys.modules['core.tools']}")
    print(f"_SCREENTREE: {sys.modules['core.tools']._SCREENTREE}")
else:
    print("core.tools not yet imported")

# 2. Simulate the exact import pattern from dude.py
print("\n\n2. Simulating dude.py import pattern")
print("-" * 50)

# First import like in dude.py line 716
print("Step 1: Import init_screentree from core.tools")
import core.tools
print(f"core.tools module: {core.tools}")
print(f"_SCREENTREE: {core.tools._SCREENTREE}")
print(f"init_screentree function: {core.tools.init_screentree}")

# Store the initial _SCREENTREE value
initial_screentree = core.tools._SCREENTREE
print(f"Initial _SCREENTREE value: {initial_screentree}")

# Create ScreenMap and call init_screentree
from core.screentree import ScreenMap
print("\nStep 2: Create ScreenMap and call init_screentree")

screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"ScreenMap _thread: {screentree._thread}")

# Call init_screentree exactly as in dude.py
print(f"\nCalling init_screentree(screentree)...")
print(f"Before: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"Map is screentree: {screentree is core.tools._SCREENTREE}")

core.tools.init_screentree(screentree)

print(f"After: core.tools._SCREENTREE = {core.tools._SCREENTREE}")
print(f"Map is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

# 3. Simulate the nested import (like in dude.py lines 733-741)
print("\n\n3. Simulating nested import scenario")
print("-" * 50)

# Save current state
current_screentree = core.tools._SCREENTREE
print(f"Current _SCREENTREE: {current_screentree}")

# Simulate the conditional import like in dude.py
print("Simulating: if not args.no_ui:")
print("  from core.tools import init_ui_cmd")

# This re-imports the module locally
from core.tools import init_ui_cmd

print(f"After nested import:")
print(f"  core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"  Same as before: {current_screentree is core.tools._SCREENTREE}")

# 4. Check if the module object is the same
print("\n\n4. Module object identity check")
print("-" * 50)

print(f"First import module id: {id(core.tools)}")
print(f"From sys.modules: {id(sys.modules['core.tools'])}")
print(f"Are they the same? {core.tools is sys.modules['core.tools']}")

# 5. Check what happens with reload or reimport
print("\n\n5. Checking for module re-import")
print("-" * 50)

# The key question: does the conditional import cause a module re-import?
# In Python, importing the same module multiple times just returns the cached
# version, unless you explicitly reload it.

import types
print(f"core.tools type: {type(core.tools)}")
print(f"Module dir: {[x for x in dir(core.tools) if not x.startswith('_')][:10]}...")

# 6. What if there's a from ... import ... that reloads?
print("\n\n6. Checking for potential reload")
print("-" * 50)

# Check if init_ui_cmd is the same function as before
print(f"init_ui_cmd id: {id(core.tools.init_ui_cmd)}")
print(f"init_ui_cmd source: {core.tools.init_ui_cmd.__module__}.{core.tools.init_ui_cmd.__name__}")

# Check if _SCREENTREE has been reset
print(f"_SCREENTREE id: {id(core.tools._SCREENTREE)}")
print(f"Same as original: {initial_screentree is core.tools._SCREENTREE}")

# 7. Final state
print("\n\n7. FINAL STATE")
print("-" * 50)

print(f"Initial _SCREENTREE (from first import): {initial_screentree}")
print(f"Final _SCREENTREE: {core.tools._SCREENTREE}")
print(f"Same object? {initial_screentree is core.tools._SCREENTREE}")
print(f"ScreenMap object in _SCREENTREE? {screentree is core.tools._SCREENTREE}")

if initial_screentree is not core.tools._SCREENTREE:
    print("\nWARNING: _SCREENTREE has changed!")
    print("This could be the root cause of UI Automation being unavailable.")
else:
    print("\nOK: _SCREENTREE remains unchanged.")

print("\n" + "=" * 80)
print("TRACE COMPLETE")
print("=" * 80)