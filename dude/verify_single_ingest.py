import sys

sys.path.insert(0, r'E:\Dude\dude')

from core.orchestrator.observation_learner import (
    ObservationLearner, PatternDetector, ObservedPattern,
)
from core.orchestrator.perception_service import (
    SemanticObservation, ChangeClass,
)

# Definition-count check
import inspect
src = inspect.getsource(PatternDetector)
assert src.count('def add_observation') == 1, 'add_observation duplicated'
assert src.count('def _check_pattern') == 1, '_check_pattern duplicated'
lsrc = inspect.getsource(ObservationLearner)
assert lsrc.count('def _process_new_observations') == 1, '_process_new_observations duplicated'
assert lsrc.count('def process_pending') == 1, 'process_pending duplicated'
assert lsrc.count('self._detector.add_observation(obs, goal=goal)') == 1, \
    'ingestion call duplicated'
assert 'self._detector.add_observation(obs)' not in lsrc.replace(
    'self._detector.add_observation(obs, goal=goal)', ''), \
    'bare ingestion call still present'
print('definition counts: add_observation=1 _check_pattern=1 '
      '_process_new_observations=1 process_pending=1 ingestion_calls=1')

# Single-ingestion behavior check with a real SemanticObservation
det = PatternDetector()
obs = SemanticObservation(
    captured_at=1.0, active_app='calc.exe', window_title='Calculator',
    hwnd=11, wclass='', focused_name='Two', focused_ctype='ButtonControl',
    dialog_kind='none', change=ChangeClass.ACTION_RELEVANT.value,
    summary='t', confidence=0.8, source='poll', related='',
    delta={}, csig='sig', rsig=('x',),
    controls_summary='2 controls', control_types=['ButtonControl'])
n0 = len(det._recent_observations)
det.add_observation(obs, goal='g1')
n1 = len(det._recent_observations)
assert n1 - n0 == 1, f'one observation must yield one ingestion, got {n1 - n0}'
assert len(det._patterns) == 1, 'exactly one pattern expected'
p = next(iter(det._patterns.values()))
assert p.frequency == 1, f'frequency must be 1, got {p.frequency}'
assert p.contexts[-1].get('goal') == 'g1', 'goal tag missing'
print(f'single ingestion OK: recent={n1} patterns=1 freq=1 goal_tagged=True')
print('ALL CHECKS PASSED')