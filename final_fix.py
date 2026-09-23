with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix all the docstring issues:
# 1. Line 570 (index 569): '        """\n' -> '            """\n'
# Line 571 (index 570): '        """Extract...' -> should be 12 spaces
# Line 573 (index 571): '        \n' -> should be 12 spaces
# Line 978 (index 978): '        """\n' then 'Get learning statistics."""\n' -> fix to '        """\nGet learning statistics."""\n'
# Line 989 (index 988): 'Get all detected patterns for inspection."""\n' -> '"Get all detected patterns for inspection."\n'
# Line 1003 (index 1002): '    """Factory to create...' -> '    "Factory to create...'

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix _extract_parameters docstring (around line 570)
# Line 570 (index 569): '        """\n' -> '            """\n'
# Line 571 (index 570): '        """Extract parameter...' -> '            """Extract...'
# Line 572 (index 571): '        \n' -> 12 spaces

# Fix get_stats docstring (around line 979)
# Find the lines
for i, line in enumerate(lines):
    if 'Get learning statistics.' in line and '"""' in line and 'return' not in line:
        # This is the problematic line
        lines[i] = lines[i].replace('Get learning statistics."""', 'Get learning statistics.')
        break

# Fix the _extract_parameters docstring (around line 570)
# Find the function
for i, line in enumerate(lines):
    if 'def _extract_parameters' in line:
        # Next line should be the docstring
        if '"""' in lines[i+1] and 'Extract parameter' in lines[i+2]:
            lines[i+1] = '            """Extract parameter definitions from pattern contexts using ParameterExtractor."""\n'
            break

# Fix get_observation_learner docstring (line ~1003)
for i, line in enumerate(lines):
    if 'Factory to create an ObservationLearner instance' in line and '"""' in line:
        lines[i] = line.replace('"""Factory to create an ObservationLearner instance."""', '"Factory to create an ObservationLearner instance."')
        break

# Fix get_patterns docstring
for i, line in enumerate(lines):
    if 'Get all detected patterns for inspection' in line and '"""' in line:
        lines[i] = line.replace('"""Get all detected patterns for inspection."""', '"Get all detected patterns for inspection."')
        break

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed all docstrings')