with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

old_b = b'    """Factory to create an ObservationLearner instance."""\n'
new_b = b'    "Factory to create an ObservationLearner instance."\n'

if old_b in content:
    content = content.replace(old_b, new_b)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Fixed!')
else:
    print('Pattern not found')
    idx = content.find(b'Factory to create an ObservationLearner instance')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx:idx+80]))