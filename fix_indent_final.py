with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the _extract_parameters function
start_idx = -1
for i, line in enumerate(lines):
    if 'def _extract_parameters(self, pattern: ObservedPattern)' in line:
        start_idx = i
        break

# Find end of function
end_idx = len(lines)
for i in range(start_idx + 1, len(lines)):
    if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= 8 and lines[i].strip():
        end_idx = i
        break

print(f'Function from line {start_idx+1} to {end_idx}')

# Fix indentation: add 4 spaces to function body
for i in range(start_idx + 1, end_idx):
    if lines[i].strip():
        if len(lines[i]) - len(lines[i].lstrip()) == 8:
            lines[i] = '    ' + lines[i]

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed indentation')