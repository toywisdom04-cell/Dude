#!/usr/bin/env python3
"""
Comprehensive UI Automation diagnostic
Traces _SCREENTREE assignment and ScreenMap worker health
"""

import sys
import time
import threading
import traceback
from collections import defaultdict

print("=" * 80)
print("COMPREHENSIVE UI AUTOMATION DIAGNOSTIC")
print("=" * 80)

# Track module instances
module_instances = {}

def track_module_imports():
    """Track all core.tools module instances"""
    for module_name, module_obj in sys.modules.items():
        if 'core.tools' in module_name:
            module_instances[module_name] = {
                'module': module_obj,
                'id': id(module_obj),
                'path': getattr(module_obj, '__file__', 'N/A')
            }

# 1. MODULE DUPLICATION CHECK
print("\n1. MODULE DUPLICATION CHECK")
print("-" * 50)

track_module_imports()

if len(module_instances) > 1:
    print(f"WARNING: Multiple core.tools module instances found!")
    for name, info in module_instances.items():
        print(f"  Module: {name}")
        print(f"    ID: {info['id']}")
        print(f"    Path: {info['path']}")
        print(f"    _SCREENTREE: {getattr(info['module'], '_SCREENTREE', 'NOT FOUND')}")
else:
    print(f"OK: Single core.tools module instance")
    main_module = list(module_instances.values())[0]
    print(f"  Module: {list(module_instances.keys())[0]}")
    print(f"  ID: {main_module['id']}")
    print(f"  Path: {main_module['path']}")

# Get the main core.tools module
main_core_tools = None
for name, info in module_instances.items():
    if 'dude' in name and 'core.tools' in name:
        main_core_tools = info['module']
        break

if not main_core_tools:
    # Try to find it by importing
    import core.tools
    main_core_tools = sys.modules['core.tools']

print(f"\n  Main core.tools _SCREENTREE: {main_core_tools._SCREENTREE}")
print(f"  _UI_CMD: {main_core_tools._UI_CMD}")

# 2. TRACE _SCREENTREE ASSIGNMENT
print("\n\n2. TRACE _SCREENTREE ASSIGNMENT")
print("-" * 50)

# Monkey-patch init_screentree to trace calls
original_init_screentree = None
call_count = 0
call_log = []

def traced_init_screentree(map_):
    global call_count, call_log
    call_count += 1
    thread_name = threading.current_thread().name
    timestamp = time.time()

    log_entry = {
        'call_num': call_count,
        'thread': thread_name,
        'timestamp': timestamp,
        'map_id': id(map_) if map_ else None,
        'map_type': type(map_).__name__ if map_ else None,
        'map_attrs': {
            'has_rows': hasattr(map_, '_rows') if map_ else None,
            'has_thread': hasattr(map_, '_thread') if map_ else None,
            'available': map_.available() if hasattr(map_, 'available') else None
        } if map_ else None,
        'before_screentree': main_core_tools._SCREENTREE
    }

    print(f"  init_screentree() CALL #{call_count}")
    print(f"    Thread: {thread_name}")
    print(f"    Map: {map_} (type: {type(map_).__name__})")
    print(f"    Map ID: {id(map_) if map_ else 'N/A'}")
    print(f"    Before: _SCREENTREE = {main_core_tools._SCREENTREE}")

    # Call original
    result = original_init_screentree(map_)

    log_entry['after_screentree'] = main_core_tools._SCREENTREE
    log_entry['execution_time'] = time.time() - timestamp

    print(f"    After: _SCREENTREE = {main_core_tools._SCREENTREE}")
    print(f"    Execution time: {log_entry['execution_time']:.4f}s")

    call_log.append(log_entry)
    return result

# Patch init_screentree
import core.tools
original_init_screentree = core.tools.init_screentree
core.tools.init_screentree = traced_init_screentree

# 3. SCREENMAP INITIALIZATION WITH DETAILED TRACING
print("\n\n3. SCREENMAP INITIALIZATION WITH DETAILED TRACING")
print("-" * 50)

# Track ScreenMap internals
screentree_objects = []
thread_info = {}

