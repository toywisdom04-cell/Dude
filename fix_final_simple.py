import re

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    source = f.read()

# Fix the specific known problematic docstrings
# 1. Get learning statistics docstring
source = source.replace(
    '        """\nGet learning statistics."""\n',
    '        """\nGet learning statistics."""\n'
)

# 2. Get all detected patterns for inspection - change to single quotes
source = source.replace(
    '        """\nGet all detected patterns for inspection."""\n',
    '        "Get all detected patterns for inspection."\n'
)

# 3. Fix the Factory docstring - change to single quotes
source = source.replace(
    '    """Factory to create an ObservationLearner instance."""\n',
    '    "Factory to create an ObservationLearner instance."\n'
)

# Write back
with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
    f.write(source)

print('Fixed docstrings')