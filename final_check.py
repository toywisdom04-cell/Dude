import re

with open(r'E:\Dude\dude\core\orchestrator\realtime_voice.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check for actual phrase-based routing patterns (not just comments or variable names)
patterns_to_check = [
    (r'"(what are you doing|what happened|status)"', 'status phrases as literals'),
    (r'"(calculate|compute)"', 'math phrases as literals'),
    (r'if.*what.*in low', 'phrase-based if check'),
    (r'if.*calculate.*in low', 'math phrase if check'),
    (r'if.*compute.*in low', 'compute phrase if check'),
]

for pattern, desc in patterns_to_check:
    if re.search(pattern, content, re.IGNORECASE):
        print(f'FAIL: {desc} still present')
    else:
        print(f'PASS: {desc} removed')

# Also check the comment line
if 'later \'what are you doing\' answers report truth' in content:
    print('INFO: Comment about "what are you doing" exists but is just a comment')

# Check _fast_reply only does time/date/math
if 'return None  # All other routing goes through CognitiveFrontDoor' in content:
    print('PASS: _fast_reply returns None for non-deterministic routes')
else:
    print('FAIL: _fast_reply not properly returning None')