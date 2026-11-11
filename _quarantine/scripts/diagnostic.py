#!/usr/bin/env python3
"""
Ultimate diagnostic to trace _SCREENTREE lifecycle during normal DUDE startup
This script traces the exact point where _SCREENTREE gets reset to None
"""

import sys
import time
import threading
from datetime import datetime

print("=" * 80)
print("ULTIMATE DIAGNOSTIC - _SCREENTREE LIFECYCLE")
print("=" * 80)

# Global tracing state
trace_log = []
trace_lock = threading.Lock()

# Track module identity
print("\n1. MODULE IDENTITY CHECK")
print("-" * 50)
module_id = id(sys.modules['core.tools'])
print(f"Core.tools module ID: {module_id}")
print(f"Module file: {sys.modules['core.tools'].__file__}")

# 2. INSTRUMENT init_screentree
print("\n2. INSTRUMENT init_screentree()")
print("-" * 50)

# Import core.tools first
import core.tools
print(f"Initial _SCREENTREE: {core.tools._SCREENTREE}")

# Save the original function
original_init_screentree = core.tools.init_screentree

def traced_init_screentree(map_):
    timestamp = datetime.now().isoformat()
    thread_name = threading.current_thread().name

    with trace_lock:
        trace_entry = {
            'timestamp': timestamp,
            'event': 'init_screentree',
            'thread': thread_name,
            'call_id': len(trace_log) + 1,
            'map_id': id(map_) if map_ else None,
            'map_type': type(map_).__name__ if map_ else None,
            'before_value': core.tools._SCREENTREE,
            'before_id': id(core.tools._SCREENTREE) if core.tools._SCREENTREE else None,
            'before_type': type(core.tools._SCREENTREE).__name__ if core.tools._SCREENTREE else None,
        }

        print(f"[CALL #{trace_entry['call_id']}] init_screentree() called:")
        print(f"   Thread: {thread_name}")
        print(f"   Map: {map_} (type: {type(map_).__name__})")
        print(f"   Before _SCREENTREE: {trace_entry['before_value']}")
        print(f"   Before _SCREENTREE ID: {trace_entry['before_id']}")
        # Call the original function
        result = original_init_screentree(map_)

        # Log after state
        after_value = core.tools._SCREENTREE
        after_id = id(core.tools._SCREENTREE) if core.tools._SCREENTREE else None
        after_type = type(core.tools._SCREENTREE).__name__ if core.tools._SCREENTREE else None

        trace_entry.update({
            'after_value': after_value,
            'after_id': after_id,
            'after_type': after_type,
        })

        print(f"   After _SCREENTREE: {trace_entry['after_value']}")
        print(f"   After _SCREENTREE ID: {trace_entry['after_id']}")

        if trace_entry['before_value'] != trace_entry['after_value']:
            print(f"   ⚠️  VALUE CHANGED!")
            print(f"      From: {trace_entry['before_value']} (ID: {trace_entry['before_id']})")
            print(f"      To: {trace_entry['after_value']} (ID: {trace_entry['after_id']})")

        trace_log.append(trace_entry)
        return result

# Replace the function
core.tools.init_screentree = traced_init_screentree

# 3. CREATE ScreenMap and call init_screentree
print("\n3. CREATE ScreenMap AND CALL init_screentree")
print("-" * 50)

from core.screentree import ScreenMap
print("Creating ScreenMap...")
screentree = ScreenMap()
print(f"ScreenMap created: {screentree}")
print(f"Thread alive: {screentree._thread.is_alive()}")
print(f"Thread ident: {screentree._thread.ident}")

print(f"\nCalling init_screentree(screentree)...")
core.tools.init_screentree(screentree)

print(f"\nAfter init_screentree:")
print(f"  core.tools._SCREENTREE: {core.tools._SCREENTREE}")
print(f"  screentree is _SCREENTREE: {screentree is core.tools._SCREENTREE}")

# 4. INSTRUMENT ui_scan
print("\n\n4. INSTRUMENT ui_scan()")
print("-" * 50)

# Save original ui_scan
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
        'current_screentree_type': type(current_screentree).__name__ if current_screentree else None,
        'is_none': current_screentree is None,
        'screentree_available': current_screentree.available() if current_screentree else None,
    }

    print(f"[CALL #{trace_entry['call_id']}] ui_scan() check:")
    print(f"   Thread: {thread_name}")
    print(f"   _SCREENTREE value: {trace_entry['current_screentree_value']}")
    print(f"   _SCREENTREE is None: {trace_entry['is_none']}")
    print(f"   _SCREENTREE.available(): {trace_entry['screentree_available']}")

    # Call original
    result = original_ui_scan(memory, args)

    trace_entry['result'] = result
    trace_entry['result_contains_error'] = "UI Automation screen map is not active" in result if result else False

    print(f"   Result: {result[:100] if result and len(result) > 100 else result}...")
    print(f"   Result contains error: {trace_entry['result_contains_error']}")

    if trace_entry['result_contains_error']:
        print(f"   ❌ ui_scan() FAILED - this is the problem!")

    trace_log.append(trace_entry)
    return result

# Replace ui_scan
core.tools.ui_scan = traced_ui_scan

# 5. Call ui_scan
print("\n5. CALL ui_scan()")
print("-" * 50)

result = core.tools.ui_scan(None, {})

# 6. ANALYZE RESULTS
print("\n\n6. ANALYZE RESULTS")
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

# Check if _SCREENTREE became None
if core.tools._SCREENTREE is None:
    print(f"\n🚨 CRITICAL: _SCREENTREE is None when ui_scan() is called!")
    print(f"   This explains why ui_scan() failed.")
else:
    print(f"\n✅ _SCREENTREE is set to a valid object")

# 7. CHECK MODULE IDENTITY
print("\n\n7. CHECK MODULE IDENTITY")
print("-" * 50)
print(f"Module ID from sys.modules: {module_id}")
print(f"Module ID from core.tools: {id(core.tools)}")
print(f"Same module: {module_id == id(core.tools)}")

# 8. CHECK FOR NESTED IMPORTS
print("\n\n8. CHECK FOR NESTED IMPORTS")
print("-" * 50)
print("Checking for from core.tools import init_ui_cmd...")

# Try to import init_ui_cmd from core.tools
from core.tools import init_ui_cmd
print(f"init_ui_cmd imported: {init_ui_cmd}")
print(f"Same as core.tools.init_ui_cmd: {init_ui_cmd is core.tools.init_ui_cmd}")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)
print(f"\nSUMMARY:")
print(f"  Total events traced: {len(trace_log)}")
print(f"  Value changes: {len(changed_entries)}")
print(f"  Final _SCREENTREE is None: {core.tools._SCREENTREE is None}")
print(f"  ui_scan() contains error: {'UI Automation screen map is not active' in result if 'result' in locals() else 'Unknown'}")