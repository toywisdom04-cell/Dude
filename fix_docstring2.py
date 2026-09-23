with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the missing closing triple quotes on the docstring
old = ') -> ObservationLearner:\n    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner('

new = ') -> ObservationLearner:\n    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner('

if old in content:
    content = content.replace(old, new)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed docstring')
else:
    print('Pattern not found with exact spacing')
    # Debug
    idx = content.find(') -> ObservationLearner:')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx:idx+200]))