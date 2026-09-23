with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix line 570 (index 569): docstring should be 12 spaces, not 8
lines[569] = '            """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')