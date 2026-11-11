with open(r'E:\Dude\dude\core\orchestrator\intelligence_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the indentation issue
old = '''        if type_text:
                # Fresh untitled tab first: typing must never land in one of
                # the user's restored documents. The Untitled title check
                # after each click proves the new tab is the active one.
                steps.append(SubGoal(
                    description="Open a new blank tab",
                    intent=intent,
                    action_type="click",
                    target_description="Add New Tab | ButtonControl",
                    expected_result="Untitled",
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))
                # Ground + focus the real editable control, then prove
                # the focus (typing goes to the focused control, so this
                # is what makes the following keystrokes trustworthy)...
                steps.append(SubGoal('''

new = '''            if type_text:
                # Fresh untitled tab first: typing must never land in one of
                # the user's restored documents. The Untitled title check
                # after each click proves the new tab is the active one.
                steps.append(SubGoal(
                    description="Open a new blank tab",
                    intent=intent,
                    action_type="click",
                    target_description="Add New Tab | ButtonControl",
                    expected_result="Untitled",
                    verification_method=VerificationMethod.WINDOW_APPEARED,
                    risk_level=RiskLevel.LOW,
                ))
                # Ground + focus the real editable control, then prove
                # the focus (typing goes to the focused control, so this
                # is what makes the following keystrokes trustworthy)...
                steps.append(SubGoal('''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\orchestrator\intelligence_router.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')