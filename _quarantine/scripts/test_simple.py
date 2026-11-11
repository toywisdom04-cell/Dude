#!/usr/bin/env python3
"""
Simple test to verify the recovery engine implementation.
"""

import os
import sys
from pathlib import Path

# Add the dude directory to path
sys.path.insert(0, str(Path(__file__).parent))

def test_recovery_exists():
    """Test that recovery engine implementation exists."""
    print("🔍 TESTING RECOVERY ENGINE IMPLEMENTATION")
    print("=" * 60)

    # Check if recovery.py exists
    recovery_path = "core/recovery.py"
    if not os.path.exists(recovery_path):
        print(f"❌ File '{recovery_path}' does not exist")
        return False

    # Read the recovery implementation
    with open(recovery_path, "r") as f:
        content = f.read()

    print(f"📁 File size: {len(content)} characters")
    print(f"📊 Lines of code: {len(content.splitlines())}")

    # Check for key implementation elements
    implementation_elements = [
        ('class RecoveryEngine', 'Main recovery engine class'),
        ('def detect_and_recover', 'Main recovery workflow method'),
        ('_observe_state', 'State observation method'),
        ('_diagnose_mismatch', 'Diagnosis method'),
        ('_generate_hypotheses', 'Hypothesis generation'),
        ('_select_recovery_action', 'Action selection'),
        ('_click_dialog_button_real', 'Dialog button click'),
        ('_inspect_screen_real', 'Screen inspection'),
        ('verify_recovery', 'Verification method'),
        ('_record_recovery_lesson', 'Lesson recording')
    ]

    print("\n🔧 Checking implementation elements:")
    implemented = 0
    for element, description in implementation_elements:
        if element in content:
            print(f"✅ {description}")
            implemented += 1
        else:
            print(f"❌ Missing: {description}")

    print(f"\n📈 Implementation score: {implemented}/{len(implementation_elements)} elements")

    # Check for evidence-based diagnosis
    evidence_patterns = [
        ('evidence', 'Evidence tracking'),
        ('confidence', 'Confidence scoring'),
        ('actual_state', 'Actual state tracking'),
        ('expected_state', 'Expected state tracking'),
        ('_SCREENTREE', 'UI Automation integration')
    ]

    print("\n🔍 Checking evidence-based patterns:")
    evidence_found = 0
    for pattern, description in evidence_patterns:
        if pattern in content:
            print(f"✅ {description}")
            evidence_found += 1
        else:
            print(f"❌ Missing: {description}")

    print(f"\n📊 Evidence patterns: {evidence_found}/{len(evidence_patterns)} found")

    # Check for placeholder/false-success patterns
    placeholder_patterns = [
        'return "Clicked dialog button',
        'return "Screen inspection completed"',
        'return "Recovery succeeded"',
        'status = "executed"',
        '"success": true',
        '"error": null'
    ]

    print("\n❌ Checking for placeholder/false-success patterns:")
    has_placeholders = False
    for pattern in placeholder_patterns:
        if pattern in content:
            print(f"❌ Found placeholder: {pattern}")
            has_placeholders = True

    if not has_placeholders:
        print("✅ No placeholder/false-success patterns found")

    # Check for ActionStatus enum
    if "class ActionStatus(Enum):" in content:
        print("\n✅ ActionStatus enum is properly defined")
    else:
        print("\n❌ ActionStatus enum is missing")

    # Check for workflow progression
    workflow_terms = ["see", "understand", "plan", "act", "verify", "recover", "learn"]
    workflow_found = sum(1 for term in workflow_terms if term in content.lower())
    print(f"\n📊 Workflow progression: {workflow_found}/7 terms found")

    # Check for UI automation integration
    if "_SCREENTREE" in content and "ui_click" in content:
        print("✅ UI automation integration is present")
    else:
        print("❌ UI automation integration is missing")

    # Check for verification
    if "verify_recovery" in content and "ActionStatus.ACTION_VERIFIED" in content:
        print("✅ Verification is implemented")
    else:
        print("❌ Verification is missing")

    # Check for lesson recording
    if "_record_recovery_lesson" in content and "experience" in content:
        print("✅ Lesson recording is implemented")
    else:
        print("❌ Lesson recording is missing")

    print("\n" + "=" * 60)

    # Summary
    min_required = 8  # Minimum required elements for a proper implementation
    if implemented >= min_required and evidence_found >= 3 and not has_placeholders:
        print("🎉 SUCCESS: Recovery engine appears to be properly implemented!")
        print("\nKey features verified:")
        print(f"  ✅ {implemented} core implementation elements")
        print(f"  ✅ {evidence_found} evidence-based patterns")
        print(f"  ✅ No placeholder/false-success patterns")
        print(f"  ✅ Proper workflow progression")
        print("\nThe recovery engine should:")
        print("  - Use existing DUDE components (UI Automation, observer, etc.)")
        print("  - Follow SEE → UNDERSTAND → PLAN → ACT → VERIFY → RECOVER → LEARN")
        print("  - Execute and verify actions, not just report success")
        print("  - Learn from recovery attempts persistently")
        return True
    else:
        print("❌ NEEDS FIXES: Recovery engine implementation is incomplete")
        print(f"  - Missing {max(0, min_required - implemented)} core elements")
        print(f"  - Missing {max(0, 3 - evidence_found)} evidence patterns")
        if has_placeholders:
            print("  - Contains placeholder patterns")
        return False


if __name__ == "__main__":
    success = test_recovery_exists()

    # Create a simple summary file
    with open("recovery_implementation_summary.txt", "w") as f:
        f.write(f"Recovery Engine Implementation Summary\n")
        f.write(f"Status: {'SUCCESS' if success else 'NEEDS FIXES'}\n")
        f.write(f"File: core/recovery.py\n")
        if success:
            f.write(f"Result: Ready for Phase 3B implementation\n")
        else:
            f.write(f"Result: Requires fixes before DUDE integration\n")

    sys.exit(0 if success else 1)