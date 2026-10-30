#!/bin/bash
set -euo pipefail
umask 022
task_root=/var/lib/comfyui-aggregator-deploy/studio137
trap 'printf "failed\n" > "$task_root/prepare.state"' ERR
printf 'running\n' > "$task_root/prepare.state"
python3 "$task_root/studio137-host.py" extract
release=/opt/comfyui-aggregator/releases/linux-20261030.137.1
export UV_CACHE_DIR=/var/lib/comfyui-aggregator-deploy/uv-cache
export UV_PYTHON_DOWNLOADS=never
export PYTHONDONTWRITEBYTECODE=1
/opt/comfyui-aggregator/tools/uv venv --python /opt/comfyui-aggregator/python/cpython-3.13.1-linux-x86_64-gnu/bin/python3.13 "$release/venv"
cd "$release"
/opt/comfyui-aggregator/tools/uv pip sync --python "$release/venv/bin/python" --offline --require-hashes --only-binary :all: --link-mode copy linux-runtime-requirements.txt
/opt/comfyui-aggregator/tools/uv pip install --python "$release/venv/bin/python" --offline --no-deps --no-index --find-links wheels --require-hashes --link-mode copy --requirement project-requirements.txt
/opt/comfyui-aggregator/tools/uv pip check --python "$release/venv/bin/python"
"$release/venv/bin/python" -B -c 'import control_api.main; from generation_contracts.device_pool import DeviceRouting; print("native_device_pool_imports_ok")'
python3 "$task_root/studio137-host.py" verify-installed
printf 'prepared\n' > "$task_root/prepare.state"
