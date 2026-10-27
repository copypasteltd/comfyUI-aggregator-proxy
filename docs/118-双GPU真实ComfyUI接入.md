# 118 · 双 GPU 真实 ComfyUI 接入

2026-10-27。用户授权启动 remote-3080 / remote-4090 的既有 ComfyUI，并同步接入第三个指定地址。相关约束：DEV-01～05、07～22、25～32。全程使用原生 Windows / Linux；沿用 ZeroTier、HTTP 高位入口与任意来源 CORS。

## 1. 实际结果

| 编号 | 项目 | 结果与边界 |
| --- | --- | --- |
| 01 | 两台 GPU | 两个真实 Worker 身份和物理 GPU 已登记；RTX 3080 为 10 GiB，RTX 4090 实际报告约 48 GiB |
| 02 | ComfyUI | 复用两台既有 0.42.1 进程，均监听 8188；未更新节点、下载模型或改动 ComfyUI Python 环境 |
| 03 | Worker | 两台独立 CPython/Git/WinSW 安装，版本 rc-20261027.118.3，原生 SCM 自动延迟启动；心跳与渠道核对通过 |
| 04 | 渠道 | 2 条启用、配置已应用且验证一致的渠道；后台可编辑地址、凭据、排空/停用与观察队列 |
| 05 | Workflow | 3080 共 78、4090 共 217 个既有文件；通过实际 ComfyUI 前端转换得到 295 个 API 图，不提交原画布修改 |
| 06 | 草稿/发布 | 241 个候选草稿（3080 为 27、4090 为 214）；其中 3 个冻结版本通过依赖同步、真实任务和运行批准后发布模型；其余 238 个候选未批准 |
| 07 | 待整理项 | 54 个导出存在缺模型、专用绑定/输入、最终输出不符等问题；3D 不计为已支持图片/视频/音频 |
| 08 | 模型与目录 | 51 个模型文件摘要/大小登记；两台真实节点类型/模型扫描以及 API 图、完整 Manifest 导出目录均可从 Admin 浏览。登记不等于所有模型均已运行验证 |
| 09 | 真实样本 | 文生图、文生视频、ComfyUI 音乐各一条完整链路成功，另有真实业务 Key 的 OpenAI 风格异步图片任务成功；均使用测试者自建青柠素材/提示 |
| 10 | 第三台 | 从控制机和云服务器重新检查均超时；需要设备在线、ZeroTier 可达、监听及 Windows 远程管理信息，尚未登记虚假 GPU 或渠道 |

## 2. 已发布模型与使用

| 模型别名 | 类型 | 默认冻结规格 |
| --- | --- | --- |
| comfy-3080-image-lustify-krea2 | text_to_image | standard / 1024x1024 / PNG / 1 张 |
| comfy-4090-video-h3 | text_to_video | standard / 864x480 / 5.167 秒 / MP4 |
| comfy-3080-music-yue2 | text_to_audio | standard / 最长 30 秒 / FLAC / 48000 Hz / 双声道 |

在 Admin 的“API 密钥”新建业务 Key，授权模型别名、对应 Workflow 冻结版本、渠道和所需权限。图片需要 images:create，视频需要 videos:create，音频需要 audio:create；任务、素材、内容权限按实际调用分别授予。音频的租户、项目和 Key 时长额度都须大于零。本次只为默认接入项目启用有界音频额度，临时验证 Key 已停用，没有发布永久通用测试 Key。

```powershell
$api='http://<server>:39443'
$headers=@{Authorization='Bearer <business-api-key>'; 'Idempotency-Key'='client-request-unique-0001'}
$body=@{model='comfy-3080-image-lustify-krea2';prompt='A green lime beside a white cup, soft daylight';size='1024x1024';quality='standard';n=1;response_format='url'}|ConvertTo-Json
$job=Invoke-RestMethod -Method Post -Uri "$api/v1/images/generations/async" -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "$api/v1/jobs/$($job.id)" -Headers $headers
```

任务成功后读取 artifacts 中的内容 ID，携带同一业务 Key 下载 /v1/content/{id}/download；支持 HEAD 与 Range。音频通过 /v1/jobs 提交下述规范任务；本轮没有新增 /v1/audio/speech，也不将音乐 Workflow 宣称为 OpenAI TTS 接口。

```json
{
  "model": "comfy-3080-music-yue2",
  "task_type": "text_to_audio",
  "input": {"prompt": "Instrumental acoustic folk, warm guitar and soft piano"},
  "parameters": {"quality": "standard", "seconds": 30, "n": 1, "seed": 20261027}
}
```

YuE2 的 prompt 绑定曲风/风格字段，保留该 Workflow 自带歌词；不把普通 prompt 解释为任意完整歌词/ABC。另一个 ABC 版本、SoulX 结构化 JSON 多角色以及参考音频条目保留候选，待定义与验证专用输入。本轮仅实测标准默认规格；冻结包另含 draft 预设，尚未独立实测，首批业务 Key 仅应授权 standard，扩大规格前需追加验证。

