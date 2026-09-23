#!/usr/bin/env python3
"""
Find all _SCREENTREE assignments in the production codebase
"""

import os
import re

print("=" * 80)
print("FINDING _SCREENTREE ASSIGNMENTS")
print("=" * 80)

# Search patterns for _SCREENTREE assignments
patterns = [
    r'_SCREENTREE\s*=',
    r'_SCREENTREE\s*:',
    r'^\s*_SCREENTREE\s*=',
    r'^\s*_SCREENTREE\s*:',
]

# Files to search (production code, not tests/diagnostics)
production_files = []

# Walk through the directory
for root, dirs, files in os.walk('E:\Dude\dude'):
    # Skip common test and diagnostic directories
    dirs[:] = [d for d in dirs if not d.startswith('test') and
               not d.startswith('diagnos') and
               not d.startswith('simple') and
               d not in ['__pycache__', '.git']]

    for file in files:
        if file.endswith('.py'):
            full_path = os.path.join(root, file)

            # Skip specific files
            if 'simple_ui_test' in file or 'ui_diag' in file or 'comprehensive_ui_diagnostic' in file or \
               'final_ui_diagnostic' in file or 'production_analysis' in file or 'trace_import' in file or \
               'test_fix' in file or 'production_analysis' in file or 'clean_startup_trace' in file:
                continue

            production_files.append(full_path)

print(f"Found {len(production_files)} Python files to search")

# Search for assignments
assignments = []

for file_path in production_files:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

            for i, line in enumerate(lines):
                line_num = i + 1
                stripped = line.strip()

                # Skip comments
                if stripped.startswith('#'):
                    continue

                # Check for _SCREENTREE assignments
                for pattern in patterns:
                    if re.search(pattern, stripped):
                        # Get file relative path
                        rel_path = os.path.relpath(file_path, 'E:\Dude\dude')

                        # Get context (previous and next lines)
                        context_lines = []
                        start_line = max(0, i - 1)
                        end_line = min(len(lines), i + 2)

                        for j in range(start_line, end_line):
                            context_lines.append(f"{j+1:4d}: {lines[j].rstrip()}")

                        assignments.append({
                            'file': rel_path,
                            'line_num': line_num,
                            'line': stripped,
                            'context': '\n'.join(context_lines)
                        })
                        break  # Only count once per line

    except Exception as e:
        print(f"Error reading {file_path}: {e}")

print(f"\nFound {len(assignments)} _SCREENTREE assignments:")

if not assignments:
    print("No _SCREENTREE assignments found in production code!")
else:
    # Group by file
    by_file = {}
    for assignment in assignments:
        file = assignment['file']
        if file not in by_file:
            by_file[file] = []
        by_file[file].append(assignment)

    # Display by file
    for file, file_assignments in sorted(by_file.items()):
        print(f"\n{file}:")
        for assignment in file_assignments:
            print(f"  Line {assignment['line_num']}: {assignment['line']}")
            # Show context
            if len(assignment['context'].split('\n')) > 3:
                print(f"    Context:")
                for context_line in assignment['context'].split('\n')[:3]:
                    print(f"      {context_line}")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
print(f"Total _SCREENTREE assignments: {len(assignments)}")

# Check for multiple assignments in same file
print("\nAssignment summary by file:")
for file, file_assignments in sorted(by_file.items()):
    print(f"  {file}: {len(file_assignments)} assignment(s)")