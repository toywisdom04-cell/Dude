with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Replace the problematic section
old = b') -> ObservationLearner:\n    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner('

new = b') -> ObservationLearner:\n    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner('

if old in content:
    content = content.replace(old, new)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Rewrote the function signature')
else:
    print('Pattern not found')
    idx = content.find(b'Factory to create an ObservationLearner instance')
    if idx >= 0:
        print('Found at:', idx)
        print(content[idx:idx+200])