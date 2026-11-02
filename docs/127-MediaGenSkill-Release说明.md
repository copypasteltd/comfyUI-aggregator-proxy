# Media Gen Skill 1.2.0

[在线单文件 / Current skill.md](https://gen.sub2share.xyz/skill.md) · [固定 1.2.0 / Immutable version](https://gen.sub2share.xyz/skills/media-gen/1.2.0/skill.md) · [GitHub Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.2.0)

1.2.0 新增单文件分发：完整说明、API 参考和 Python 3.10+ 标准库客户端均嵌入 `skill.md`。保存为 Agent 的 `media-gen-skill/SKILL.md` 并重新打开会话，无需额外下载脚本或参考文件。需要 CLI 时按文内命令独占提取 `media_gen.py`；原生 HTTP 工具可直接使用 API 参考。先设置自己的业务 Key，再按实时模型说明调用；保留旧版的图片、视频、音频、静态 GLB、幂等恢复与下载校验能力。

Version 1.2.0 adds one-file distribution with instructions, API reference and the complete standard-library client embedded. Save it as `media-gen-skill/SKILL.md` and reload the session. Extract the client for CLI use, or use native HTTP tools directly. Supply your own business API Key and discover live models before submitting. Existing generation, permissions, recovery and verified download behavior remain supported.

正式发行四项资产：`media-gen-skill-1.2.0.zip`、`skill.md`、`SHA256SUMS.txt`、`release.json`。ZIP 仍保留原始六个成员；新增单文件作为独立发行资产，清单绑定两种分发形式的来源，摘要覆盖 ZIP 和单文件。构建器固定 ZIP 创建系统并规范单文件换行，便于 Windows/Linux 复现。验证记录和实际部署结果见 [131](131-单文件Skill与固定在线地址.md)，安装升级见 [126](126-MediaGenSkill安装与调用.md)。

The four assets provide the ZIP, single file, checksums and source manifest. Both distributions bind to one committed source revision, with deterministic ZIP platform metadata and single-file newlines. See document 131 for build/publication verification. Preserve credentials, recovery state and outputs outside the installation directory during updates.

固定地址通过独立静态目录和 Nginx 提供；current 原子切换，版本目录不可覆盖，验证失败恢复原配置和指针。Git 标签触发 GitHub 四项资产发行；**只推标签不会自动更新线上固定入口**，维护者需再执行文档 131 的原生静态部署步骤。已安装的文件需主动下载更新并重新加载；不会自动升级服务端或 Worker。

The tag workflow publishes GitHub assets. Maintainers separately run the native static publisher in document 131 to update the fixed URL. Publication preserves immutable version directories and restores the prior pointer/config if verification fails. Installed Skills require an explicit update and session reload.

## 历史 1.1.1 / Previous release record

[历史 1.1.1](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.1.1) · [历史 1.1.0](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.1.0) · [历史 1.0.0](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.0.0)

1.1.1 同步云端 129.3 的 3D 兼容修复：补齐最小权限、单图请求、实际几何字段与 GLB 下载命名；3D 使用 `meshes:create`，不需要 `images:create`。查询 `comfy-3080-3d-trellis2`，传入 `--source-image`，不传文字提示或图片尺寸。三级 3D 额度仍需显式开启；成功返回自包含静态 GLB。Agent 展示、客户端 User-Agent 和版本同步，原有图片、视频和音频保持支持。

Version 1.1.1 aligns with cloud 129.3 mesh-only permissions, exact image-to-3D inputs, geometry metadata and canonical GLB downloads. It preserves previous media calls and request recovery. A mesh caller does not need images:create. Current validation and public asset provenance are recorded in [130](130-MediaGenSkill兼容同步与发布.md); previous live GPU/client evidence remains in [128](128-ComfyUI三维模型产物与3080适配.md) and [129](129-三维兼容复查与GLB边界修复.md).

正式发行的三项资产已匿名重新下载，公开摘要、标签提交、清单、全部文件和独立 CLI help 核对通过；本轮 11 项客户端回归通过。[公开验证证据 / public verification](evidence/2026-10-29/media-gen-skill-130-public-release.json)。Windows 与 Linux ZIP 仅 creator-platform 头字段有差异，内容相同；使用公开 Release 的摘要核验下载文件。

All three public assets were independently downloaded and checked against the public checksum, source tag, manifest and exact file payloads; isolated CLI help and 11 local client regressions pass. Windows/Linux ZIP creator-platform fields differ, so verify a downloaded archive against the checksum from its own Release.

通过 `https://gen.sub2share.xyz` 或自定义部署发现并调用图片、视频和 ComfyUI Workflow 音频模型。纯 Python 3.10+ 标准库客户端，包含模型说明查询、参考图上传、异步生成、原请求恢复、结果下载及 SHA-256 校验。默认服务地址不包含凭证，使用者自行设置 `MEDIA_GEN_API_KEY`。

Discover image, video and ComfyUI Workflow audio models at `https://gen.sub2share.xyz` or another configured deployment. The Python 3.10+ standard-library client supports model usage discovery, reference uploads, asynchronous jobs, exact-request recovery, authenticated downloads and SHA-256 verification. Supply your own business API Key through `MEDIA_GEN_API_KEY`.

先校验 `SHA256SUMS.txt`，解压 ZIP，将 `media-gen-skill/` 放入 Agent 的 Skill 目录，或直接运行其 `scripts/media_gen.py`。`release.json` 记录来源提交和每个文件的摘要。先执行 `models`，再执行 `describe MODEL_ALIAS`，按实时 usage 的参数与规格调用。公开模型由 API Key 权限决定。

Verify the checksum, extract the archive and install the `media-gen-skill/` directory in your agent, or run its client directly. `release.json` records the source commit and per-file hashes. Run `models`, then `describe MODEL_ALIAS`, and follow its current usage schema and presets. Visibility depends on your API Key.

- [安装与完整调用说明 / installation and usage](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/main/docs/126-MediaGenSkill安装与调用.md)
- [平台说明 / platform README](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/main/README.md)
- [English README](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/main/README.en.md)
- [更新说明 / changelog](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/main/CHANGELOG.md)

生成采用异步任务。等待超时不取消服务端任务；保留请求状态文件并用 `resume` 恢复，避免重复生成。产物通常在成功后八小时过期，请及时下载。当前音乐模型的歌词由冻结流程提供；支持的媒体、参考数量和参数以模型发现结果为准。

Generation is asynchronous. A polling timeout does not cancel the server job: retain the state file and use `resume`. Outputs normally expire eight hours after success. The currently published music model has fixed Workflow lyrics; discover actual media capabilities, reference counts and parameters before submitting.
