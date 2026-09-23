with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Find all triple quotes
positions = []
idx = 0
while True:
    idx = content.find(b'"""', idx)
    if idx == -1:
        break
    positions.append(idx)
    idx += 3

print(f'Found {len(positions)} triple quotes at positions: {positions}')

# Should be even number
if len(positions) % 2 == 1:
    print('UNEVEN number of triple quotes - UNTERMINATED STRING!')
    # Find the unmatched one
    for i, pos in enumerate(positions):
        context = content[max(0,pos-30):pos+30]
        print(f'  {i}: pos={pos}, context={content[max(0,pos-30):pos+50]}')
else:
    print('All triple quotes are balanced')