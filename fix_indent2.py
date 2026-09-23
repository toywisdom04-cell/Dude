with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix the docstring indentation for _extract_parameters
# Line 570 (index 569): '        """\n' - should be 8 spaces
# Line 571 (index 570): 'Extract parameter definitions from pattern contexts using ParameterExtractor."""\n' - should be 12 spaces

# Fix line 570 (index 570): should be 8 spaces + """
lines[570] = '        """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed docstring indentation')