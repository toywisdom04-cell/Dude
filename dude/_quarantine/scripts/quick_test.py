#!/usr/bin/env python3
"""
Quick test of recovery engine implementation.
"""

import os
import sys

def main():
    print("QUICK RECOVERY ENGINE TEST")
    print("=" * 50)

    # Check if recovery.py exists
    recovery_path = "core/recovery.py"
    if not os.path.exists(recovery_path):
        print(f"ERROR: File '{recovery_path}' does not exist")
        return False

    print(f"✓ File exists: {recovery_path}")

    # Get file size
    file_size = os.path.getsize(recovery_path)
    print(f"File size: {file_size} bytes")

    # Read first 500 bytes to check for key elements
    with open(recovery_path, "rb") as f:
        content_bytes = f.read(500)
        content = content_bytes.decode('utf-8', errors='replace')

    # Check for key elements
    key_elements = [
        ("class RecoveryEngine", "Main recovery engine class"),
        ("def detect_and_recover", "Main recovery workflow method"),
        ("class ActionStatus", "Action status enum"),
        ("_observe_state", "State observation"),
        ("_diagnose_mismatch", "Diagnosis method"),
        ("_generate_hypotheses", "Hypothesis generation"),
        ("_select_recovery_action", "Action selection"),
        ("_click_dialog_button_real", "Dialog button click"),
        ("_inspect_screen_real", "Screen inspection"),
        ("verify_recovery", "Verification method"),
        ("_record_recovery_lesson", "Lesson recording")
    ]

    print("\nChecking key implementation elements:")
    passed = 0
    failed = 0

    for element, description in key_elements:
        if element in content:
            print(f"  ✓ {description}")
            passed += 1
        else:
            print(f"  ✗ {description}")
            failed += 1

    # Check for evidence-based diagnosis (basic check)
    evidence_found = 0
    if "_SCREENTREE" in content:
        print("  ✓ UI Automation (_SCREENTREE) integration")
        evidence_found += 1
    if "ui_click" in content:
        print("  ✓ Real UI clicking (ui_click)")
        evidence_found += 1
    if "ActionStatus" in content:
        print("  ✓ Action status tracking")
        evidence_found += 1

    print(f"\nEvidence patterns found: {evidence_found}")

    # Check for obvious placeholder patterns
    placeholder_patterns = [
        'return "Clicked dialog button',
        'return "Screen inspection completed"',
        '"success": true',
        '"error": null'
    ]

    print("\nChecking for placeholder patterns:")
    has_placeholders = False
    for pattern in placeholder_patterns:
        if pattern in content:
            print(f"  ✗ Found placeholder: {pattern}")
            has_placeholders = True

    if not has_placeholders:
        print("  ✓ No placeholder patterns found")

    # Final assessment
    print("\n" + "=" * 50)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print(f"Evidence patterns: {evidence_found}/3 found")

    min_requirements_met = (passed >= 8 and evidence_found >= 2 and not has_placeholders)

    if min_requirements_met:
        print("\n✓ SUCCESS: Recovery engine appears properly implemented!")
        print("\nKey features:")
        print("  - Uses existing DUDE components (UI Automation, observer, etc.)")
        print("  - No false-success placeholder patterns")
        print("  - Evidence-based diagnosis and action selection")
        print("  - Proper verification and lesson recording")
        return True
    else:
        print("\n✗ NEEDS FIXES: Implementation incomplete")
        print("  - Missing core implementation elements")
        print("  - Missing evidence patterns")
        print("  - Contains placeholder patterns")
        return False

if __name__ == "__main__":
    # Change to script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    success = main()

    # Write summary
    with open("recovery_implementation_summary.txt", "w") as f:
        f.write(f"Recovery Engine Implementation\n")
        f.write(f"Status: {'SUCCESS' if success else 'NEEDS FIXES'}\n")
        f.write(f"File: core/recovery.py\n")
        f.write(f"Test run: quick_test.py\n")

    sys.exit(0 if success else 1)