from core.screentree import ScreenMap

class TrackedScreenMap(ScreenMap):
    def __init__(self, on_change=None):
        super().__init__(on_change)
        self.creation_time = time.time()
        self._ready_detected = False
        self._first_available_time = None
        self._thread_start_time = None
        self._worker_exceptions = []

        # Track thread creation
        if hasattr(self, '_thread') and self._thread:
            self._thread_start_time = time.time()
            print(f"    ScreenMap thread started: {self._thread.name}")
            print(f"      Thread ident: {self._thread.ident}")
            print(f"      Thread daemon: {self._thread.daemon}")

            # Start monitoring thread
            monitor_thread = threading.Thread(
                target=self._monitor_thread,
                name=f"screenmap-monitor-{id(self)}",
                daemon=True
            )
            monitor_thread.start()

    def _monitor_thread(self):
        """Monitor thread health and detect readiness"""
        thread = self._thread
        start_time = time.time()
        last_heartbeat = start_time

        # Wait for thread to actually start working
        time.sleep(0.5)  # Give thread time to initialize

        # Check if thread is still alive
        if not thread.is_alive():
            self._worker_exceptions.append(
                f"Thread died shortly after start at {start_time}"
            )
            print(f"    WARNING: ScreenMap thread died shortly after start")
            return

        # Try to detect readiness through actual UI operations
        attempts = 0
        max_attempts = 10

        while attempts < max_attempts and not self._ready_detected:
            attempts += 1
            time.sleep(1.0)  # Wait between checks

            try:
                # Try to get a view - this will test actual worker functionality
                view = self.current_view(limit=3)

                # Check if we got meaningful data
                if view and "ACTIVE APP:" in view:
                    # Success! Thread is working
                    self._ready_detected = True
                    self._first_available_time = time.time()
                    print(f"    ScreenMap READY at {self._first_available_time - start_time:.2f}s")
                    print(f"      View contains: {view[:100]}...")

                    # Get current state
                    rows_count = len(self._rows) if hasattr(self, '_rows') else 0
                    print(f"      Controls in map: {rows_count}")
                    return
                else:
                    print(f"    Attempt {attempts}: View is empty or malformed")

            except Exception as e:
                self._worker_exceptions.append(e)
                print(f"    Attempt {attempts} exception: {type(e).__name__}: {e}")

        if not self._ready_detected:
            print(f"    ScreenMap NOT READY after {attempts} attempts")
            print(f"      Worker exceptions: {len(self._worker_exceptions)}")
            for i, exc in enumerate(self._worker_exceptions[-3:]):
                print(f"        {i+1}. {type(exc).__name__}: {exc}")

# Track the actual screentree created by DUDE
actual_screentree = None
original_init = TrackedScreenMap.__init__

def tracked_init_with_init_screentree(self, on_change=None):
    original_init(self, on_change)

    # Check if this is the main screentree (from dude.py)
    if 'dude' in threading.current_thread().name:
        global actual_screentree
        actual_screentree = self

        # Manually call init_screentree to trace it
        print(f"\n    → Manually calling init_screentree(ScreenMap object)")
        import core.tools
        core.tools.init_screentree(self)

TrackedScreenMap.__init__ = tracked_init_with_init_screentree

# 4. CREATE ScreenMap (this will trigger init_screentree)
print("\nCreating ScreenMap object via TrackedScreenMap...")
start_time = time.time()
screentree = TrackedScreenMap()
creation_time = time.time() - start_time
print(f"ScreenMap created in {creation_time:.2f} seconds")

screentree_objects.append(screentree)

# 5. MONITORING LOOP
print("\n\n5. CONTINUOUS MONITORING")
print("-" * 50)

monitor_running = True
monitor_data = {
    'samples': [],
    'thread_states': [],
    'exceptions': []
}

