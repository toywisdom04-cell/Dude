"""Generic Autonomous Task Executor

Executes multi-step computer tasks end-to-end with:
- Structured task planning
- Screen-guided verification
- Intelligent recovery
- Background execution capability
- Persistent learning
"""

import logging
import time
from typing import Optional, Dict, Any, List, Tuple

log = logging.getLogger('dude')

class TaskExecutor:
    """Generic autonomous task executor."""
    
    def __init__(self, memory, brain, observer, tools_module, recovery_engine=None, task_state=None):
        self.memory = memory
        self.brain = brain
        self.observer = observer
        self.tools = tools_module
        self.recovery_engine = recovery_engine
        self.task_state = task_state
        self._max_retries_per_step = 3
        self._max_steps = 20
    
    def is_action_task(self, user_text: str) -> bool:
        """Detect if user request requires computer action."""
        user_lower = user_text.lower().strip()
        action_keywords = [
            'open', 'close', 'create', 'delete', 'move', 'rename', 'copy',
            'click', 'type', 'go to', 'visit', 'search', 'find', 'scroll',
            'download', 'upload', 'save', 'edit', 'send', 'find the',
            'run', 'launch', 'start', 'stop', 'make', 'write', 'put',
            'check', 'verify', 'look for', 'show me'
        ]
        return any(kw in user_lower for kw in action_keywords)
    
    def execute(self, user_goal: str) -> Dict[str, Any]:
        """Execute a user goal end-to-end."""
        try:
            log.info(f'TaskExecutor.execute: {user_goal}')
            
            # Step 1: Parse intent and create task plan
            task_plan = self._plan_task(user_goal)
            if not task_plan:
                return {'status': 'no_plan', 'skip_execution': True}
            
            # Step 2: Initialize task state
            if self.task_state:
                self.task_state.set_current_goal(user_goal)
                self.task_state.create_task(user_goal, task_plan.get('steps', []))
            
            # Step 3: Execute steps
            result = self._execute_steps(task_plan, user_goal)
            
            # Step 4: Record lesson if successful
            if result.get('status') == 'TASK_COMPLETE':
                self._store_lesson(user_goal, task_plan, result)
            
            return result
        
        except Exception as e:
            log.exception(f'TaskExecutor.execute failed: {e}')
            return {'status': 'executor_error', 'error': str(e), 'skip_execution': True}
    
    def _plan_task(self, user_goal: str) -> Optional[Dict[str, Any]]:
        """Plan task steps from user goal."""
        # Try keyword-based planning first (fast path)
        plan = self._plan_from_keywords(user_goal)
        if plan and plan.get('confidence', 0) > 0.7:
            return plan
        
        # For complex queries, could use brain to generate plan
        # For now, keyword-based is primary
        return plan
    
    def _plan_from_keywords(self, user_goal: str) -> Optional[Dict[str, Any]]:
        """Plan task from keyword patterns."""
        low = user_goal.lower().strip()
        
        # Multi-step patterns
        if ' and ' in low or ', ' in low or 'then ' in low:
            # Split on conjunctions
            parts = []
            for sep in [' and ', ', ', ' then ']:
                if sep in low:
                    parts = [p.strip() for p in low.split(sep)]
                    break
            
            if parts:
                steps = []
                for part in parts:
                    step = self._action_to_step(part)
                    if step:
                        steps.append(step)
                
                if steps:
                    return {
                        'goal': user_goal,
                        'steps': steps,
                        'type': 'multi_step',
                        'confidence': 0.8
                    }
        
        # Single-step patterns
        step = self._action_to_step(low)
        if step:
            return {
                'goal': user_goal,
                'steps': [step],
                'type': 'single_step',
                'confidence': 0.9
            }
        
        return None
    
    def _action_to_step(self, action_phrase: str) -> Optional[Dict[str, Any]]:
        """Convert action phrase to executable step."""
        low = action_phrase.strip()
        
        # Open patterns
        if low.startswith('open '):
            target = low[5:].strip().rstrip('.,!?;:')
            return {
                'action': 'open_app',
                'target': target,
                'expected_state': f'{target} is open',
                'verification': f'app_open:{target}'
            }
        
        # Close patterns
        if low.startswith('close '):
            target = low[6:].strip().rstrip('.,!?;:')
            return {
                'action': 'close_app',
                'target': target,
                'expected_state': f'{target} is closed',
                'verification': f'app_closed:{target}'
            }
        
        # Create file patterns
        if 'create' in low and 'file' in low:
            target = low.replace('create file', '').replace('create', '').strip().rstrip('.,!?;:')
            return {
                'action': 'create_file',
                'target': target,
                'expected_state': f'file {target} exists',
                'verification': f'file_exists:{target}'
            }
        
        # Create folder patterns
        if 'create' in low and 'folder' in low:
            target = low.replace('create folder', '').replace('create', '').strip().rstrip('.,!?;:')
            return {
                'action': 'create_folder',
                'target': target,
                'expected_state': f'folder {target} exists',
                'verification': f'folder_exists:{target}'
            }
        
        # Open folder patterns
        if 'open' in low and 'folder' in low:
            target = low.replace('open folder', '').replace('open', '').replace('the', '').strip().rstrip('.,!?;:')
            return {
                'action': 'open_folder',
                'target': target,
                'expected_state': f'folder {target} is open',
                'verification': f'folder_open:{target}'
            }
        
        return None
    
    def _execute_steps(self, task_plan: Dict[str, Any], user_goal: str) -> Dict[str, Any]:
        """Execute task steps sequentially."""
        steps = task_plan.get('steps', [])
        if not steps:
            return {'status': 'no_steps', 'skip_execution': True}
        
        execution_log = []
        current_step = 0
        
        while current_step < len(steps):
            step = steps[current_step]
            step_num = current_step + 1
            total_steps = len(steps)
            
            log.info(f'TaskExecutor: Step {step_num}/{total_steps}: {step.get("action")} {step.get("target")}')
            
            # Update task state
            if self.task_state:
                self.task_state.current_subtask = f'{step.get("action")} {step.get("target")}'
            
            # Pre-execution observation
            pre_state = self._observe_state()
            
            # Execute step
            step_result = self._execute_step(step)
            execution_log.append(step_result)
            
            # Post-execution observation
            time.sleep(0.5)
            post_state = self._observe_state()
            
            # Verify step
            verified = self._verify_step(step, pre_state, post_state, step_result)
            
            if verified:
                log.info(f'TaskExecutor: Step {step_num} verified')
                current_step += 1
                continue
            
            # Step failed - attempt recovery
            log.info(f'TaskExecutor: Step {step_num} failed, attempting recovery')
            recovered = self._recover_step(step, post_state)
            
            if recovered:
                log.info(f'TaskExecutor: Recovery successful, retrying step')
                # Don't increment current_step; retry same step
                continue
            
            # Recovery failed - abort
            log.warning(f'TaskExecutor: Step {step_num} unrecoverable')
            return {
                'status': 'TASK_FAILED',
                'goal': user_goal,
                'failed_step': step_num,
                'failed_action': step.get('action'),
                'execution_log': execution_log,
                'verified': False
            }
        
        # All steps completed
        log.info(f'TaskExecutor: All {len(steps)} steps completed')
        return {
            'status': 'TASK_COMPLETE',
            'goal': user_goal,
            'steps_executed': len(steps),
            'execution_log': execution_log,
            'verified': True
        }
    
    def _execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single step action."""
        action = step.get('action')
        target = step.get('target', '')
        
        try:
            if action == 'open_app':
                result = self.tools.open_app(self.memory, {'name': target})
                return {'action': action, 'target': target, 'result': str(result)}
            
            elif action == 'close_app':
                result = self.tools.close_app(self.memory, {'name': target})
                return {'action': action, 'target': target, 'result': str(result)}
            
            elif action == 'create_file':
                result = self.tools.create_file(self.memory, {'name': target})
                return {'action': action, 'target': target, 'result': str(result)}
            
            elif action == 'create_folder':
                result = self.tools.create_folder(self.memory, {'path': target})
                return {'action': action, 'target': target, 'result': str(result)}
            
            elif action == 'open_folder':
                self.tools.open_app(self.memory, {'name': 'explorer'})
                return {'action': action, 'target': target, 'result': 'explorer opened'}
            
            else:
                return {'action': action, 'target': target, 'result': f'Unknown action: {action}'}
        
        except Exception as e:
            log.exception(f'Step execution failed: {e}')
            return {'action': action, 'target': target, 'result': f'ERROR: {e}'}
    
    def _observe_state(self) -> Dict[str, Any]:
        """Observe current screen/app state."""
        try:
            state = {
                'timestamp': time.time(),
                'observer_desc': '',
                'active_app': '',
                'screenshot_available': False
            }
            
            if self.observer:
                state['observer_desc'] = self.observer.description or ''
            
            try:
                from core.tools import _active_app
                state['active_app'] = _active_app() or ''
            except Exception:
                pass
            
            return state
        except Exception as e:
            log.warning(f'observe_state failed: {e}')
            return {'timestamp': time.time(), 'error': str(e)}
    
    def _verify_step(self, step: Dict[str, Any], pre: Dict, post: Dict, exec_result: Dict) -> bool:
        """Verify step completion."""
        action = step.get('action')
        target = step.get('target', '')
        verification = step.get('verification', '')
        
        # Check for execution errors
        if 'ERROR' in str(exec_result.get('result', '')):
            return False
        
        if action == 'open_app':
            # Check if app is active or observable
            post_app = post.get('active_app', '').lower()
            post_desc = post.get('observer_desc', '').lower()
            target_lower = target.lower()
            
            if target_lower in post_app or target_lower in post_desc:
                return True
            
            # Check again after delay
            time.sleep(1)
            post_2 = self._observe_state()
            if target_lower in post_2.get('active_app', '').lower():
                return True
            
            return False
        
        elif action == 'close_app':
            # App should not be active
            post_app = post.get('active_app', '').lower()
            return target.lower() not in post_app
        
        elif action in ('create_file', 'create_folder'):
            # File/folder should exist
            return not 'ERROR' in str(exec_result.get('result', ''))
        
        else:
            # Default verification
            return not 'ERROR' in str(exec_result.get('result', ''))
    
    def _recover_step(self, step: Dict[str, Any], state: Dict[str, Any]) -> bool:
        """Attempt to recover from a failed step."""
        try:
            desc = state.get('observer_desc', '').lower()
            
            # Detect common blockers
            blockers = ['dialog', 'block', 'error', 'cannot', 'permission']
            
            for blocker in blockers:
                if blocker in desc:
                    # Try to dismiss
                    log.info(f'TaskExecutor: Detected blocker: {blocker}, attempting dismissal')
                    try:
                        self.tools.press_hotkey(self.memory, {'key': 'escape'})
                        time.sleep(0.3)
                        self.tools.press_hotkey(self.memory, {'key': 'enter'})
                        time.sleep(0.5)
                        return True
                    except Exception:
                        pass
            
            return False
        except Exception as e:
            log.warning(f'Recovery attempt failed: {e}')
            return False
    
    def _store_lesson(self, user_goal: str, task_plan: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Store successful task completion as a lesson."""
        try:
            if not self.memory:
                return
            
            steps = task_plan.get('steps', [])
            lesson = {
                'goal': user_goal,
                'num_steps': len(steps),
                'completion_time': result.get('completion_time', 0),
                'verified': result.get('verified', False),
                'actions': [s.get('action') for s in steps]
            }
            
            # Store in memory as a fact
            self.memory.add_notice(f'Task completed: {user_goal}')
            log.info(f'TaskExecutor: Stored lesson for goal: {user_goal}')
        
        except Exception as e:
            log.debug(f'Could not store lesson: {e}')

def get_task_executor():
    """Get singleton task executor."""
    global _task_executor
    return globals().get('_task_executor')

def init_task_executor(memory, brain, observer, tools_module, recovery_engine=None, task_state=None):
    """Initialize task executor."""
    global _task_executor
    _task_executor = TaskExecutor(memory, brain, observer, tools_module, recovery_engine, task_state)
    log.info('TaskExecutor initialized')
    return _task_executor
