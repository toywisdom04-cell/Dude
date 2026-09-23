with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix the _extract_parameters docstring
# Line 570 (index 569): '        """\n'
# Line 571: 'Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
# Line 572: '        params = []\n'

# Fix: Combine into a single-line docstring
# Find the function definition
for i, line in enumerate(lines):
    if 'def _extract_parameters(self, pattern: ObservedPattern)' in line:
        func_idx = i
        break

# The next line should be the docstring
# Current state: line 570 (index 569) is '        """\n'
# Line 571 is 'Extract parameter definitions...' (no indent)
# Line 572 is '        """\n' (closing)

# Fix: Replace lines 570-572 with a proper single-line docstring
# Find the function definition
for i, line in enumerate(lines):
    if 'def _extract_parameters(self, pattern: ObservedPattern)' in line:
        func_idx = i
        break

# The docstring starts at func_idx + 1
# Current state:
#   idx:     """\n
#   idx+1: 'Extract parameter definitions from pattern contexts using ParameterExtractor."""\n
#   idx+1: '        params = []\n'

# Replace these 3 lines with a proper docstring
lines[569] = '        """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
# Remove the next line which has the closing """
# Find the closing """
for i in range(found_idx + 1, min(found_idx + 10, len(lines))):
    if '"""' in lines[i] and 'Extract parameter' in lines[i]:
        # This is the line with the closing """
        lines.pop(i)
        break

# Also remove the blank line after if it exists
# (the params = [] line should stay)

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')