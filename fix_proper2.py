with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the _extract_parameters function
func_idx = -1
for i, line in enumerate(lines):
    if 'def _extract_parameters(self, pattern: ObservedPattern)' in line:
        func_idx = i
        break

if func_idx >= 0:
    # Fix the docstring - replace lines func_idx+1 to func_idx+2 with proper docstring
    # Current lines:
    #   func_idx + 1: '        """\n'
    #   func_idx + 1: 'Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
    # We want to replace these two lines with one proper docstring line
    
    # Replace line func_idx + 1 (the """\n)
    lines[func_idx + 1] = '        """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
    # Remove the next line which has the closing """
    del lines[func_idx + 2]
    
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print('Fixed _extract_parameters docstring')