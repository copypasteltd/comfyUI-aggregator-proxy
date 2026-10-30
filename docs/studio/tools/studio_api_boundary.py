"""Freeze/check API workspaces read-only while delivering the independent canvas."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / '.local/standalone-api-baseline.json'
REPOSITORIES = {'api_delivery': ROOT / 'aggregator-service', 'original_api': ROOT.parent / '生图生视API封装'}


def snapshot() -> dict:
    result = {}
    for name, directory in REPOSITORIES.items():
        def git(*arguments):
            return subprocess.check_output(['git', *arguments], cwd=directory)
        result[name] = {'head': git('rev-parse', 'HEAD').decode().strip(),
                        'status_sha256': hashlib.sha256(git('status', '--porcelain=v1', '-z', '--untracked-files=normal')).hexdigest(),
                        'working_diff_sha256': hashlib.sha256(git('diff', '--binary', 'HEAD')).hexdigest()}
    return result


def main():
    actual = snapshot()
    if sys.argv[1:] == ['capture']:
        with BASELINE.open('x', encoding='utf-8') as target:
            target.write(json.dumps(actual, indent=2) + '\n')
        print(json.dumps({'api_baseline_recorded': True, 'api_workspaces_written': False}))
        return
    if sys.argv[1:] != ['check']:
        raise SystemExit('Choose capture or check')
    expected = json.loads(BASELINE.read_text(encoding='utf-8'))
    passed = actual == expected
    result = {'passed': passed, 'at': datetime.now(UTC).isoformat(), 'api_workspaces': actual,
              'api_workspaces_written': False, 'api_changes_allowed': False,
              'scope': 'Git HEAD, tracked diff and worktree status compared with the independent canvas baseline; no runtime/API requests performed'}
    target = ROOT / 'docs/research/studio-implementation' / ('independent-api-boundary-' + uuid4().hex + '.json')
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': passed, 'report': target.relative_to(ROOT).as_posix(), 'api_workspaces_written': False}))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
