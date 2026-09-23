with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Check if file ends with newline
print('Ends with newline:', content.endswith(b'\n'))
print('Length:', len(content))

# Check the exact bytes at the end
print('Last 100 bytes:')
print(content[-100:])
print()
print('Last 100 hex:')
print(content[-100:].hex())