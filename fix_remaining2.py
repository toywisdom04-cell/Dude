with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Fix the "Get learning statistics" docstring
old = b'        """\nGet learning statistics."""\n'
new = b'        """\nGet learning statistics."""\n'

if old in content:
    content = content.replace(old, new)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Fixed get_stats docstring')
else:
    print('Pattern not found')
    idx = content.find(b'Get learning statistics')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx:idx+80]))