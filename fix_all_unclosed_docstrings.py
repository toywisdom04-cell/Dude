import re

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

# Fix all triple-quoted strings that don't have whitespace after closing """
# Pattern: """...""" followed by non-whitespace
# Replace """text"""X with """text"""\nX where X is not whitespace

# First, let's find all problematic cases
matches = list(re.finditer(rb'"""[\s\S]*?"""', content, re.DOTALL))
fixed = 0

# We need to process from end to beginning to maintain correct indices
# Or build a new string by processing matches in reverse order

# Build a list of replacements
replacements = []
for i, m in enumerate(re.finditer(rb'"""[\s\S]*?"""', content, re.DOTALL)):
    start = m.start()
    end = m.end()
    if m.end() < len(content):
        next_chars = content[end:end+3]
        # Check if there's a whitespace character (space, tab, newline, carriage return)
        has_whitespace = any(c in (9, 10, 13, 32) for c in next_chars)
        if not has_whitespace:
            # Need to add newline after closing """
            # But only if the next char is not already whitespace
            replacements.append((end, end, b'\n'))

# Apply replacements in reverse order to maintain correct indices
replacements.sort(reverse=True)
for pos, end_pos, replacement in replacements:
    content = content[:pos] + replacement + content[end_pos:]

# Also fix the two specific docstrings we know about
content = content.replace(
    b'        """Get all detected patterns for inspection."""\n',
    b'        "Get all detected patterns for inspection."\n'
)
content = content.replace(
    b'    """Factory to create an ObservationLearner instance."""\n',
    b'    "Factory to create an ObservationLearner instance."\n'
)

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
    f.write(content)

print('Fixed all docstring issues')