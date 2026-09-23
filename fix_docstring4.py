with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find the docstring that's not closed
idx = content.find(b'"""Factory to create an ObservationLearner instance."""')
if idx >= 0:
    # The docstring should end with """ but it's followed by \n    return
    # We need to add the closing """ before the \n    return
    # The current text: """Factory to create an ObservationLearner instance."""\n    return
    # Should be: """Factory to create an ObservationLearner instance."""\n    return
    
    # But wait - it already has the closing """ in the search pattern
    # Let me check what's actually there
    print('Found at:', idx)
    print('Context:', content[idx:idx+100])
else:
    print('Pattern not found, searching for partial...')
    idx2 = content.find(b'Factory to create an ObservationLearner instance')
    if idx2 >= 0:
        print('Found partial at:', idx2)
        print('Context:', content[idx2:idx2+150])