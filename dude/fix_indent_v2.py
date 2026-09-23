with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the _extract_parameters function
start_idx = -1
for i, line in enumerate(lines):
    if 'def _extract_parameters(self, pattern: ObservedPattern)' in line:
        start_idx = i
        break

# Find end of function - look for next method at class level (8 spaces) or end of class
end_idx = len(lines)
for i in range(start_idx + 1, len(lines)):
    stripped = lines[i].strip()
    leading = len(lines[i]) - len(lines[i].lstrip())
    if lines[i].strip() and leading <= 8 and lines[i].strip().startswith('def '):
        end_idx = i
        break
    elif lines[i].strip() and leading < 8:
        end_idx = i
        break

print(f'Function from line {start_idx+1} to {end_idx}')

# Fix indentation: add 4 spaces to function body (lines start_idx+1 to end_idx-1)
for i in range(start_idx + 1, end_idx):
    line = lines[i]
    if line.strip():
        leading = len(line) - len(line.lstrip())
        if leading == 8:
            lines[i] = '    ' + line

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed indentation')