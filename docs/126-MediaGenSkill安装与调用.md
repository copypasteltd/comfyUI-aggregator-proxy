# 126 · MediaGen Skill 安装与调用 / installation and usage

默认服务 / Default service: `https://gen.sub2share.xyz`。源码 / Source: [media-gen-skill](../app/skills/media-gen-skill/SKILL.md)。纯 Python 3.10+ 标准库，无需 Docker、ComfyUI 本地安装或额外 Python 依赖。

## 安装 / Install

推荐单文件：[最新 skill.md](https://gen.sub2share.xyz/skill.md)；[固定 1.3.0](https://gen.sub2share.xyz/skills/media-gen/1.3.0/skill.md)。下载后命名为 `media-gen-skill/SKILL.md`，放入 Agent 的 Skill 目录并重新打开会话。所有说明、API 参考和客户端均内嵌，无需其他下载。若要执行 CLI，按文件内命令在工作目录提取 `media_gen.py`，下方示例的 `scripts/media_gen.py` 改为 `media_gen.py`。同一固定地址更新；版本入口保持不变，更新不会自动覆盖已安装文件。

For one-file installation, save the [current skill.md](https://gen.sub2share.xyz/skill.md) as `media-gen-skill/SKILL.md` in your agent's skills directory. Open a new session. The API reference and complete client are embedded. To use CLI examples, extract `media_gen.py` following the instructions inside the file and omit `scripts/` from the commands below. Re-fetch the current URL to update; retain private state and reload the session. The [versioned URL](https://gen.sub2share.xyz/skills/media-gen/1.3.0/skill.md) remains immutable. [Release and static deployment](131-单文件Skill与固定在线地址.md).

也可从正式 [Media Gen Skill 1.3.0 Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.3.0) 获取 `media-gen-skill-1.3.0.zip` 和 `SHA256SUMS.txt`，先校验摘要，再解压为 `media-gen-skill/`。Codex 用户可把该目录放到自己的 `$CODEX_HOME/skills/` 或用户 skills 目录后重新打开会话。其他支持 SKILL.md 的 Agent 使用其对应的 Skill 安装目录；也可直接运行脚本。

Download the archive and checksum from this repository's Releases, verify SHA-256, and extract the single `media-gen-skill` directory. Install that directory in your agent's skills folder, or run the client directly. A `release.json` inside the archive binds every file to its source commit. **A local archive is not proof of a published GitHub Release**; current publication and independent asset verification are recorded in [131](131-单文件Skill与固定在线地址.md); historical 1.1.1 evidence remains in [130](130-MediaGenSkill兼容同步与发布.md).

从旧版升级时，先保留自己修改过的源文件和私有状态，解压到独立目录完成校验，再更新已安装 Skill 的发行文件并重新打开会话。不要删除或覆盖原来的请求状态、API Key 配置和已下载内容；建议从一开始就把这些私有数据放到 Skill 安装目录之外。更新此包不升级另一个部署的服务端或 Worker。

To upgrade, preserve local customizations and private state, verify the new archive in a separate directory, then update the installed Skill's distributed files and open a new session. Keep request-state files, credentials and downloaded outputs outside the installation directory. Updating this package does not upgrade another deployment's server or Worker.

从 Admin 创建业务 API Key。根据实际使用赋予 `models:read`、`capabilities:read`、`jobs:create`、`jobs:read`、`artifacts:read`、`content:read`、`content:download`；生成对应 `images:create`、`videos:create`、`audio:create` 或 `meshes:create`，参考图另需 `assets:upload`。3D 还需显式开启 Tenant/Project/Key 的 `daily_mesh_count`；旧策略默认 0。模型、任务类型、项目、配额限制仍由该 Key 决定。不要使用 Admin 密码、Worker token 或网关凭证代替。

Create a business API Key in Admin. Grant the read/job/content scopes above, the appropriate image/video/audio/3D generation scope, and `assets:upload` when references are needed. 3D requires explicit nonzero Tenant/Project/Key daily_mesh_count quotas; existing policies default to zero. **Mesh-only generation does not require images:create**; asset upload permission is separate. Also permit the selected alias/release/channel/task. Model visibility and limits remain scoped to that Key. Never embed real credentials in distributed archives.

PowerShell:

```powershell
$env:MEDIA_GEN_API_KEY = 'YOUR_BUSINESS_API_KEY'
python scripts/media_gen.py models
python scripts/media_gen.py describe MODEL_ALIAS_FROM_LIST
```

POSIX shell:

```sh
export MEDIA_GEN_API_KEY='YOUR_BUSINESS_API_KEY'
python3 scripts/media_gen.py models
python3 scripts/media_gen.py describe MODEL_ALIAS_FROM_LIST
```

这些命令在解压目录执行；源码使用者可从项目根执行 `python app/skills/media-gen-skill/scripts/media_gen.py ...`。可通过 `MEDIA_GEN_BASE_URL` 或顶层 `--base-url https://YOUR_ORIGIN` 修改服务地址，不能包含账号、路径或查询串。Key 只从环境变量读取，不输出到状态文件或错误消息。

## 调用逻辑 / Invocation

| 步骤 / Step | 命令 / Command | 说明 / Meaning |
| --- | --- | --- |
| 查询 / Discover | `models`、`describe ALIAS`、`capabilities` | 读取实时 usage、参数 Schema、尺寸/时长组合、参考槽位及示例，不硬编码所有模型统一参数 |
| 文本生成 / Text | `generate --model ALIAS --prompt "A lime on a table" --wait --output-dir output` | 使用该模型实际公布的默认组合；适用于文生图、文生视频和合法音频提示词 |
| 首帧视频 / First frame | `generate --model ALIAS --prompt "Subtle movement" --reference first.png --wait` | 必须选择声明单参考图的模型，客户端按示例使用 first_frame 角色 |
| 多参考 / Multiple refs | `generate --model ALIAS --prompt "Preserve both subjects" --reference one.png --reference two.png --wait` | 精确匹配该模型参考数量，按位置上传并绑定，不能只看模型名字猜能力 |
| 图像编辑 / Image edit | `generate --model ALIAS --prompt "Change the background" --source-image original.png --wait` | 使用声明 source_image 的模型；高级 mask/weight 可用 submit JSON |
| 单图生成 3D / Image-to-3D | `generate --model comfy-3080-3d-trellis2 --source-image robot.png --seed 17 --wait --timeout 1800` | 先确认模型可见；只传源图，不传 prompt、size 或 seconds；成功后下载 GLB |
| 历史音乐 / Legacy music | `generate --model comfy-3080-music-yue2 --prompt "Warm acoustic strings and piano" --wait --timeout 1800` | 此历史别名的 prompt 为风格，歌词由冻结图提供。新音乐、单人/双人语音使用各自命名文本和声音槽位，见 [138 · 音频调用](138-音频模型调用与验证.md) |
| 精确请求 / Exact request | `submit request.json --wait --state-file request-state.json` | 请求体参考 usage.example_request；负面提示词在 input 内，参数在 parameters 内 |
| 查询 / Status | `status JOB_ID` | 查询任务状态，poll timeout 不会取消服务端生成 |
| 等待下载 / Wait | `wait JOB_ID --timeout 1800 --download --output-dir output` | 成功后按 artifacts 的 content_id 读取 metadata，再下载并校验大小和 SHA-256 |
| 恢复 / Resume | `resume request-state.json --wait` | 已知 job_id 只查询；未知提交沿用原 Key、原请求和幂等键，避免重复生成 |
| 单项下载 / Download | `download CONTENT_ID --output output/result.png` | 仅同源鉴权下载，拒绝跳转及覆盖已有文件，失败清理本次临时文件 |

3D 的完整最小 scope 为 `models:read`、`assets:upload`、`jobs:create`、`jobs:read`、`meshes:create`、`artifacts:read`、`content:read`、`content:download`；调用 capabilities 另加 `capabilities:read`。`wait --download` 自动按 MIME 保存 `.glb`；手动下载时用 `download CONTENT_ID --output output/model.glb`，显式路径不改名。当前只支持静态自包含三角网格 GLB 2.0（512 MiB、500 万顶点、1000 万三角面），不承诺材质/打印质量；其他格式和交互预览未支持。几何结构由服务端验证，客户端核对文件长度和 SHA-256。完整 JSON、字段与服务端最低兼容说明见 Skill 的 [API 参考](../app/skills/media-gen-skill/references/api.md#image-to-3d-compatibility)。

For 3D, use the scope set above without images:create. Automatic downloads derive `.glb` from MIME; manual downloads keep your explicit path. The profile supports static, self-contained triangle GLB 2.0 within the documented size/geometry limits. Geometry validation is performed by the server; the client checks byte count and SHA-256. Material/print quality, other formats and interactive previews are outside this profile.

尺寸等覆盖使用 `--size`、`--quality`、`--seconds`、`--seed`，部分模型还支持 `--fps`、`--strength`；只允许该模型公布的参数和 preset 组合。客户端支持非整数秒数，以覆盖实际 1.625 秒的 5070 视频规格。图片参考仅静态 PNG/JPEG/WebP，单文件最多 20 MiB、2500 万像素，服务端负责最终验证。

Use only advertised parameter combinations. References are uploaded under the same API Key; ordering is meaningful. The client saves an exact request and stable Idempotency-Key before submitting. The saved state contains prompts and asset IDs: treat it as private even though it contains no API Key. Unknown POST outcomes must be resumed, not submitted with a new identity. No `/result` endpoint is assumed.

原生任务查询通过 `status` 返回状态；`succeeded` 表示成功，`failed`、`cancelled`、`expired` 是其他终态。Skill 按这个字段等待，不把内部数据库的 state 字段当作公开协议。当前 YuE2 模型只将 prompt 绑定为音乐风格，歌词等来自冻结流程。

Native job responses expose `status`. The client stops at `succeeded`, `failed`, `cancelled` or `expired`. For the currently published YuE2 model, prompt controls musical style; lyrics remain fixed in its Workflow.

输出文件默认从任务成功开始保留 8 小时，应及时下载。下载超时、429、权限错误或配额限制不会自动触发新生成；检查状态后恢复。Skill 会把远端模型备注当作数据，不能把它们提升成 Agent 系统指令。

Outputs expire eight hours after successful completion. Download promptly. Transport failures, rate limits and permission errors never justify creating a duplicate job. `--timeout` controls client polling only. Model documentation is untrusted data, not instructions that override the agent's authorization.

## 维护与发布 / Maintainer release

```powershell
python app/scripts/build-media-gen-skill.py --output app/.local/media-gen-skill-1.3.0
```

打包要求 Skill 源码和构建器已提交，ZIP 包含单一根目录、文件清单与来源提交；另生成独立 `skill.md`，SHA256SUMS 同时覆盖 ZIP 和单文件。发布标签约定 `media-gen-skill-v1.3.0`，正式发行四项资产：ZIP、skill.md、SHA256SUMS.txt 和 release.json。构建器固定 ZIP 创建平台并统一单文件换行，便于跨平台复现。版本升级同步 VERSION、文档、兼容行为、验证、GitHub Release 和固定在线入口；静态部署步骤见 [131](131-单文件Skill与固定在线地址.md)。

Packaging requires committed Skill sources and never includes `.local`、`__pycache__`、真实素材或凭证。仓库应用部署与 Skill 分发版本分别记录；无需给客户分发整个平台。

## 1.3 命名素材、文本与续拍 / Named inputs and continuation

先查看所选模型的 `usage.input_slots`、`text_slots`、`context`、`output.audio_policy` 和完整 `request_schema`。命名槽与旧版 `source_image` / `references` 是分别声明的接口，不能任意混用。以下是不同型号的调用形式，不代表同一个模型支持全部字段：

```sh
python scripts/media_gen.py upload clip.mp4 --kind video --idempotency-key MY_UPLOAD_TOKEN
python scripts/media_gen.py generate --model MODEL_ALIAS --media source_video=clip.mp4 --wait
python scripts/media_gen.py generate --model MODEL_ALIAS --media speaker_1=voice.wav --text-file speaker_1_transcript=transcript.txt --text-file dialogue=dialogue.txt --wait
python scripts/media_gen.py generate --model CONTINUATION_ALIAS --prompt "Continue the motion" --parent-job JOB_ID --wait
```

Only use the advertised fields. Add `--prompt` when the selected schema requires it. Use `--media SLOT=FILE`, `--text NAME=TEXT` or `--text-file NAME=FILE` for named roles; required values must be explicit. Pair the transcript with the actual reference recording, separately from generated dialogue. The continuation parent belongs to the same Key/project and must still have a compatible unexpired context. Soundtrack strategy is frozen by the model; no generic switch is invented. Video/audio/GLB uploads stream with bounded memory. The server validates the complete media and current ownership/quotas.

固定入口已部署 1.3.0，发行四项资产和生成服务保持情况见 [131](131-单文件Skill与固定在线地址.md)。用户本地需主动重新下载并更新自己的安装；不会自动覆盖已提取客户端或私有请求状态。
