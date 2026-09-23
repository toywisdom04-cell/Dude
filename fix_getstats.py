with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix get_stats docstring - line 978 (index 977)
# Current: '        """\n' then 'Get learning statistics.\"\"\"\n'
# Should be: '        \"\"\"Get learning statistics.\"\"\"\n'

# Also fix the _extract_parameters docstring
# Current: line 570 (index 570): '        \"\"\"\\n'
# Should be: '        \"\"\"Extract parameter definitions from pattern contexts using ParameterExtractor.\"\"\"\\n'

# Fix get_stats docstring
lines[977] = '        """Get learning statistics."""\n'

# Fix _extract_parameters docstring
lines[570] = '        \"\"\"Extract parameter definitions from pattern contexts using ParameterExtractor.\"\"\"\\n'

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')