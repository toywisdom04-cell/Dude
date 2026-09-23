#!/usr/bin/env python3
"""
Simple diagnostic to trace _SCREENTREE lifecycle during normal DUDE startup
"""

import sys
import time
import threading
from datetime import datetime

print("=" * 80)
print("DIAGNOSTIC - _SCREENTREE LIFECYCLE")
print("=" * 80)

# Global tracing state
trace_log = []

# 1. IMPORT core.tools FIRST
print("\n1. IMPORT core.tools")
print("-" * 50)
import core.tools
print(f"core.tools imported successfully")
print(f"core.tools._SCREENTREE initial: {core.tools._SCREENTREE}")
print(f"Module ID: {id(core.tools)}")

# 2. CREATE ScreenMap AND CALL init_screentree
print("\n2. CREATE ScreenMap AND CALL init_screentree")
print("-" * 50)

from core.screentree import ScreenMap
print("Creating ScreenMap...")
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")

print("\nCalling init_screentree(screentree)...")
print(f"Before: core.tools._SCREENTREE = {core.tools._SCREENTREE}")

# Instrument init_screentree
original_init_screentree = core.tools.init_screentree

def traced_init_screentree(map_):
    timestamp = datetime.now().isoformat()
    thread_name = threading.current_thread().name

    with threading.Lock():
        trace_entry = {
            'timestamp': timestamp,
            'event': 'init_screentree',
            'thread': thread_name,
            'call_id': len(trace_log) + 1,
            'before_value': core.tools._SCREENTREE,
            'before_id': id(core.tools._SCREENTREE) if core.tools._SCREENTREE else None,
            'map_id': id(map_) if map_ else None,
            'map_type': type(map_).__name__ if map_ else None,
        }

        print(f"[CALL #{trace_entry['call_id']}] init_screentree() called:")
        print(f"   Thread: {thread_name}")
        print(f"   Map: {map_} (type: {type(map_).__name__})")
        print(f"   Before _SCREENTREE: {trace_entry['before_value']}")

        # Call original
        result = original_init_screentree(map_)

        # Check if value changed
        after_value = core.tools._SCREENTREE
        after_id = id(core.tools._SCREENTREE) if core.tools._SCREENTREE else None

        trace_entry.update({
            'after_value': after_value,
            'after_id': after_id,
        })

        print(f"   After _SCREENTREE: {trace_entry['after_value']}")

        if trace_entry['before_value'] != trace_entry['after_value']:
            print(f"   WARNING: VALUE CHANGED!")
            print(f"      From: {trace_entry['before_value']} (ID: {trace_entry['before_id']})")
            print(f"      To: {trace_entry['after_value']} (ID: {trace_entry['after_id']})")

        trace_log.append(trace_entry)
        return result

# Replace the function
core.tools.init_screentree = traced_init_screentree

# Call init_screentree
core.tools.init_screentree(screentree)

# 3. INSTRUMENT ui_scan
print("\n\n3. INSTRUMENT ui_scan()")
print("-" * 50)

# Instrument ui_scan
original_ui_scan = core.tools.ui_scan

def traced_ui_scan(memory, args):
    timestamp = datetime.now().isoformat()
    thread_name = threading.current_thread().name

    # Get _SCREENTREE before calling
    current_screentree = core.tools._SCREENTREE

    trace_entry = {
        'timestamp': timestamp,
        'event': 'ui_scan_check',
        'thread': thread_name,
        'call_id': len(trace_log) + 1,
        'current_screentree_value': current_screentree,
        'current_screentree_id': id(current_screentree) if current_screentree else None,
        'is_none': current_screentree is None,
        'screentree_available': current_screentree.available() if current_screentree else None,
    }

    print(f"[CALL #{trace_entry['call_id']}] ui_scan() check:")
    print(f"   Thread: {thread_name}")
    print(f"   _SCREENTREE value: {trace_entry['current_screentree_value']}")
    print(f"   _SCREENTREE is None: {trace_entry['is_none']}")

    # Call original
    result = original_ui_scan(memory, args)

    trace_entry['result'] = result
    trace_entry['result_contains_error'] = "UI Automation screen map is not active" in result if result else False

    print(f"   Result: {result[:100] if result and len(result) > 100 else result}...")
    print(f"   Result contains error: {trace_entry['result_contains_error']}")

    if trace_entry['result_contains_error']:
        print(f"   ❌ ui_scan() FAILED")

    trace_log.append(trace_entry)
    return result

# Replace ui_scan
core.tools.ui_scan = traced_ui_scan

# Call ui_scan
print(f"\nCalling ui_scan()...")
result = core.tools.ui_scan(None, {})

# 4. FINAL ANALYSIS
print("\n\n4. FINAL ANALYSIS")
print("-" * 50)

print(f"Total trace entries: {len(trace_log)}")
print(f"\nValue changes detected:")
changed_entries = [e for e in trace_log if e['before_value'] != e.get('after_value', e['before_value'])]
print(f"  Number of assignments: {len(changed_entries)}")

for entry in changed_entries:
    print(f"  {entry['timestamp']} - {entry['event']}:")
    print(f"    From: {entry['before_value']} (ID: {entry['before_id']})")
    print(f"    To: {entry['after_value']} (ID: {entry['after_id']})")

print(f"\nFinal state:")
print(f"  core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"  screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

# Check if ui_scan failed
if 'result' in locals() and "UI Automation screen map is not active" in result:
    print(f"\n🚨 CRITICAL: ui_scan() failed with 'UI Automation screen map is not active'")
    print(f"   This is the primary issue preventing UI Automation from working.")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)
print(f"\nSUMMARY:")
print(f"  Total events traced: {len(trace_log)}")
print(f"  Value changes: {len(changed_entries)}")
print(f"  Final _SCREENTREE is None: {core.tools._SCREENTREE is None}")
print(f"  ui_scan() contains error: {'UI Automation screen map is not active' in (result if 'result' in locals() else '')}")

if core.tools._SCREENTREE is None:
    print(f"  CONCLUSION: _SCREENTREE was reset to None during DUDE startup")
else:
    print(f"  CONCLUSION: _SCREENTREE is set but ui_scan() still fails")