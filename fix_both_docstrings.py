with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

old1 = b'        """Get all detected patterns for inspection."""\n'
new1 = b'        "Get all detected patterns for inspection."\n'

old2 = b'    """Factory to create an ObservationLearner instance."""\n'
new2 = b'    "Factory to create an ObservationLearner instance."\n'

if old1 in content and old2 in content:
    content = content.replace(old1, new1)
    content = content.replace(old2, new2)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
        f.write(content)
    print('Fixed both docstrings')
else:
    print('Patterns not found')
    idx1 = content.find(b'Get all detected patterns for inspection')
    idx2 = content.find(b'Factory to create an ObservationLearner instance')
    if idx1 >= 0:
        print('Pattern 1 found at:', idx1)
    if idx2 >= 0:
        print('Pattern 2 found at:', idx2)