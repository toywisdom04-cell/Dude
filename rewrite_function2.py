with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

old_bytes = b') -> ObservationLearner:\n    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner('

new_bytes = b') -> ObservationLearner:\n    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner('

if old_bytes in content:
    content = content.replace(old_bytes, new_bytes)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Rewrote function')
else:
    print('Pattern not found')
    idx = content.find(b'Factory to create an ObservationLearner instance')
    if idx >= 0:
        print('Found at:', idx)
        print(content[idx:idx+200])