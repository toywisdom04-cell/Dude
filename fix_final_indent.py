with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix _extract_parameters function indentation
# The function def is at line 569 (index 568) - 8 spaces (correct for class method)
# Docstring at line 570 (index 570) should be 12 spaces (4 more than function def)
# Line 571 (docstring content) should also be indented
# Line 572 (empty line in docstring) should be 12 spaces
# Line 573+ (function body) should be 12 spaces

# Fix docstring lines
lines[569] = '            """\n'  # 12 spaces
lines[570] = '            """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
lines[571] = '            \n'  # empty line in docstring

# Also fix the blank line at index 572 (0-indexed = line 573)
if len(lines[572]) > 1 and len(lines[572]) - len(lines[572].lstrip()) == 9:
    lines[572] = '            \n'

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed docstring indentation')