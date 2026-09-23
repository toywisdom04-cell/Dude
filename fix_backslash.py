with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the backslash escaping issue
old = 'param_type = "path" if "/" in value or "\\" in value else "filename"'
new = 'param_type = "path" if "/" in value or "\\\\" in value else "filename"'

if old in content:
    content = content.replace(old, new)
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed the backslash escaping')
else:
    print('Pattern not found')
    idx = content.find('param_type = "path" if')
    if idx >= 0:
        print(content[idx:idx+100])