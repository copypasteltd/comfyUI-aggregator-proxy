"""Apply an explicit independent static release plan using the existing audited controller."""
import json
from pathlib import Path
import re

import studio_safe_release_host as control

control.TASK = Path(__file__).resolve().parent
plan = json.loads((control.TASK / 'release-plan.json').read_text())
assert set(plan) == {'original', 'new', 'recovery', 'operation'}
assert all(re.fullmatch(r'canvas-\d{8}\.\d+\.\d+', plan[name]) for name in ['original', 'new', 'recovery'])
assert plan['recovery'].endswith('.0') and len({plan[name] for name in ['original', 'new', 'recovery']}) == 3
assert re.fullmatch(r'[a-z0-9-]+', plan['operation'])
control.ORIGINAL, control.NEW, control.RECOVERY = [plan[name] for name in ['original', 'new', 'recovery']]

if __name__ == '__main__':
    control.main()
