with open(r'E:\Dude\dude\core\orchestrator\intelligence_router.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the calculator-specific section
old = '''            _calc_built = False
            calc_match = re.search(
                r'(?:calculate|compute)\s+(.+?)(?:\s+(?:and|then|,)\s+|$)',
                intent, re.IGNORECASE)
            if calc_match and not _reuse_doc:
                expr = calc_match.group(1).strip()
                # Simple expression parsing for basic arithmetic
                # Format: "X + Y", "X - Y", "X * Y", "X / Y", "X / Y"
                # Supports: numbers, +, -, *, /, x, ÷, plus word operators
                # ("times", "plus", "minus", "divided by", ...) so goal
                # phrasing does not change the plan: this is generic
                # language understanding, not an app recipe.
                expr = re.sub(r'\bmultiplied\s+by\b', '*', expr)
                expr = re.sub(r'\bdivided\s+by\b', '/', expr)
                expr = re.sub(r'\btimes\b', '*', expr)
                expr = re.sub(r'\bmultiply\b', '*', expr)
                expr = re.sub(r'\bplus\b', '+', expr)
                expr = re.sub(r'\bminus\b', '-', expr)
                expr = re.sub(r'\bminus\b', '-', expr)
                expr = re.sub(r'\bsubtract\b', '-', expr)
                expr = re.sub(r'\bdivide\b', '/', expr)
                expr = expr.replace('x', '*').replace('÷', '/').replace('x', '*')
                # Parse simple binary expression
                op_match = re.search(r'(\d+(?:\.\d+)?)\s*([+\-*/])\s*(\d+(?:\.\d+)?)', expr)
                if op_match:
                    num1, op, num2 = op_match.groups()
                    # Map operator to Calculator button names
                    op_map = {'+': 'Plus', '-': 'Minus', '*': 'Multiply by', '/': 'Divide by'}
                    op_name = op_map.get(op, op)
                    if not any(((s.action_type or '').lower() == 'open_app'
                                and 'calcul' in (s.target_description or '').lower())
                               for s in steps):
                        steps.append(SubGoal(
                            description="Open Calculator",
                            intent=intent,
                            action_type="open_app",
                            target_description="calculator",
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW,
                        ))
                    # Click number buttons for first operand (by word
                    # name: UIA exposes keypad keys as words, not glyphs).
                    # Clicks assert the app context still holds
                    # (WINDOW_APPEARED on the window title); a pressed key
                    # changes only a small patch of pixels plus the
                    # display elsewhere, which a hash diff cannot see
                    # reliably. The final display read below is the
                    # load-bearing check that the presses landed.
                    # A leading Clear makes the calculation idempotent:
                    # whatever a previous run left on the display, the
                    # expression always starts from a clean state.
                    # The final display read below is the load-bearing check
                    # that the presses landed.
                    # A leading Clear makes the calculation idempotent:
                    # whatever a previous run left on the display, the
                    # expression always starts from a clean state.
                    steps.append(SubGoal(
                        description="Clear calculator",
                        intent=intent,
                        action_type="click",
                        target_description="Clear | ButtonControl",
                        expected_result="Calculator",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW,
                    ))
                    for digit in num1:
                        _word = DIGIT_WORDS.get(digit, digit)
                        steps.append(SubGoal(
                            description=f"Click {_word}",
                            intent=intent,
                            action_type="click",
                            target_description=f"{_word} | ButtonControl",
                            expected_result="Calculator",
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW,
                        ))
                    steps.append(SubGoal(
                        description=f"Click {op_name}",
                        intent=intent,
                        action_type="click",
                        target_description=f"{op_name} | ButtonControl",
                        expected_result="Calculator",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW,
                    ))
                    for digit in num2:
                        _word = DIGIT_WORDS.get(digit, digit)
                        steps.append(SubGoal(
                            description=f"Click {_word}",
                            intent=intent,
                            action_type="click",
                            target_description=f"{_word} | ButtonControl",
                            expected_result="Calculator",
                            verification_method=VerificationMethod.WINDOW_APPEARED,
                            risk_level=RiskLevel.LOW,
                        ))
                    steps.append(SubGoal(
                        description="Click Equals",
                        intent=intent,
                        action_type="click",
                        target_description="Equals | ButtonControl",
                        expected_result="Calculator",
                        verification_method=VerificationMethod.WINDOW_APPEARED,
                        risk_level=RiskLevel.LOW,
                    ))
                    # Optional verify tail ("... and verify the displayed
                    # result is 4"): read the expected text itself. The
                    # read grounds live against any control showing that
                    # text (the display), and TEXT_READ proves it — a
                    # generic self-grounding check that works for any app
                    # showing an expected value, no control names needed.
                    _verify_m = re.search(
                        r'verif\w*\s+(?:that\s+)?(?:the\s+)?'
                        r'(?:displayed\s+|visible\s+)?'
                        r'(?:result|text|content|value)'
                        r'(?:\s+is\s+|\s*:\s*)(.+?)\s*$',
                        intent, re.IGNORECASE)
                    if _verify_m:
                        _expect = _verify_m.group(1).strip().rstrip(" .")
                        if _expect:
                            steps.append(SubGoal(
                                description=f"Verify displayed result {_expect}",
                                intent=intent,
                                action_type="read_text",
                                target_description=_expect,
                                expected_result=_expect,
                                verification_method=VerificationMethod.TEXT_READ,
                                risk_level=RiskLevel.LOW,
                            ))
                    _calc_built = True

            # The plan below drives the REAL Windows GUI through the existing
            # action types only: grounded clicks (UIA name lookup) establish
            # which control receives each step, and keyboard-direct typing
            # delivers keystrokes to the focused control. No step writes
            # files directly; the file appears only as a consequence of the
            # Save dialog interaction.
            if type_text:'''

new = '''if type_text:'''

content = content.replace(old, new)

with open(r'E:\Dude\dude\core\orchestrator\intelligence_router.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')