with open(r'E:\Dude\dude\core\orchestrator\action_executor.py', 'r') as f:
    content = f.read()

old = '''if _fctype and _fctype not in ("EditControl", "DocumentControl"):
                return ActionExecutionResult(
                    success=False,
                    status=ExecutionStatus.NOT_GROUNDED,
                    message=f"Focused control is {_fctype} "
                            f"{_fcname[:40]!r}, not an editable control",
                    error="Refusing to type outside editable controls",
                    grounded_method=grounded.method,
                    grounded_coordinates=grounded.coordinates,
                    grounded_confidence=grounded.confidence,
                )
            except Exception as e:
                log.warning(f"Focus check unavailable, proceeding: {e}")'''

new_code = '''if _fctype and _fctype not in ("EditControl", "DocumentControl"):
                # If focused control is not editable, try to find and focus an editable control
                log.info(f"Focused control is {_fctype} {_fcname!r}, not editable. Attempting to find editable control...")
                editable_ctrl = self._find_editable_control()
                if editable_ctrl is not None:
                    try:
                        editable_ctrl.SetFocus()
                        log.info(f"Focused editable control: {editable_ctrl.ControlTypeName} {editable_ctrl.Name[:40]!r}")
                        # Re-check focus after setting
                        with auto.UIAutomationInitializerInThread():
                            _fc = auto.GetFocusedControl()
                        _fctype = getattr(_fc, "ControlTypeName", "") or ""
                        _fcname = getattr(_fc, "Name", "") or ""
                        if _fctype not in ("EditControl", "DocumentControl"):
                            return ActionExecutionResult(
                                success=False,
                                status=ExecutionStatus.NOT_GROUNDED,
                                message=f"Failed to focus editable control after attempt",
                                error="Could not focus editable control",
                                grounded_method=grounded.method,
                                grounded_coordinates=grounded.coordinates,
                                grounded_confidence=grounded.confidence,
                            )
                    else:
                        return ActionExecutionResult(
                            success=False,
                            status=ExecutionStatus.NOT_GROUNDED,
                            message=f"Focused control is {_fctype} {_fcname[:40]!r}, not an editable control",
                            error="Refusing to type outside editable controls",
                            grounded_method=grounded.method,
                            grounded_coordinates=grounded.coordinates,
                            grounded_confidence=grounded.confidence,
                        )
            except Exception as e:
                log.warning(f"Focus check unavailable, proceeding: {e}")'''

with open(r'E:\Dude\dude\core\orchestrator\action_executor.py', 'r') as f:
    content = f.read()

if old in content:
    content = content.replace(old, new)
    with open(r'E:\Dude\dude\core\orchestrator\action_executor.py', 'w') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('Pattern not found')
    idx = content.find('if _fctype and _fctype not in')
    if idx >= 0:
        print('Found at:', content[idx:idx+200])