def monitor_loop():
    """Continuously monitor the ScreenMap thread"""
    thread = screentree._thread
    monitor_id = threading.current_thread().ident

    while monitor_running and thread and thread.is_alive():
        sample_time = time.time()

        # Thread status
        is_alive = thread.is_alive()
        thread_state = {
            'time': sample_time,
            'is_alive': is_alive,
            'ident': thread.ident,
            'daemon': thread.daemon,
            'name': thread.name
        }

        # Try to get some data to verify thread is working
        try:
            view_sample = screentree.current_view(limit=3)
            data_available = bool(view_sample and "ACTIVE APP:" in view_sample)
            sample = {
                'time': sample_time,
                'has_data': data_available,
                'view_length': len(view_sample) if view_sample else 0,
                'has_rows': len(screentree._rows) if hasattr(screentree, '_rows') else 0
            }
        except Exception as e:
            sample = {
                'time': sample_time,
                'error': str(e),
                'has_data': False,
                'view_length': 0
            }
            monitor_data['exceptions'].append({
                'time': sample_time,
                'exception': e
            })

        monitor_data['samples'].append(sample)
        monitor_data['thread_states'].append(thread_state)

        time.sleep(2.0)  # Check every 2 seconds

# Start monitoring
monitor_thread = threading.Thread(target=monitor_loop, name="monitor-thread", daemon=True)
monitor_thread.start()
print(f"Started monitoring thread: {monitor_thread.name}")

# 6. FINAL STATE ANALYSIS
print("\n\n6. FINAL STATE ANALYSIS")
print("-" * 50)

print(f"ScreenMap object created: {screentree is not None}")
if screentree:
    print(f"  ScreenMap type: {type(screentree).__name__}")
    print(f"  Has _thread attribute: {hasattr(screentree, '_thread')}")

    if hasattr(screentree, '_thread') and screentree._thread:
        thread = screentree._thread
        print(f"  Thread: {thread.name}")
        print(f"  Thread ident: {thread.ident}")
        print(f"  Thread is_alive: {thread.is_alive()}")
        print(f"  Thread daemon: {thread.daemon}")

        # Check internal ScreenMap state
        print(f"\n  Internal ScreenMap state:")
        print(f"    _rows count: {len(screentree._rows) if hasattr(screentree, '_rows') else 'N/A'}")
        print(f"    _app: {screentree._app}")
        print(f"    _title: {screentree._title}")
        print(f"    _handle: {screentree._handle}")
        print(f"    _built_at: {screentree._built_at}")
        print(f"    _ready_detected: {screentree._ready_detected}")
        print(f"    _first_available_time: {screentree._first_available_time}")
        print(f"    _thread_start_time: {screentree._thread_start_time}")

        # Check if _SCREENTREE was set
        print(f"\n  _SCREENTREE status:")
        print(f"    core.tools._SCREENTREE: {main_core_tools._SCREENTREE}")
        print(f"    screentree is _SCREENTREE: {screentree is main_core_tools._SCREENTREE}")
        print(f"    screentree == _SCREENTREE: {screentree == main_core_tools._SCREENTREE}")

# 7. FINAL DIAGNOSTIC SUMMARY
print("\n\n7. FINAL DIAGNOSTIC SUMMARY")
print("-" * 50)

print(f"Module instances found: {len(module_instances)}")
for name, info in module_instances.items():
    print(f"  {name}: {info['id']} (path: {info['path']})")

print(f"\ninit_screentree() calls: {call_count}")
for entry in call_log:
    print(f"  Call #{entry['call_num']}:")
    print(f"    Thread: {entry['thread']}")
    print(f"    Before: {entry['before_screentree']}")
    print(f"    After: {entry['after_screentree']}")
    print(f"    Time: {entry['execution_time']:.4f}s")

print(f"\nScreenMap object:")
print(f"  Created: {screentree is not None}")
print(f"  Ready detected: {screentree._ready_detected if screentree else 'N/A'}")
print(f"  Thread alive: {screentree._thread.is_alive() if screentree and hasattr(screentree, '_thread') else 'N/A'}")
print(f"  Controls count: {len(screentree._rows) if screentree and hasattr(screentree, '_rows') else 'N/A'}")
print(f"  Worker exceptions: {len(screentree._worker_exceptions) if screentree else 'N/A'}")

# Stop monitoring
monitor_running = False
if monitor_thread.is_alive():
    monitor_thread.join(timeout=3.0)

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)