with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# The docstring has CRLF line endings which breaks the triple quotes
# Find the docstring
idx = content.find(b'"""Factory to create an ObservationLearner instance."""')
if idx >= 0:
    # The docstring should end with """ but it has \r\n after it
    # We need to ensure the """ is properly closed
    # The current bytes: b'"""Factory to create an ObservationLearner instance."""\r\n'
    # Should be: b'"""Factory to create an ObservationLearner instance."""\r\n'
    # But the issue is the triple quotes are not being recognized as closing
    
    # Let's check the exact bytes
    snippet = content[idx:idx+80]
    print('Current bytes:', snippet)
    print('Hex:', snippet.hex())
    
    # The problem: the docstring is """text"""\r\n but Python sees """text"""\r as the string and \n as next token
    # We need to ensure the docstring is properly closed
    
    # Fix: replace the docstring line with proper format
    old = b'    """Factory to create an ObservationLearner instance."""\r\n'
    new = b'    """Factory to create an ObservationLearner instance."""\n'
    
    if old in content:
        content = content.replace(old, new)
        with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
            f.write(content)
        print('Fixed docstring line endings')
    else:
        print('Pattern not found')
        # Debug: show what we have
        idx = content.find(b'Factory to create an ObservationLearner instance')
        if idx >= 0:
            print('Found at:', idx)
            print('Context:', content[idx-10:idx+80])
else:
    print('Docstring not found')