import ast

with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
    source = f.read()

try:
    ast.parse(source)
    print('Full file AST parse successful')
except SyntaxError as e:
    print(f'SyntaxError: {e}')
    print(f'Error at line {e.lineno}, offset {e.offset}')
    with open(r'E:\Dude\dude\core\orchestrator\observation_learner.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    if e.lineno <= len(lines):
        print(f'Line {e.lineno}: {repr(lines[e.lineno-1])}')
        for i in range(max(0, e.lineno-5), min(len(lines), e.lineno+5)):
            print(f'  {i+1}: {repr(lines[i])}')