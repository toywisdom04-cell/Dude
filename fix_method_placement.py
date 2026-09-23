with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the WorkflowCandidateBuilder class
class_start = content.find('class WorkflowCandidateBuilder:')
next_class = content.find('class ', class_start + 1)
if next_class == -1:
    next_class = len(content)

class_content = content[class_start:next_class]

# Find the _generate_goal method (should be the last method in the class)
goal_method = content.find('def _generate_goal', class_start)
if goal_method == -1:
    goal_method = content.find('def _generate_goal')
    
# Find the end of the _generate_goal method (where the class ends)
# The class ends with the _generate_goal method and then blank line + class end
# Find the end of the class (before the next class or module-level function)
class_end = next_class

# Now find the _extract_parameters method (which is at module level)
extract_idx = content.find('def _extract_parameters')
if extract_idx == -1:
    print("ERROR: _extract_parameters not found")
    exit(1)

# The method is at module level, we need to move it inside the class
# Find the end of the _generate_goal method (which should be the last method in the class)
# The class ends right before the next class definition

# Strategy: 
# 1. Remove the module-level _extract_parameters method
# 2. Insert it inside the WorkflowCandidateBuilder class, right before the class ends

# Find the exact boundaries
# The _generate_goal method ends with "return f\"In {pattern.app}, {actions}\""
# Then there's a blank line, then the class ends

# Find the end of the _generate_goal method
gen_goal_start = content.find('def _generate_goal', class_start)
gen_goal_end = content.find('\n\n', content.find('return f"In {pattern.app}, {actions}"', gen_goal_start))
if gen_goal_end == -1:
    gen_goal_end = content.find('\n\nclass ', gen_goal_start)
if gen_goal_end == -1:
    gen_goal_end = content.find('\n\n', content.find('return f"In {pattern.app}, {actions}"', gen_goal_start) + 50)

# The class ends right after _generate_goal method
class_end = content.find('\n\nclass ', content.find('def _generate_goal', class_start))
if class_end == -1:
    class_end = content.find('\n\n', content.find('return f"In {pattern.app}, {actions}"', content.find('def _generate_goal', class_start)) + 50)

print(f"Class start: {class_start}")
print(f"Class end: {class_end}")
print(f"Extract params at: {content.find('def _extract_parameters')}")

# Show the end of class area
idx = content.find('def _generate_goal')
end_area = content[content.find('def _generate_goal'):content.find('def _generate_goal')+500]
print("End of class area:")
print(end_area[:500])