## 3. Windows 与同步运维

两台独立安装根目录均为 C:/dev/comfy-aggregator-worker；既有 ComfyUI 仍在 C:/dev/comfyUI/ComfyUI。Worker 使用 own private/node.secret、private/worker.json 与 state/worker 持久 journal。正式服务为 rc-comfy-3080-worker-main / rc-comfy-4090-worker-main。服务配置与节点凭据独立隔离；默认 HTTP 拒绝，只有设置 allow_http=true 才启用已授权的私网 HTTP 控制源。

每台另登记固定计划任务 ComfyAggregator-ComfyUI-8188：Windows 启动 45 秒后先检查既有本机 ComfyUI；健康时直接退出，端口被未知进程占用时拒绝，缺失时调用原有已验证启动器。原生任务单实例且有命名互斥，不重启健康进程。本轮健康检查返回 0；没有通过重启整机宣称开机恢复已验收。保留其他既有计算/直播进程，最终观察可能包含其他用户任务。

导出根目录 exports/api 保留既有 API 图，exports/manifests 保存可整理候选。Admin “从渠道导入”可浏览、读取、预览后创建或更新草稿；需要编辑/冻结/同步/实测批准后，才发布新的模型路由。目录为本次同步快照；以后 Windows 新增或修改画布，需要重新导出 API 图并更新该导出目录，后台不会无提示扫描任意本机磁盘。

既有 archive 节点没有上游 Git 元数据，因此对已安装源码建立本地快照提交；记录为本地快照，不伪称上游 commit，不上传第三方源码或权重。Worker 同步会实际重算发布包中锁定模型文件的 SHA-256 并核对当前 ComfyUI/节点提交。3080 模型发现为 partial/path_not_allowed，55 个允许目录条目可见；4090 为 ready、68 个条目。保留扫描限制，不放宽任意路径或谎报全盘完整。

服务器保留正式公开入口、私网 Worker 内部入口、PG/S3 与既有服务。临时受限分发服务已停止，约 700 MB 本轮分发缓存已删除；正式安装、导出、凭据、journal 和配置回退副本保留。Windows 分发缓存和带临时下载令牌的安装脚本已清理。

## 4. 兼容实现与验证

1. Worker allow_http 默认 false；显式设置后允许已授权 HTTP origin，仍严格规范 origin、端口、凭据和 journal/进程互斥身份。
2. 可选 git_executable 只接受存在的本地 git.exe；原生服务在受限 PATH 下可核验依赖，不修改全局 Git/PATH。临时 safe.directory 只针对本次选中的本地路径。
3. Windows history 的 subfolder 允许将反斜杠规范为斜杠，再执行原相对路径和目录包含检查；文件名、绝对路径、盘符和父目录越界校验仍严格。

| 证据 | 结果 | 边界 |
| --- | --- | --- |
| [当前来源组件检查](evidence/2026-10-27/worker-onboarding-component-118-final.json) | 588 / 588，0 跳过 | 27 个具名 Python 文件，相关 14 条需求；不是全项目重跑 |
| Worker Mypy | 17 个源文件通过 | 当前 Worker 类型检查 |
| [真实生成与交付](evidence/2026-10-27/comfy-real-delivery-118.json) | 3 条真实生成、19 项检查 | PNG 1024²；音频 29.952 秒/48k 双声道；视频 864×480/5.166 秒，实际下载摘要、HEAD/Range |
| [最终接入核对](evidence/2026-10-27/comfy-onboarding-closeout-118.json) | 真实业务图片及 5 项收尾检查、4 个导出目录成功 | 临时 Key 停用、匿名拒绝；正式节点/渠道持续在线 |
| [实际后台浏览器](evidence/2026-10-27/comfy-admin-browser-118-final.json) | 8 个页面/尺寸检查 | 实际部署数据，无 fixture；私网地址视觉遮挡，所有自建浏览器资源已关闭 |
| [截图/MP4/GIF/样本](media/comfy-onboarding-118/README.md) | 实际画面与生成内容 | 发布样本移除内嵌生成元数据，原始下载摘要与公开副本摘要分列 |

候选完整 ZIP 来源 4a5dfcef573095ce63ba8b970b9a0c774e6e79f1，SHA-256 cc6c1d7ea3ac994ec350ebc3eee93fd6dcc06821128a535c1e8e171cad84a109；58 个 wheel / 169 个载荷。两台均按完整官方安装器核验并安装，增量传输只复原相同 ZIP 字节，不修改安装后源码。Linux 控制面仍使用已部署 116.2 的兼容协议。

## 5. 剩余边界

本轮实际证明 3/6 能力类型的单个受控规格和一个业务 Key 图片通路。图生图、多参考图生图、多参考视频仍需真实输入与独立验证；54 个阻塞导出、238 个候选、完整模型质量、所有规格、10～20 台实机容量、定制 Sub2API 联合运行、重启/断电/恢复和完整首版门禁未签收。历史 69 个稳定出口保持原编号，不以单次接入清空。第三台设备待在线及管理方式，不用前两台身份代接。
