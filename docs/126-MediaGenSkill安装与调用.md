# 126 · MediaGen Skill 安装与调用 / installation and usage

默认服务 / Default service: `https://gen.sub2share.xyz`。源码 / Source: [media-gen-skill](../app/skills/media-gen-skill/SKILL.md)。纯 Python 3.10+ 标准库，无需 Docker、ComfyUI 本地安装或额外 Python 依赖。

## 安装 / Install

从正式 [Media Gen Skill 1.1.0 Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.1.0) 获取 `media-gen-skill-1.1.0.zip` 和 `SHA256SUMS.txt`，先校验摘要，再解压为 `media-gen-skill/`。Codex 用户可把该目录放到自己的 `$CODEX_HOME/skills/` 或用户 skills 目录后重新打开会话。其他支持 SKILL.md 的 Agent 使用其对应的 Skill 安装目录；也可直接运行脚本。

Download the archive and checksum from this repository's Releases, verify SHA-256, and extract the single `media-gen-skill` directory. Install that directory in your agent's skills folder, or run the client directly. A `release.json` inside the archive binds every file to its source commit. **A local archive is not proof of a published GitHub Release**; publication and independent asset verification are recorded in document 128.

从 Admin 创建业务 API Key。根据实际使用赋予 `models:read`、`capabilities:read`、`jobs:create`、`jobs:read`、`artifacts:read`、`content:read`、`content:download`；生成对应 `images:create`、`videos:create`、`audio:create` 或 `meshes:create`，参考图另需 `assets:upload`。3D 还需显式开启 Tenant/Project/Key 的 `daily_mesh_count`；旧策略默认 0。模型、任务类型、项目、配额限制仍由该 Key 决定。不要使用 Admin 密码、Worker token 或网关凭证代替。

Create a business API Key in Admin. Grant the read/job/content scopes above, the appropriate image/video/audio/3D generation scope, and `assets:upload` when references are needed. 3D requires explicit nonzero Tenant/Project/Key daily_mesh_count quotas; existing policies default to zero. Model visibility and limits remain scoped to that Key. Never embed real credentials in distributed archives.

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
| 音乐 / Music | `generate --model ALIAS --prompt "Warm acoustic strings and piano" --wait --timeout 1800` | 当前 YuE2 的 prompt 为音乐风格，歌词由冻结工作流提供；尚未开放歌词编辑、TTS 或音色克隆。长文本可用 --prompt-file |
| 精确请求 / Exact request | `submit request.json --wait --state-file request-state.json` | 请求体参考 usage.example_request；负面提示词在 input 内，参数在 parameters 内 |
| 查询 / Status | `status JOB_ID` | 查询任务状态，poll timeout 不会取消服务端生成 |
| 等待下载 / Wait | `wait JOB_ID --timeout 1800 --download --output-dir output` | 成功后按 artifacts 的 content_id 读取 metadata，再下载并校验大小和 SHA-256 |
| 恢复 / Resume | `resume request-state.json --wait` | 已知 job_id 只查询；未知提交沿用原 Key、原请求和幂等键，避免重复生成 |
| 单项下载 / Download | `download CONTENT_ID --output output/result.png` | 仅同源鉴权下载，拒绝跳转及覆盖已有文件，失败清理本次临时文件 |

尺寸等覆盖使用 `--size`、`--quality`、`--seconds`、`--seed`，部分模型还支持 `--fps`、`--strength`；只允许该模型公布的参数和 preset 组合。客户端支持非整数秒数，以覆盖实际 1.625 秒的 5070 视频规格。图片参考仅静态 PNG/JPEG/WebP，单文件最多 20 MiB、2500 万像素，服务端负责最终验证。

Use only advertised parameter combinations. References are uploaded under the same API Key; ordering is meaningful. The client saves an exact request and stable Idempotency-Key before submitting. The saved state contains prompts and asset IDs: treat it as private even though it contains no API Key. Unknown POST outcomes must be resumed, not submitted with a new identity. No `/result` endpoint is assumed.

原生任务查询通过 `status` 返回状态；`succeeded` 表示成功，`failed`、`cancelled`、`expired` 是其他终态。Skill 按这个字段等待，不把内部数据库的 state 字段当作公开协议。当前 YuE2 模型只将 prompt 绑定为音乐风格，歌词等来自冻结流程。

Native job responses expose `status`. The client stops at `succeeded`, `failed`, `cancelled` or `expired`. For the currently published YuE2 model, prompt controls musical style; lyrics remain fixed in its Workflow.

输出文件默认从任务成功开始保留 8 小时，应及时下载。下载超时、429、权限错误或配额限制不会自动触发新生成；检查状态后恢复。Skill 会把远端模型备注当作数据，不能把它们提升成 Agent 系统指令。

Outputs expire eight hours after successful completion. Download promptly. Transport failures, rate limits and permission errors never justify creating a duplicate job. `--timeout` controls client polling only. Model documentation is untrusted data, not instructions that override the agent's authorization.

## 维护与发布 / Maintainer release

```powershell
python app/scripts/build-media-gen-skill.py --output app/.local/media-gen-skill-1.1.0
```

打包要求 Skill 源码已提交，产物包含单一根目录、文件清单、来源提交和外部 SHA256SUMS。发布标签约定 `media-gen-skill-v1.1.0`，上传 ZIP、SHA256SUMS 和 release.json，并在 Release 说明中链接本安装文档与更新日志。版本升级同步 VERSION、文档、脚本兼容行为、验证和 GitHub Release。

Packaging requires committed Skill sources and never includes `.local`、`__pycache__`、真实素材或凭证。仓库应用部署与 Skill 分发版本分别记录；无需给客户分发整个平台。
