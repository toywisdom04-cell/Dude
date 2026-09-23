with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find all lines with triple quotes
for i, line in enumerate(lines):
    if '"""' in line:
        print(f'{i+1}: {repr(line)}')

# Also check lines 975-985 for the get_stats docstring
print()
print("Lines 975-985:")
for i in range(975, 990):
    if i < len(lines):
        print(f'{i+1}: {repr(lines[i])}')