with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Pattern 1: Get all detected patterns for inspection
old1 = b'        """Get all detected patterns for inspection."""\n'
new1 = b'        "Get all detected patterns for inspection."\n'

# Pattern 2: Factory to create an ObservationLearner instance
old2 = b'    """Factory to create an ObservationLearner instance."""\n'
new2 = b'    "Factory to create an ObservationLearner instance."\n'

if old1 in content and old2 in content:
    content = content.replace(old1, b'        "Get all detected patterns for inspection."\n')
    content = content.replace(old2, b'    "Factory to create an ObservationLearner instance."\n')
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Fixed both docstrings')
else:
    print('Patterns not found')
    idx1 = content.find(b'Get all detected patterns for inspection')
    idx2 = content.find(b'Factory to create an ObservationLearner instance')
    if idx1 >= 0:
        print('Pattern 1 found at:', idx1)
        print(repr(content[idx1:idx1+80]))
    if idx2 >= 0:
        print('Pattern 2 found at:', idx2)
        print(repr(content[idx2:idx2+80]))