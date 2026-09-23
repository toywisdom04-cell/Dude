with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix docstring indentation for _extract_parameters
# Line 570 (index 569): '        """\n' -> should be 12 spaces
# Line 571 (index 570): '        """Extract parameter definitions...' -> should be 12 spaces
# Line 572 (index 571): '        \n' -> should be 12 spaces

lines[569] = '            """\n'
lines[570] = '            """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
lines[571] = '            \n'

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed docstring indentation')