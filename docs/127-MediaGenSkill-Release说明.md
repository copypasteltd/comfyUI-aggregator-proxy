# Media Gen Skill 1.1.0

[当前正式发布 / Current release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.1.0) · [历史 1.0.0 / Previous release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.0.0)

1.1.0 新增单图生成 3D：查询 `comfy-3080-3d-trellis2`，传入 `--source-image`，不传文字提示或图片尺寸。需要独立 `meshes:create` 和三级 3D 额度；成功返回自包含静态 GLB。原有图片、视频和音频保持支持。

Version 1.1.0 adds image-to-3D: discover the approved model, supply `--source-image`, omit prompts and image dimensions, then retrieve a verified static GLB. Independent mesh permissions and quotas apply. The public archive was independently downloaded, checked against its SHA-256 manifest and compared with the live-tested client. [Evidence](evidence/2026-10-29/model3d-skill-public-release-128.json).

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
