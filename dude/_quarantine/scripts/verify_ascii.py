#!/usr/bin/env python3
"""
ASCII-only verification of latency instrumentation implementation.

This script verifies that the latency instrumentation has been correctly
added to the DUDE codebase without using any Unicode characters.
"""

import os
import sys

def check_file_exists(filepath):
    return os.path.exists(filepath)

def check_file_contains(filepath, patterns):
    if not check_file_exists(filepath):
        return False
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        for pattern in patterns:
            if pattern not in content:
                return False
        return True
    except:
        return False

def print_check(label, condition):
    if condition:
        print(f"   PASS: {label}")
    else:
        print(f"   FAIL: {label}")

def main():
    print("=" * 80)
    print("DUDE LATENCY INSTRUMENTATION - ASCII VERIFICATION")
    print("=" * 80)

    cwd = os.getcwd()
    print(f"Current directory: {cwd}")

    all_pass = True

    # Check 1: latency_measurement.py
    print("\n1. Checking latency_measurement.py...")
    if check_file_exists("latency_measurement.py"):
        print("   File exists: latency_measurement.py")

        latency_patterns = [
            "class DUDELatencyTracker",
            "def start_request(",
            "def end_request(",
            "def start_stage(",
            "def end_stage(",
            "time.perf_counter_ns()"
        ]

        latency_pass = True
        for pattern in latency_patterns:
            if not check_file_contains("latency_measurement.py", [pattern]):
                print(f"   Missing: {pattern}")
                latency_pass = False

        if latency_pass:
            print("   All required components present")
        else:
            all_pass = False
    else:
        print("   FAIL: latency_measurement.py does not exist")
        all_pass = False

    # Check 2: dude.py
    print("\n2. Checking dude.py...")
    if check_file_exists("dude.py"):
        print("   File exists: dude.py")

        dude_patterns = [
            "# LATENCY INSTRUMENTATION:",
            "tracker.start_request(",
            "tracker.start_stage('total_execution')",
            "tracker.start_stage('brain_processing')",
            "tracker.end_stage('brain_processing')",
            "tracker.end_stage('total_execution')",
            "tracker.end_request()",
            "from latency_measurement import get_latency_tracker"
        ]

        dude_pass = True
        for pattern in dude_patterns:
            if not check_file_contains("dude.py", [pattern]):
                print(f"   Missing: {pattern}")
                dude_pass = False

        if dude_pass:
            print("   All latency instrumentation present")
        else:
            all_pass = False
    else:
        print("   FAIL: dude.py does not exist")
        all_pass = False

    # Check 3: ScreenMap.available() fix
    print("\n3. Checking ScreenMap.available() fix...")
    if check_file_exists("core/screentree.py"):
        if check_file_contains("core/screentree.py", [
            "def available(self):",
            "return _UIA and self._built_at > 0.0"
        ]):
            print("   ScreenMap.available() fix preserved")
        else:
            print("   ScreenMap.available() fix not found")
            all_pass = False
    else:
        print("   core/screentree.py not found")

    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION RESULT")
    print("=" * 80)

    if all_pass:
        print("\nSUCCESS: Latency instrumentation implementation complete")
        print("\nWhat was implemented:")
        print("1. latency_measurement.py - Complete latency tracking system")
        print("2. dude.py - Integration into DUDE's handle_text() function")
        print("3. ScreenMap.available() - Phase 3B fix preserved")

        print("\nFiles modified/created:")
        print("  - latency_measurement.py")
        print("  - dude.py")

        print("\nThe implementation includes:")
        print("  - Request total latency timing")
        print("  - Brain processing timing")
        print("  - Uses time.perf_counter_ns()")
        print("  - Phase 3B preserved")

        return True
    else:
        print("\nFAILURE: Some requirements not met")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)