import sys
sys.path.insert(0, r'E:\Dude\dude')
from core.screentree import ScreenMap
from core.orchestrator.perception import PerceptionEngine
import time

st = ScreenMap()
time.sleep(2)
print('Rows:', len(st.rows))

pe = PerceptionEngine(get_screentree=lambda: st)
snap = pe.observe(force_refresh=True)
print('Controls:', len(snap.controls))
for c in snap.controls[:3]:
    cls_name = getattr(c, 'ClassName', 'N/A')
    print(f'  {c.ctype} | {c.name} | Class={c.ClassName if hasattr(c, "ClassName") else "N/A"}')