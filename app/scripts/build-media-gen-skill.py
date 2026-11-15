"""Build a reproducible, credential-free Skill archive from committed sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
SKILL = APP / "skills/media-gen-skill"
FILES = ("SKILL.md", "VERSION", "agents/openai.yaml", "references/api.md", "references/comfy-origin.md", "scripts/media_gen.py", "scripts/comfy_origin.py")
SINGLE_FILE_URL = "https://gen.sub2share.xyz/skill.md"


def render_single_file(payload: dict[str, bytes], version: str) -> bytes:
    """Compile an installable Skill without external scripts or reference files."""
    entry = payload["SKILL.md"].decode("utf-8").replace("\r\n", "\n")
    reference = payload["references/api.md"].decode("utf-8").replace("\r\n", "\n")
    native_reference = payload["references/comfy-origin.md"].decode("utf-8").replace("\r\n", "\n")
    client = payload["scripts/media_gen.py"].decode("utf-8").replace("\r\n", "\n")
    native_client = payload["scripts/comfy_origin.py"].decode("utf-8").replace("\r\n", "\n")
    if not entry.startswith("---\n") or entry.count("\n---\n") != 1 or "```python" in entry + reference + native_reference or "```" in client + native_client:
        raise ValueError("Skill inputs cannot be safely compiled into one Markdown file")
    header, body = entry[4:].split("\n---\n", 1)
    header += f'\nmetadata:\n  version: "{version}"\n  homepage: "{SINGLE_FILE_URL}"'
    body = body.replace("scripts/media_gen.py", "media_gen.py")
    body = body.replace("scripts/comfy_origin.py", "comfy_origin.py")
    body = body.replace("The bundled Python 3.10+ clients use the standard library.", "The two Python 3.10+ clients embedded below use the standard library. Extract them before using CLI examples, or use the HTTP API directly.")
    body = body.replace("(references/api.md#image-to-3d-compatibility)", "(#image-to-3d-compatibility)").replace("(references/api.md)", "(#native-api-reference)")
    body = body.replace("(references/comfy-origin.md)", "(#direct-comfyui-reference)")
    body = body.replace("Read [references/api.md]", "Read [the embedded API reference]")
    if re.search(r"\]\((?!https?://|#)[^)]+\)", body + reference + native_reference):
        raise ValueError("Single-file Skill still depends on a local reference")
    reference = reference.removeprefix("# API use and recovery\n").lstrip()
    native_reference = native_reference.removeprefix("# Direct ComfyUI operation\n").lstrip().replace("scripts/comfy_origin.py", "comfy_origin.py")
    introduction = f'''## 单文件安装与更新 / Single-file installation

版本 / Version: **{version}**. 最新固定地址 / Current URL: **{SINGLE_FILE_URL}**.

把本文件保存为 Agent Skill 目录中的 `media-gen-skill/SKILL.md`，重新打开会话。说明、业务/原生 ComfyUI 参考和两个完整客户端均在本文中，无需下载其他文件。用户可按任务直接操控获准设备的 ComfyUI；管理员需先独立授权设备。文件不包含凭证或模型权重。更新时从同一固定地址获取新版并重新加载；保留自己的凭证、请求状态和下载素材。

Save this file as `media-gen-skill/SKILL.md` in your agent's skills directory and open a new session. It includes business/native ComfyUI references and both clients. Direct ComfyUI graph operations are permitted for the user's task on explicitly granted devices. Keep credentials, native sessions, recovery state and outputs outside the installation directory. The current URL serves the newest deployed version; historical versions use `/skills/media-gen/VERSION/skill.md`.

For CLI use, extract the embedded blocks as `media_gen.py` (business models) and `comfy_origin.py` (direct ComfyUI). Run the following in a new task directory containing `SKILL.md`; exclusive creation preserves existing scripts. HTTP tools may use the references directly without extraction. After updating the Skill, extract into a new directory or deliberately update old extracted scripts.

```bash
python -c "from pathlib import Path; text=Path('SKILL.md').read_text(encoding='utf-8'); blocks=text.split(chr(96)*3+'python'+chr(10))[1:]; assert len(blocks)==2; [Path(name).open('x',encoding='utf-8').write(block.split(chr(96)*3,1)[0]) for name,block in zip(('media_gen.py','comfy_origin.py'),blocks)]"
```

'''
    rendered = "---\n" + header + "\n---\n" + introduction + body.lstrip() + "\n\n## Native API reference\n\n" + reference.rstrip() + "\n\n## Direct ComfyUI reference\n\n" + native_reference.rstrip() + "\n\n## Embedded Python client\n\n```python\n" + client.rstrip() + "\n```\n\n## Embedded ComfyUI client\n\n```python\n" + native_client.rstrip() + "\n```\n"
    compile(client, "media_gen.py", "exec")
    compile(native_client, "comfy_origin.py", "exec")
    return rendered.encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(APP / ".local") or destination.exists():
        raise SystemExit("Choose a new output directory under app/.local")
    git = shutil.which("git.exe") or shutil.which("git")
    if git is None:
        raise SystemExit("Native Git is required")
    revision = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()  # noqa: S603 -- fixed native Git arguments
    payload = {name: (SKILL / name).read_bytes() for name in FILES}
    for name, data in payload.items():
        tracked = subprocess.check_output([git, "show", revision + ":app/skills/media-gen-skill/" + name], cwd=ROOT)  # noqa: S603 -- fixed native Git arguments
        if tracked != data:
            raise SystemExit("Commit Skill sources before packaging")
    builder = Path(__file__).read_bytes()
    if subprocess.check_output([git, "show", revision + ":app/scripts/build-media-gen-skill.py"], cwd=ROOT) != builder:  # noqa: S603 -- fixed native Git arguments
        raise SystemExit("Commit the Skill builder before packaging")
    version = payload["VERSION"].decode().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise SystemExit("Invalid Skill version")
    compile(payload["scripts/media_gen.py"], "media_gen.py", "exec")
    compile(payload["scripts/comfy_origin.py"], "comfy_origin.py", "exec")
    standalone = render_single_file(payload, version)
    subprocess.run([sys.executable, str(SKILL / "scripts/media_gen.py"), "--help"], check=True, stdout=subprocess.DEVNULL, timeout=10)  # noqa: S603 -- packaged client help, no credentials or network
    subprocess.run([sys.executable, str(SKILL / "scripts/comfy_origin.py"), "--help"], check=True, stdout=subprocess.DEVNULL, timeout=10)  # noqa: S603 -- native client help, no credentials or network
    record = {"format_version": 1, "name": "media-gen-skill", "version": version, "source_revision": revision,
              "default_base_url": "https://gen.sub2share.xyz", "python_minimum": "3.10", "external_dependencies": [],
              "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())},
              "builder_sha256": hashlib.sha256(builder).hexdigest(),
              "single_file": {"name": "skill.md", "bytes": len(standalone), "sha256": hashlib.sha256(standalone).hexdigest(), "url": SINGLE_FILE_URL}}
    payload["release.json"] = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
    destination.mkdir(parents=True)
    archive = destination / f"media-gen-skill-{version}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo("media-gen-skill/" + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, data)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "skill.md").write_bytes(standalone)
    (destination / "SHA256SUMS.txt").write_text(checksum + "  " + archive.name + "\n" + hashlib.sha256(standalone).hexdigest() + "  skill.md\n", encoding="utf-8", newline="\n")
    (destination / "release.json").write_bytes(payload["release.json"])
    with zipfile.ZipFile(archive) as check:
        assert len(check.namelist()) == len(payload)
        assert check.testzip() is None
        assert all(check.read("media-gen-skill/" + name) == data for name, data in payload.items())
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "sha256": checksum, "version": version, "source_revision": revision}))


if __name__ == "__main__":
    main()
