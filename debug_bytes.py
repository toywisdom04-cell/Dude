with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

idx = content.find(b'Factory to create an ObservationLearner instance')
if idx >= 0:
    print('Found at:', idx)
    # Print raw bytes
    snippet = content[idx:idx+150]
    print('Raw bytes:')
    for i, b in enumerate(snippet):
        if b == 10:
            char = '\\n'
        elif b == 13:
            char = '\\r'
        elif 32 <= b < 127:
            char = chr(b)
        else:
            char = '.'
        print(f'{i:3d}: 0x{b:02x} \'{char}\'')