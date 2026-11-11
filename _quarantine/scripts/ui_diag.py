#!/usr/bin/env python3
"""
Simple UI Automation diagnostic
"""

import sys
import time

print("=" * 70)
print("UI AUTOMATION DIAGNOSTIC")
print("=" * 70)

# 1. VERIFY _UIA
print("\n1. VERIFY _UIA")
print("-" * 50)

try:
    import uiautomation as auto
    print("SUCCESS: uiautomation imported successfully")
    print(f"  uiautomation version: {getattr(auto, 'VERSION', 'N/A')}")
    print(f"  UIAutomationInitializerInThread available: {hasattr(auto, 'UIAutomationInitializerInThread')}")
    print(f"  GetForegroundControl available: {hasattr(auto, 'GetForegroundControl')}")

    # Check what's in core.screentree module
    sys.path.insert(0, 'E:\\Dude\\dude')
    from core.screentree import _UIA
    print(f"\n  _UIA value from core.screentree: {_UIA}")
    print(f"  Type: {type(_UIA)}")

except Exception as e:
    print(f"ERROR: Error verifying _UIA: {e}")
    import traceback
    traceback.print_exc()

# 2. TEST ScreenMap in MAIN thread
print("\n2. TEST ScreenMap in MAIN thread")
print("-" * 50)

try:
    from core.screentree import ScreenMap
    print("SUCCESS: ScreenMap imported")

    print("\nCreating ScreenMap object in MAIN thread...")
    start_time = time.time()
    screentree = ScreenMap()
    creation_time = time.time() - start_time
    print(f"SUCCESS: ScreenMap created in {creation_time:.2f} seconds")

    print(f"\n  screentree.available() = {screentree.available()}")

    # Check thread status
    print(f"\n  Thread info:")
    print(f"    Thread object: {screentree._thread}")
    print(f"    Thread name: {screentree._thread.name if screentree._thread else 'N/A'}")
    print(f"    Thread is_alive: {screentree._thread.is_alive() if screentree._thread else 'N/A'}")

    # Try uiautomation functions from main thread
    print(f"\n  Testing uiautomation functions from MAIN thread:")
    try:
        fg = auto.GetForegroundControl()
        print(f"    SUCCESS: GetForegroundControl() succeeded: {fg is not None}")
    except Exception as e:
        print(f"    ERROR: GetForegroundControl() failed: {type(e).__name__}: {e}")

    # Try current_view from main thread (this is what ScreenMap._loop does)
    print(f"\n  Trying screentree.current_view() from MAIN thread...")
    try:
        view = screentree.current_view(limit=5)
        print(f"    SUCCESS: current_view succeeded")
        print(f"    First 200 chars: {view[:200]}...")
    except Exception as e:
        print(f"    ERROR: current_view failed: {type(e).__name__}: {e}")

    # Check if thread is alive and running
    if screentree._thread and screentree._thread.is_alive():
        print(f"\n  Background thread is ALIVE")
        print(f"  Thread daemon: {screentree._thread.daemon}")
        print(f"  Thread ident: {screentree._thread.ident}")

        # Let it run for a bit to see if it processes any frames
        print(f"\n  Letting thread run for 2 seconds...")
        time.sleep(2)

        # Try current_view again
        try:
            view2 = screentree.current_view(limit=5)
            print(f"    SUCCESS: current_view after sleep succeeded")
        except Exception as e:
            print(f"    ERROR: current_view after sleep failed: {type(e).__name__}: {e}")
    else:
        print(f"\n  Background thread is DEAD or never started")

except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# 3. TEST ScreenMap in NEW thread
print("\n3. TEST ScreenMap in NEW thread")
print("-" * 50)

def test_screentree_in_thread():
    try:
        print("    New thread (test-screentree): Starting...")
        import uiautomation as auto

        print("    New thread: Creating ScreenMap...")
        screentree = ScreenMap()

        print("    New thread: screentree.available() = " + str(screentree.available()))

        # Test uiautomation from this thread
        print("    New thread: Testing GetForegroundControl()...")
        try:
            fg = auto.GetForegroundControl()
            print("    New thread: SUCCESS: GetForegroundControl() succeeded: " + str(fg is not None))
        except Exception as e:
            print("    New thread: ERROR: GetForegroundControl() failed: " + type(e).__name__ + ": " + str(e))

        # Test current_view from this thread
        print("    New thread: Testing current_view()...")
        try:
            view = screentree.current_view(limit=5)
            print("    New thread: SUCCESS: current_view succeeded")
        except Exception as e:
            print("    New thread: ERROR: current_view failed: " + type(e).__name__ + ": " + str(e))

        return screentree
    except Exception as e:
        print("    New thread: ERROR: Exception: " + type(e).__name__ + ": " + str(e))
        import traceback
        traceback.print_exc()
        return None

import threading
try:
    test_thread = threading.Thread(target=test_screentree_in_thread, name="test-screentree")
    test_thread.start()
    test_thread.join(timeout=5.0)
    print("  Test thread completed")
except Exception as e:
    print("ERROR: Error creating test thread: " + type(e).__name__ + ": " + str(e))

# 4. TEST UIAutomationInitializerInThread
print("\n4. TEST UIAutomationInitializerInThread")
print("-" * 50)

print("\nTesting UIAutomationInitializerInThread in MAIN thread...")

try:
    import uiautomation as auto

    print("  Calling UIAutomationInitializerInThread() in main thread...")
    with auto.UIAutomationInitializerInThread():
        print("  Inside UIA context")
        fg = auto.GetForegroundControl()
        print("  GetForegroundControl() succeeded: " + str(fg is not None))
        if fg:
            print("  Control name: " + str(fg.Name))
            print("  Control type: " + str(fg.ControlTypeName))
except Exception as e:
    print("  ERROR: Exception: " + type(e).__name__ + ": " + str(e))
    import traceback
    traceback.print_exc()

# 5. FINAL DIAGNOSTIC SUMMARY
print("\n5. FINAL DIAGNOSTIC SUMMARY")
print("-" * 50)

print("\nCollecting final diagnostic information...")

result = {
    'uiautomation_imported': False,
    '_UIA_value': None,
    'screentree_main_thread_created': False,
    'main_thread_uia_success': False,
    'main_thread_current_view_success': False,
    'main_thread_thread_alive': False,
    'uia_initializer_main_thread_success': False,
}

# Collect data
try:
    import uiautomation as auto
    result['uiautomation_imported'] = True

    from core.screentree import _UIA
    result['_UIA_value'] = _UIA

    from core.screentree import ScreenMap
    screentree = ScreenMap()
    result['screentree_main_thread_created'] = True
    result['main_thread_thread_alive'] = screentree._thread.is_alive() if screentree._thread else False

    # Test main thread uiautomation
    try:
        with auto.UIAutomationInitializerInThread():
            fg = auto.GetForegroundControl()
            result['main_thread_uia_success'] = fg is not None
    except:
        pass

    # Test main thread current_view
    try:
        view = screentree.current_view(limit=5)
        result['main_thread_current_view_success'] = True
    except:
        pass

    # Test uia initializer
    try:
        with auto.UIAutomationInitializerInThread():
            result['uia_initializer_main_thread_success'] = True
    except:
        pass

except Exception as e:
    print("Error during final diagnostic: " + type(e).__name__ + ": " + str(e))

print("\nFinal Diagnostic Results:")
for key, value in result.items():
    print("  " + key + ": " + str(value))

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)