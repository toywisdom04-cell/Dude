with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find the docstring
idx = content.find(b'Factory to create an ObservationLearner instance')
if idx >= 0:
    # Check 100 bytes around it
    print('Context:')
    print(content[idx-50:idx+100])
    print()
    # Find closing triple quotes
    end_idx = content.find(b'"""', idx + 50)
    if end_idx >= 0:
        print('Closing triple quotes found at:', end_idx)
        print('Bytes:', content[end_idx:end_idx+3])
    else:
        print('NO CLOSING TRIPLE QUOTES FOUND!')
        # Show what's there
        print('Bytes after docstring start:', content[idx:idx+100])
else:
    print('Docstring not found')