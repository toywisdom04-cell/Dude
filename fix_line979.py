with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix the get_stats docstring - line 977-978
# Change from:
#         """
# Get learning statistics."""
# to:
#        """
# Get learning statistics."""

# Find and fix the specific line
for i, line in enumerate(lines):
    if 'Get learning statistics."""' in line:
        print(f'Found at line {i}: {repr(lines[i])}')
        # Fix: change 'Get learning statistics."""' to '"""\\nGet learning statistics."""'
        lines[i] = lines[i].replace('Get learning statistics."""', '"""\\nGet learning statistics."""')
        break

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')