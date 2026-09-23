with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'rb') as f:
    content = f.read()

import re

# Fix all docstrings that don't have a newline after the closing triple quotes
# Pattern: """text""" followed by something other than newline
# We need to add a newline after """ when it's followed by something other than newline/space/tab

# Pattern: """text""" followed by something that's not whitespace/newline
# We'll replace """text""" followed by non-whitespace with """text"""\n

# Use regex to find """text""" not followed by whitespace/newline
# and add a newline after the closing """

# More precise: find """...""" followed by a letter or parenthesis or other non-whitespace
# and add newline after the closing """

# Simple approach: replace """\n with """\n (ensure there's a newline after closing quotes)
# But we need to be careful not to add extra newlines where they already exist

# Better approach: find """ followed by non-whitespace and add \n after the """
pattern = re.compile(b'(?<!\\n)("""[^"]*""")(?=[^\\s])')
content = re.sub(pattern, b'\\1\n', content)

# Also fix the specific case in get_observation_learner
content = content.replace(
    b'    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner(',
    b'    """Factory to create an ObservationLearner instance."""\n    return ObservationLearner(\r'
)

# Write back
with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'wb') as f:
    f.write(content)

print('Fixed docstring issues')