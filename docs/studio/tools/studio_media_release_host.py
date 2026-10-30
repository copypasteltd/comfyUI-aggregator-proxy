"""Bind the existing audited static controller to the media-only 149 release."""
from pathlib import Path

import studio_safe_release_host as control

control.TASK = Path('/var/lib/infinite-canvas-deploy/149-media-release')
control.ORIGINAL = 'canvas-20261030.146.1'
control.NEW = 'canvas-20261031.149.1'
control.RECOVERY = 'canvas-20261031.149.0'

if __name__ == '__main__':
    control.main()
