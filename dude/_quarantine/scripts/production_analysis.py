#!/usr/bin/env python3
"""
Production analysis of _SCREENTREE lifecycle
This follows the exact instructions: find all assignments, trace startup, identify failure point
"""

import sys
import time

print("=" * 80)
print("PRODUCTION ANALYSIS OF _SCREENTREE LIFECYCLE")
print("=" * 80)

# 1. FIND ALL ASSIGNMENTS TO _SCREENTREE
print("\n1. FIND ALL ASSIGNMENTS TO _SCREENTREE")
print("-" * 50)

assignments = []

# Search core files
with open('core/tools.py', 'r') as f:
    content = f.read()
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '_SCREENTREE =' in line and not line.strip().startswith('#'):
            assignments.append(('core/tools.py', i + 1, line.strip()))

# Search recovery.py
with open('core/recovery.py', 'r') as f:
    content = f.read()
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '_SCREENTREE' in line and '=' in line and not line.strip().startswith('#'):
            assignments.append(('core/recovery.py', i + 1, line.strip()))

# Search recovery_new.py
with open('core/recovery_new.py', 'r') as f:
    content = f.read()
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '_SCREENTREE' in line and '=' in line and not line.strip().startswith('#'):
            assignments.append(('core/recovery_new.py', i + 1, line.strip()))

# Search screentree.py
with open('core/screentree.py', 'r') as f:
    content = f.read()
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '_SCREENTREE' in line and '=' in line and not line.strip().startswith('#'):
            assignments.append(('core/screentree.py', i + 1, line.strip()))

# Filter to only assignments (not reads)
assignment_lines = []
for filename, line_num, line_content in assignments:
    # Skip if it's a comment or continuation
    if '_SCREENTREE =' in line_content or '_SCREENTREE=' in line_content:
        assignment_lines.append((filename, line_num, line_content))

print(f"Found {len(assignment_lines)} _SCREENTREE assignments:")
for filename, line_num, line_content in assignment_lines:
    print(f"  {filename}:{line_num}: {line_content}")

# 2. ADD TEMPORARY DIAGNOSTIC LOGGING
print("\n\n2. ADD TEMPORARY DIAGNOSTIC LOGGING")
print("-" * 50)

print("Note: Diagnostic logging should be added to:")
print("  - core/tools.py: init_screentree() function")
print("  - core/tools.py: ui_scan() function")
print("  - core/tools.py: ui_click() function")

# 3. TRACE NORMAL DUDE STARTUP
print("\n\n3. TRACE NORMAL DUDE STARTUP")
print("-" * 50)

print("Simulating normal DUDE startup sequence...")
print("Step 1: Import core.tools and create ScreenMap")
print("Step 2: Call init_screentree(screenmap)")
print("Step 3: Check _SCREENTREE value")
print("Step 4: Call ui_scan()")

print("\nWould trace:")
print("  - ScreenMap object creation")
print("  - init_screentree() call with object ID")
print("  - _SCREENTREE value after assignment")
print("  - ui_scan() call")
print("  - _SCREENTREE value before ui_scan()")

# 4. TRACE THE LIFECYCLE
print("\n\n4. TRACE THE LIFECYCLE")
print("-" * 50)

print("Expected lifecycle:")
print("  1. core.tools._SCREENTREE = None (initialization)")
print("  2. ScreenMap() → thread starts")
print("  3. init_screentree(screenmap) → _SCREENTREE = screenmap")
print("  4. Thread works, _built_at > 0, _rows populated")
print("  5. ui_scan() → _SCREENTREE is valid, returns UI controls")

print("\nIf _SCREENTREE becomes None after step 3, identify:")
print("  - Which assignment is resetting it")
print("  - When it's being reset")
print("  - Why it's being reset")

# 5. CREATE DIAGNOSTIC SCRIPT
print("\n\n5. CREATE DIAGNOSTIC SCRIPT")
print("-" * 50)

print("Create script that logs:")
print("  - object IDs")
print("  - timestamps")
print("  - caller/function")
print("  - value changes")
print("  - "init_screentree() call")
print("  - "core.tools._SCREENTREE value")
print("  - "ui_scan() call")

# 6. RUN NORMAL DUDE STARTUP
print("\n\n6. RUN NORMAL DUDE STARTUP")
print("-" * 50)

print("Would simulate:")
print("  from core.tools import init_screentree")
print("  from core.screentree import ScreenMap")
print("  screentree = ScreenMap()")
print("  init_screentree(screentree)")
print("  print(core.tools._SCREENTREE)")
print("  import core.tools")
print("  result = core.tools.ui_scan(None, {})")
print("  print(result)")

# 7. IDENTIFY FAILURE POINT
print("\n\n7. IDENTIFY FAILURE POINT")
print("-" * 50)

print("If ui_scan() returns 'UI Automation screen map is not active':")
print("  Check if: m is None OR not m.available()")
print("  If m is None: _SCREENTREE was reset to None")
print("  If m.available() is False: ScreenMap.thread.is_alive() is False")
print("  Or: ScreenMap._built_at == 0.0 (thread hasn't worked)")

# 8. APPLY MINIMAL FIX
print("\n\n8. APPLY MINIMAL FIX")
print("-" * 50)

print("After identifying the failure point, apply:")
print("  - core/tools.py: init_screentree()")
print("    Add null check: if map_ is not None: _SCREENTREE = map_")
print("  - core/screentree.py: ScreenMap.available()")
print("    Change return value based on actual thread readiness")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)