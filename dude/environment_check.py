#!/usr/bin/env python3
"""
Environment check for DUDE Windows runtime testing.

This script verifies the environment requirements for running DUDE
on Windows with real UI Automation and application launching.
"""

import sys
import os

def main():
    print("DUDE Windows Runtime Environment Check")
    print("=" * 80)

    # Check current directory
    print(f"Current directory: {os.getcwd()}")

    # Check Python environment
    print("\nPython Environment:")
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version}")

    # Try to import DUDE core modules
    print("\nTesting DUDE module imports:")

    # Try to import core DUDE modules
    try:
        import core.brain
        print("✓ core.brain - import succeeded")
    except Exception as e:
        print(f"✗ core.brain - import failed: {e}")

    try:
        from core.recovery import RecoveryEngine
        print("✓ RecoveryEngine - import succeeded")
    except Exception as e:
        print(f"✗ RecoveryEngine - import failed: {e}")

    try:
        from core.screentree import ScreenMap
        print("✓ ScreenMap - import succeeded")
    except Exception as e:
        print(f"✗ ScreenMap - import failed: {e}")

    try:
        from core.tools import ui_scan, open_app
        print("✓ core.tools - import succeeded")
    except Exception as e:
        print(f"✗ core.tools - import failed: {e}")

    # Try to import uiautomation (Windows-only)
    print("\nTesting Windows UI Automation:")
    try:
        import uiautomation as auto
        print("✓ uiautomation - import succeeded")

        # Test if ScreenMap.available() fix exists
        sm = ScreenMap()
        available_result = sm.available()
        print(f"✓ ScreenMap.available() works: {available_result}")

        # Check the actual implementation
        import inspect
        source = inspect.getsource(sm.available)
        if "return _UIA and self._built_at > 0.0" in source:
            print("✓ ScreenMap.available() fix verified")
        else:
            print("✗ ScreenMap.available() fix NOT found")

    except ImportError as e:
        print(f"✗ uiautomation - import failed: {e}")
    except Exception as e:
        print(f"✗ uiautomation - runtime error: {e}")

    # Check for latency measurement
    print("\nChecking latency measurement system:")
    try:
        import latency_measurement
        print("✓ latency_measurement - import succeeded")

        # Try to get latency tracker
        from latency_measurement import get_latency_tracker
        tracker = get_latency_tracker()
        print("✓ latency_tracker - accessible")
    except Exception as e:
        print(f"✗ latency_measurement - import failed: {e}")

    # Summary
    print("\n" + "=" * 80)
    print("ENVIRONMENT CHECK SUMMARY")
    print("=" * 80)

    print("\nFor real DUDE execution with 'open notepad', you need:")
    print("1. Windows operating system")
    print("2. Windows UI Automation (uiautomation library)")
    print("3. Windows applications (Notepad)")
    print("4. Proper Windows permissions")
    print("5. Real DUDE runtime execution")

    print("\nCurrent environment limitations:")
    print("- Cannot import Windows-only libraries (uiautomation)")
    print("- Cannot launch Windows applications")
    print("- Cannot run full DUDE Windows runtime")

    print("\nWhat can be tested:")
    print("✓ DUDE code structure verification")
    print("✓ Latency instrumentation implementation")
    print("✓ Phase 3B component verification")
    print("✓ RecoveryEngine integration")

    print("\nWhat cannot be tested:")
    print("✗ Real UI Automation functionality")
    print("✗ Windows application launching")
    print("✗ Real-time performance measurements")
    print("✗ Actual 'open notepad' execution")

    return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)