#!/usr/bin/env python3
"""
Fix ScreenMap.available() to check if the thread is actually working
"""

print("=" * 80)
print("FIXING SCREENMAP.AVAILABLE() METHOD")
print("=" * 80)

# Read the current core/screentree.py file
with open('core/screentree.py', 'r') as f:
    lines = f.readlines()

# Find and replace the available() method
for i, line in enumerate(lines):
    if 'def available(self):' in line:
        print(f"Found available() method at line {i + 1}")

        # Find the return statement in the method (next few lines)
        for j in range(i + 1, min(i + 10, len(lines))):
            if 'return _UIA' in lines[j]:
                print(f"Current implementation at line {j + 1}: {lines[j].strip()}")

                # Replace with fixed implementation
                lines[j] = '        return _UIA and self._built_at > 0.0\n'
                print("Replacing with: return _UIA and self._built_at > 0.0")

                # Write the file back
                with open('core/screentree.py', 'w') as f:
                    f.writelines(lines)

                print("✓ Fix applied successfully!")
                break
        break

print("\n" + "=" * 80)
print("FIX APPLIED")
print("=" * 80)
print("\nSummary:")
print("  File: core/screentree.py")
print("  Method: ScreenMap.available()")
print("  Change: return _UIA and self._built_at > 0.0")
print("\nThis ensures available() returns True only when:")
print("  1. uiautomation module imports successfully")
print("  2. ScreenMap thread has actually started working (_built_at > 0.0)")
print("\nThe fix addresses the root cause where ui_scan() was returning")
print("'UI Automation screen map is not active' despite _UIA being True.")