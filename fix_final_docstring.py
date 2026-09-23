with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# The issue is at position 43170 - the docstring for get_observation_learner
# Find the exact location
idx = content.find(b'def get_observation_learner(')
if idx >= 0:
    # Find the docstring start
    docstring_start = content.find(b'"""', idx)
    if docstring_start >= 0:
        docstring_end = content.find(b'"""', docstring_start + 3)
        if docstring_end == -1:
            print('Docstring not closed! Fixing...')
            # The docstring should end before "return ObservationLearner("
            return_idx = content.find(b'return ObservationLearner(', docstring_start)
            if return_idx != -1:
                # Insert closing triple quotes before return
                content = content[:return_idx] + b'"""\n    ' + content[return_idx:]
                with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
                    f.write(content)
                print('Fixed docstring')
            else:
                print('Could not find return statement')
        else:
            print('Docstring not found')
    else:
        print('Function not found')

# Verify
with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()
count = content.count(b'"""')
print(f'Triple quotes count: {count}')
if count % 2 == 0:
    print('BALANCED!')
else:
    print('STILL UNBALANCED')