with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

idx = content.find(b'Factory to create an ObservationLearner instance')
if idx >= 0:
    print('Found at:', idx)
    snippet = content[idx:idx+120]
    for i, b in enumerate(snippet):
        if b == 10:
            print(f'{i}: LF')
        elif b == 13:
            print(f'{i}: CR')
        elif b == 34:
            print(f'{i}: QUOTE')
        elif 32 <= b < 127:
            print(f'{i}: {chr(b)}')
        else:
            print(f'{i}: 0x{b:02x}')