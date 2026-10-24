# 03 · 接口与 Sub2API 接入规范

本文是拟议接口契约，不是已运行 API。示例模型名和地址均为本项目占位值，不代表 OpenAI 模型或已选定工作流。

开发设计基线 v0.4 包含首版必交 Web Admin，以及 Admin 管理的多业务 API Key。管理端点独立于客户兼容协议；见第 13～14 节、[Admin 专项设计](./07-Admin可视化管理后台.md) 和 [API Key 专项设计](./08-APIKey鉴权与访问控制.md)。工程协议、字段 Schema 和端点权限清单见 [12](./12-接口契约与Worker协议.md)。

## 1. “尽可能符合 OpenAI”如何验收

| 层面 | 目标 | 必须说明的边界 |
| --- | --- | --- |
| 基础 HTTP | Bearer 鉴权、JSON/multipart、请求 ID、OpenAI 风格 error | 内部 Worker 协议不对外冒充 OpenAI API |
| 图片同步 | `/v1/images/generations`、`/v1/images/edits`，标准 prompt/model/n/size 与图片结果外形 | 只支持已声明字段；OpenAI 不同模型也有不同约束 |
| 图片异步 | 沿用所查 Sub2API 的 `/async` 和 `/images/tasks/{id}` 外形 | 这是 Sub2API 扩展，不是 OpenAI Images 的通用异步标准 |
| 视频 | 采用冻结的 `/v1/videos` 创建、状态查询和 content 下载约定 | 属于历史 OpenAI Videos 风格；具体网关的字段/状态需实测 |
| 多参考图 | 图片编辑的有序多图输入，加项目扩展角色信息 | 不存在适配所有图像/视频模型的统一参考图角色协议 |
| 全能力任务 | 项目原生 `/v1/jobs` | 承载能力参数、取消、准确阶段与交付信息，不标为 OpenAI 标准 |

OpenAI 当前官方图片文档按模型区分参数与返回形式，不能承诺“任意 OpenAI 字段原样支持”。[图片生成参考](https://developers.openai.com/api/reference/resources/images/methods/generate)、[图片编辑参考](https://developers.openai.com/api/reference/resources/images/methods/edit)。

按 2026-10-22 所查官方说明，OpenAI 的 Sora 2 与 Videos API 已于 2026-09-24 停止服务；因此本项目的视频兼容目标应固定为客户/网关仍使用的协议外形，不描述为当前仍可调用的 OpenAI 视频服务，也不使用 `sora-2` 冒充本地模型。[官方说明](https://developers.openai.com/api/docs/guides/video-generation)、[弃用记录](https://developers.openai.com/api/docs/deprecations)。

## 2. Sub2API 接入路径

### 2.1 本次核对范围

优先研究 `Wei-Shaw/sub2api` 的 `7da09cab4e875463baa79fc150adde700c5438df` 快照。用户实际使用的仓库、分支和版本尚未提供，不能用本次公开源码代替安装实例联调。

已确认该快照的图片 API-key 转发可使用账户自定义 Base URL，但模型校验并不允许任意 `render-*` 名称；转发头也有白名单。[图片转发实现](https://github.com/Wei-Shaw/sub2api/blob/7da09cab4e875463baa79fc150adde700c5438df/backend/internal/service/openai_images.go)。

### 2.2 路径 A：利用现有同步上游验证链路

```text
客户 → Sub2API 图片异步入口
       → Sub2API 后台调用同步图片入口
       → 本项目图片同步适配 → 持久化 job → Windows → 最终图片
```

适合验证图片路由、multipart、账户自定义上游与结果转存。前置条件是解决自定义模型校验、确认图片权限、超时链路、幂等头与计费单位。不要通过伪称 `gpt-image-*` 的方式隐藏实际模型来绕过校验。

所查原版异步图片入口有对象存储开关、执行时限与独立结果保留规则。[原版异步说明](https://github.com/Wei-Shaw/sub2api/blob/7da09cab4e875463baa79fc150adde700c5438df/docs/ASYNC_IMAGE_TASKS.md)。源码是创建记录后启动后台执行同步调用；没有因此获得本项目要求的 Worker 租约和重启恢复能力。[异步 Handler](https://github.com/Wei-Shaw/sub2api/blob/7da09cab4e875463baa79fc150adde700c5438df/backend/internal/handler/image_task_handler.go)。

因此路径 A 只是早期联通验证路径。它存在两层任务、长连接和可能二次转存，不能作为覆盖长视频和可恢复队列的完整生产方案。

### 2.3 路径 B：ComfyUI Provider 适配，建议生产路径

```text
客户 → Sub2API 鉴权/额度/模型路由
       → ComfyUI Provider 创建云端 job，保存公共 ID ↔ job 绑定
       → 返回异步公共任务

查询/取消/下载 → 校验原客户归属 → 查询同一云端 job
终态后台事件 → 幂等结算/释放额度 → 更新公共任务投影
```

该路径保留 Sub2API 面向客户的外形，将内部执行改为“创建持久化任务 + 后台恢复查询”。适配的实现位置需要在固定源码版本中明确；这里不是假定原项目已有通用插件接口。

最小改动职责：

1. 登记自有模型及能力，允许自有模型通过解析、权限、模型列表和路由校验；不让它进入 GPT/OAuth 专有转换逻辑。
2. 将图片同步、图片异步、视频创建和状态读取分别映射到任务服务；不能把上游 `202` 当作同步图片成功结果。
3. 完整传递有序多图和已授权扩展；multipart 重编码保持图片顺序、名称与内容。
4. 使用稳定外部幂等键调用任务服务，持久化公共任务 ID、内部 ID、客户和额度凭证。
5. 查询/取消/文件路径使用同一归属校验；新增资产和原生任务路由也必须经过客户鉴权。
6. 允许可靠的终态恢复、后台结算、额度释放；客户不轮询时也会完成结算。
7. 提供图片数量/规格、视频秒数/规格的费率与真实计量，停止沿用不适用的 token 估算。
8. 对已在执行的任务不做无条件换上游重发；保留请求 ID、错误类别和恢复任务链接。

外部映射、云端任务和 Sub2API 账本处于不同事务域，使用稳定请求 ID、唯一约束和可恢复补偿流程。预留已完成但任务创建失败时释放预留；任务已创建但映射回执失败时通过稳定键恢复原任务，不能新建第二个。未完成准入确认的记录不得进入 GPU 队列。

建议顺序为：先持久化公共任务意图与稳定键 → 幂等预留额度 → 创建 `admission_pending` 云端任务 → 持久化公共 ID 与 job 绑定 → 幂等确认入队 → 返回接收成功。确认回执丢失时核查原任务；准入超时且未确认时释放预留。该过程以可恢复状态机实现，不能假定两个数据库有同一个事务。

所查 Sub2API 的视频路径包含 Grok/Composite 等供应商判断，即使存在 `/v1/videos` 路由，也不能推断自有 ComfyUI 视频模型可直接使用。[路由源码](https://github.com/Wei-Shaw/sub2api/blob/7da09cab4e875463baa79fc150adde700c5438df/backend/internal/server/routes/gateway.go)。生产路径应给本项目建立明确的 Provider 分支及测试。

## 3. 拟议公共端点

所有端点在同一云端入口下提供，模型/任务/资产归属由可信接入层解析。`/v1` 前缀并不表示所有子路径均属于 OpenAI 标准。

| 方法与路径 | 请求/响应 | 类型 |
| --- | --- | --- |
| `GET /v1/models` | OpenAI 风格模型列表 | 兼容 |
| `GET /v1/capabilities` | 各模型真实输入/输出、档位和版本 | 项目扩展 |
| `POST /v1/assets` | multipart 文件上传，返回资产 ID 与校验状态 | 项目扩展 |
| `GET /v1/assets/{id}` | 元数据、可用状态、当前 Key 允许的授权下载信息 | 项目扩展 |
| `GET /v1/assets/{id}/content` | 鉴权文件流或签名重定向，支持 HEAD/Range | 项目扩展 |
| `DELETE /v1/assets/{id}` | 请求删除；活动任务引用存在时返回冲突或延迟清理 | 项目扩展 |
| `GET /v1/content`、`GET /v1/content/{id}` | 按当前 Key 权限检索/读取任务素材与产物目录，不扩大源对象权限 | 项目内容扩展 |
| `GET/HEAD /v1/content/{id}/download` | 按 Key 下载策略代理文件或签发允许的 URL，支持 Range | 项目内容扩展 |
| `DELETE /v1/content/{id}` | 按内容删除权限处置，保留活动引用和任务历史约束 | 项目内容扩展 |
| `GET /v1/access-policy` | 当前业务主体的脱敏有效范围/限额摘要 | 项目鉴权扩展 |
| `POST /v1/jobs` | 规范请求，`202` 返回 job | 项目异步核心 |
| `GET /v1/jobs/{id}` | 完整内部状态的客户安全投影 | 项目扩展 |
| `GET /v1/jobs` | 当前授权范围任务分页列表/检索 | 项目扩展 |
| `POST /v1/jobs/{id}/cancel` | 请求取消，返回当前状态 | 项目扩展 |
| `POST /v1/images/generations` | JSON，等待最终图片或明确同步超时错误 | 图片兼容 |
| `POST /v1/images/edits` | multipart 或已支持的 JSON 引用格式 | 图片兼容 |
| `POST /v1/images/generations/async` | 与图片生成相同 body，`202` 返回公共任务 | Sub2API 风格扩展 |
| `POST /v1/images/edits/async` | 与图片编辑相同 body，`202` 返回公共任务 | Sub2API 风格扩展 |
| `GET /v1/images/tasks/{id}` | processing/completed/failed 投影 | Sub2API 风格扩展 |
| `POST /v1/videos` | 创建视频任务，返回 video 对象 | 历史 OpenAI 风格兼容 |
| `GET /v1/videos/{id}` | queued/in_progress/completed/failed 投影 | 历史 OpenAI 风格兼容 |
| `GET /v1/videos/{id}/content` | 已完成视频下载，支持 HEAD/Range | 历史 OpenAI 风格兼容 |

图片异步任务若需要取消，使用原生任务取消路由并提供可定位绑定；不擅自把不存在的 OpenAI 图片取消路由描述为标准。Responses/Chat Completions 的工具调用和 SSE 图片流不进入首版必需范围，发送不支持的流式参数应明确报错。

## 4. 公共规则

- 外部使用 `Authorization: Bearer <customer_key>`；本项目直连 Key 由统一鉴权模块验证，Sub2API 客户 Key 由网关验证并传入可信映射。业务 Key、管理会话、网关服务身份和 Worker 身份隔离，无 Key 不匿名放行。
- 异步创建建议客户端提供 `Idempotency-Key`。没有键的重复 POST 被视为新任务，不能靠相同 prompt 自动去重。
- 幂等范围为租户、调用主体、逻辑操作与键；跨不同入口创建若需要合并，接入适配必须传递同一规范操作与稳定键。
- 同键同请求返回原任务与既有状态；同键不同请求返回 `409 idempotency_conflict`。
- 返回 `X-Request-ID`；异步创建返回 `Location`、`Retry-After` 和 `Cache-Control: no-store`；查询也禁止缓存旧进度。
- 接口时间戳统一为 Unix 秒；另外的日志使用 UTC ISO 8601。控制台按用户时区展示。
- 分页使用游标，按创建时间和唯一 ID 稳定排序；不允许查询其他租户列表。
- 每 Key 的 scopes、模型/解析后的 Workflow、规格、渠道范围和资源额度统一检查；无授权的条目/聚合/文件地址不因换一个兼容入口而可见。稳定 key_id 用于内容归属与业务幂等，凭证轮换不重置额度。
- 未支持字段不静默丢弃：可识别的无害 SDK 元数据明确列白名单，其余参数报错；任何影响结果/费用的未知字段必须拒绝。
- 同步入口只返回标准最终成功形状或 error，不在原图片端点私自返回 `202 task` 并声称兼容普通 Images SDK。

## 5. 资产上传

先上传再创建任务，避免多参考图随每次轮询或重试重复发送。`/v1/assets` 属于项目接口，不等同于 OpenAI Files；首版不承诺任意 OpenAI `file_id` 可用。

上传成功示例，HTTP `201`：

```json
{
  "id": "asset_character01",
  "object": "asset",
  "status": "ready",
  "mime_type": "image/png",
  "bytes": 1824300,
  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "width": 1024,
  "height": 1536
}
```

上传流程若采用预签名直传，则增加创建上传凭证和完成确认两步。客户端说“上传完成”后，服务端仍须核查对象和解码结果；只有 ready 资产能进入生成队列。首版可先由云端 API 流式接收素材并写入对象存储。

## 6. 原生任务请求：承载完整能力

```json
{
  "model": "render-ref-video-v1",
  "task_type": "references_to_video",
  "input": {
    "prompt": "人物走过雨后的街道，保持人物与服装一致，镜头缓慢跟随。",
    "references": [
      {"asset_id": "asset_character01", "role": "character"},
      {"asset_id": "asset_scene01", "role": "scene"}
    ]
  },
  "parameters": {
    "size": "720x1280",
    "seconds": 5,
    "quality": "standard",
    "seed": 104857
  },
  "client_request_id": "client-order-0001"
}
```

示例尺寸与 5 秒仅展示字段组织，必须由选定工作流发布清单确认后才对外开放。`references` 的数组顺序有意义，role 为该模型支持的枚举。`client_request_id` 用于客户检索，真正去重仍使用幂等键。

创建响应，HTTP `202`：

```json
{
  "id": "job_example01",
  "object": "generation.job",
  "status": "queued",
  "stage": "waiting_for_worker",
  "model": "render-ref-video-v1",
  "created_at": 1792622400,
  "poll_url": "/v1/jobs/job_example01",
  "progress": null
}
```

成功查询响应示例（此例假设有 artifacts:read/content:download 且明确开启 signed_url；默认策略只返回需鉴权的 content 地址，不返回对象存储直链）：

```json
{
  "id": "job_example01",
  "object": "generation.job",
  "status": "succeeded",
  "stage": "complete",
  "model": "render-ref-video-v1",
  "workflow_version": "ref-video-example@1.0.0",
  "progress": 100,
  "completed_at": 1792623300,
  "billing_status": "settled",
  "artifacts": [
    {
      "id": "artifact_example01",
      "kind": "video",
      "mime_type": "video/mp4",
      "bytes": 84213760,
      "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "width": 720,
      "height": 1280,
      "duration_seconds": 5,
      "fps": 24,
      "has_audio": false,
      "download_url": "https://download.example.com/signed-example",
      "url_expires_at": 1792624200,
      "artifact_expires_at": 1793228100
    }
  ],
  "metering": {
    "video_count": 1,
    "video_seconds": 5,
    "size": "720x1280"
  }
}
```

对外不返回 Windows 地址、磁盘目录、内部租约、原始 ComfyUI 节点堆栈。`billing_status` 可以先为 pending，由后台结算完成后变更；生成终态保持 succeeded。

## 7. 图片兼容入口

### 7.1 文生图

```json
{
  "model": "render-image-v1",
  "prompt": "白色背景上的陶瓷茶壶，柔和侧光。",
  "n": 1,
  "size": "1024x1024",
  "quality": "standard"
}
```

普通同步接口以 `b64_json` 作为兼容默认输出；本项目自有模型若声明支持 URL，则可接受 `response_format: "url"`。这是一项本地模型兼容约定，不能推论所有当前 OpenAI GPT Image 模型接受该参数。Base64/URL 交付均需 `content:download` 权限；缺少该权限的 Key 只可使用已授权的异步提交/元数据查询，不接受内联结果模式。

同步最终成功形状：

```json
{
  "created": 1792622400,
  "data": [
    {"b64_json": "<完整图片的Base64，示例省略>"}
  ]
}
```

兼容同步等待预算建议初值 60 秒，需与网关、代理和客户端超时一致。超过预算时返回 `504 sync_wait_timeout`，附带项目扩展的恢复任务 ID；已接收的异步任务仍继续。重试应使用原幂等键。不能用持续写空格或伪造 SSE 声称所有代理都能无限等待。

### 7.2 单图编辑与多参考图编辑

multipart 基本字段：`model`、`prompt`、`image` 或有序的 `image[]`、`n`、`size`、`quality`。单图形式与数组形式不得混用。接受多少张由模型清单决定。

对图片编辑 JSON，优先兼容官方的 `images` 数组引用外形，例如 `[{"image_url":"data:image/png;base64,..."}]`。首版允许受限数据 URL 和经过资产归属解析的本平台 URL；不直接抓取任意客户外链，也不承诺未经实现的 OpenAI Files 引用方式。将来受控外链导入须另做SSRF/DNS/重定向/下载预算验证。

参考图角色或重绘强度通过 `workflow_options` 项目扩展表达。multipart 时该字段为一个 JSON 字符串；JSON body 时为对象，例如：

```json
{
  "image_roles": [
    {"index": 0, "role": "character"},
    {"index": 1, "role": "scene"}
  ],
  "strength": 0.6
}
```

只有对应模型声明支持时才接受 strength；多参考条件模型未必具有“重绘强度”。标准请求没有角色字段时采用该模型已公开的固定有序槽位语义；没有默认语义则要求客户提供扩展，不任意猜测。

原生 `/v1/jobs` 使用 asset_id 与结构化 references 表达同一内容。不同入口归一化为同一个内部请求，不各自实现一套生成逻辑。

## 8. Sub2API 风格图片异步

图片请求发送到 `/v1/images/generations/async` 或 `/v1/images/edits/async`。不支持 `stream:true`。

创建，HTTP `202`：

```json
{
  "id": "imgtask_example01",
  "task_id": "imgtask_example01",
  "object": "image.generation.task",
  "status": "processing",
  "created_at": 1792622400,
  "expires_at": 1795214400,
  "poll_url": "/v1/images/tasks/imgtask_example01"
}
```

完成查询：

```json
{
  "id": "imgtask_example01",
  "task_id": "imgtask_example01",
  "object": "image.generation.task",
  "status": "completed",
  "http_status": 200,
  "image_url": "https://download.example.com/signed-image-example",
  "result": {
    "created": 1792622460,
    "data": [{"url": "https://download.example.com/signed-image-example"}]
  },
  "completed_at": 1792622460,
  "expires_at": 1795214460
}
```

本项目生产适配建议异步查询返回 URL，避免大 Base64 占用队列/缓存；与所查 Sub2API 转存后移除 Base64 的方向一致。URL 按 Key 的下载策略返回需鉴权的云端 content 地址，或显式允许的短时签名地址；上面签名 URL 示例对应后者。无下载权限仅返回允许的任务元数据。同步入口仍支持已声明的 Base64 输出，另可通过项目资产接口查看文件保留期。

本项目建议元数据终态后保留 30 天，与所查原版 Sub2API 的 24 小时规则不同；`expires_at` 必须反映实际实现，创建时可给出保守截止，终态后更新。若阶段 A 沿用原版，应按其真实规则对外说明，不能照抄本示例日期。

任务归属默认至少同时校验租户与提交 Key；更换 Key 后能否继续访问需显式授权流程。不能因同一用户拥有两个 Key 就自动暴露原 Key 的任务。

## 9. 视频兼容

标准外形示例：

```json
{
  "model": "render-video-v1",
  "prompt": "清晨山谷中的薄雾，固定机位。",
  "size": "1280x720",
  "seconds": "5"
}
```

兼容层可以接受秒数字符串并规范为内部数值；只允许发布清单中的离散时长。单图引导可采用已约定的 `input_reference`；多图使用项目扩展 `workflow_options.references`，或直接使用原生任务接口。多参考字段与供应商 `reference_images` 的转换属于具体 Provider 适配，不能当成通用 OpenAI 字段。

冻结实现采用 HTTP `202` 返回 queued video 对象，异步生成仍在后台；这是本项目的接收语义，目标网关需要按该状态码验证。较早草案中的 `200` 不再作为本项目创建契约：

```json
{
  "id": "video_example01",
  "object": "video",
  "status": "queued",
  "model": "render-video-v1",
  "created_at": 1792622400,
  "seconds": "5",
  "size": "1280x720"
}
```

对照目标客户端冻结创建状态码、字段类型和终态 error 外形后再实现。视频 content 在成功前返回任务未完成错误；完成后支持鉴权流式下载或受控重定向，必须联调 HEAD、Range 与客户端跟随跳转。

当前标准化输入增量见 [38](./38-标准化参考输入与Workflow选项.md)：Provider 与控制面兼容层接受视频 JSON/multipart `input_reference`，多参考扩展及已约定的 Workflow 选项；单参考首帧必须对应单槽位发布包，多参考能力仍要求独立验证。上传/编辑、媒体访问与模型发现等已有边界见文档 34～37。`file_id`、外部 URL 和任意节点参数仍没有映射，不把上述范围表述为完整 OpenAI 服务兼容。

### 状态映射

| 内部状态 | 图片异步 | 视频兼容 |
| --- | --- | --- |
| admission_pending | 通常不对外返回；恢复查询如需展示则为 processing | 通常不对外返回；恢复查询如需展示则为 queued |
| queued / leased | processing | queued |
| preparing / submitting / running / collecting / uploading / verifying | processing | in_progress |
| retry_wait / reconciling | processing | in_progress |
| cancel_requested | processing | in_progress |
| succeeded | completed | completed |
| failed | failed | failed |
| cancelled / expired | failed + 对应 error.code | failed + 对应 error.code |

标准兼容对象不随意添加 SDK 不认识的 status 枚举，准确阶段、取消确认和未知进度通过原生任务接口查看。视频整体进度不可用时省略；不能把采样结束映射成 completed。

## 10. 错误规范

```json
{
  "error": {
    "message": "该模型最多接受4张参考图，实际收到5张。",
    "type": "invalid_request_error",
    "param": "input.references",
    "code": "reference_limit_exceeded"
  }
}
```

| HTTP | 常见 code | 是否可重试 |
| --- | --- | --- |
| 400 | invalid_parameter / unsupported_parameter / reference_limit_exceeded | 修改输入后重试 |
| 401 / 403 | invalid_api_key / api_key_disabled / api_key_expired / api_key_revoked / permission_denied / scope_denied | 修正身份、有效期或授权 |
| 404 | model_not_found / task_not_found / asset_not_found | 不泄露其他租户存在性 |
| 409 | idempotency_conflict / asset_in_use / task_state_conflict | 先核查原请求或当前状态 |
| 410 | artifact_expired | 重新生成需新任务；URL 过期本身不使用该错误 |
| 413 / 415 | payload_too_large / unsupported_media_type | 缩小或更换素材 |
| 429 | quota_exceeded / queue_limit_exceeded / key_rate_limit_exceeded / key_resource_quota_exceeded | 按适用的 Retry-After/额度恢复条件，保持原幂等键 |
| 503 | no_eligible_worker / service_unavailable | 区分无能力节点与暂时不可达 |
| 504 | sync_wait_timeout | 原任务可能继续；先恢复查询，不盲目重复生成 |

异步任务执行失败时，查询仍可返回 HTTP `200` 的任务对象，status=failed，内嵌 error 和失败分类；查询接口故障与任务本身失败需要区别。提交遇到网络错误也不证明任务未接收。

## 11. 内部 Worker API 边界

拟议 `/internal/v1` 提供 register、heartbeat、claim、lease renew、event、upload authorize、complete、reconcile 等操作。公网不直接暴露；事件至少包含 node、boot、attempt、fencing token 与去重 ID。

内部接口接受冻结工作流版本与受控参数，不接受云端随意下发 shell 命令。租约续期采用服务器时间，节点时钟只作日志辅助。资源探测和维护命令必须有固定动作集合与审计。

首版另提供管理操作领取与回执：Worker 只可领取属于自身节点/渠道的连接探测、清单扫描、工作流包同步、配置应用等固定动作。操作包含 operation_id、目标渠道、配置/发布摘要和幂等标识；回执记录实际应用版本、boot_id、结果及脱敏错误。重启或重复领取不会重复应用，影响运行环境的动作在排空后执行，不能用管理操作绕过 GPU 锁。

## 12. New API 后续适配

New API 所查快照提供 openai_image/openai_video 任务插件协议，可在后续将其提交、轮询和渲染钩子接到同一云端任务服务。[任务插件文档](https://github.com/QuantumNous/new-api/blob/7d8b91268dab69d7150f345c448b5e250163146a/docs/plugin-api/v1.md)。

后续适配不改变 Workflow、Worker、job 状态和资产存储；只增加鉴权主体、公共任务 ID、请求/响应映射与结算适配。是否使用该快照或发布版，应以其稳定性和实测结果决定，不把插件文档当作已经联通的证据。

## 13. 首版 Admin 管理 API

后台页面位于 `/admin`，配套接口统一前缀 `/admin/api/v1`。这些是项目管理协议，不属于 OpenAI 格式，也不能直接使用客户 API Key 或 Worker 凭证访问。浏览器采用独立管理会话，写操作校验角色与 CSRF；SSE 与详情查询同样鉴权。

下表路径均相对管理前缀；对象/状态/队列含义以文档 07 为准。

| 方法与路径 | 管理能力 |
| --- | --- |
| `POST /session`、`GET /session`、`DELETE /session` | 登录、当前身份/权限、退出并终止实时订阅 |
| `GET /nodes`、`GET /nodes/{id}` | 可绑定节点/GPU、Worker 身份与心跳信息 |
| `GET /channels`、`POST /channels` | 渠道列表、创建未启用登记 |
| `GET /channels/{id}`、`PATCH /channels/{id}`、`DELETE /channels/{id}` | 查看/更新配置、受引用与排空约束的逻辑删除 |
| `POST /channels/{id}/probe` | 由绑定 Worker 异步测试目标地址、认证和 ComfyUI 环境 |
| `POST /channels/{id}/actions` | 固定枚举 enable/disable/drain/resume/apply_config，校验状态与配置版本 |
| `POST /channels/{id}/inventory-scans`、`GET /channels/{id}/inventory` | 扫描并读取模型/节点及授权 Workflow 来源清单，附采样和校验状态 |
| `GET /channels/{id}/workflow-bindings`、`PUT /channels/{id}/workflow-bindings` | 查询/更新允许发布版本与画像绑定，校验版本冲突和依赖 |
| `GET /workflows`、`POST /workflows`、`GET /workflows/{id}`、`PATCH /workflows/{id}` | Workflow 目录、草稿元数据/参数绑定/API JSON 导入与编辑 |
| `POST /workflows/{id}/releases`、`GET /workflow-releases/{id}` | 创建冻结版本、读取模板/依赖/样本和资源画像 |
| `POST /workflow-releases/{id}/validate` | 在授权渠道执行依赖校验及明确选择的样本验证，返回异步操作 |
| `POST /workflow-syncs` | 渠道导入 pull 或中央发布包分发 push，指定来源/目标和已选择的 Workflow/版本 |
| `GET /model-artifacts`、`POST /model-artifacts`、`PATCH /model-artifacts/{id}`、`DELETE /model-artifacts/{id}` | 底层模型文件登记、来源/哈希和依赖管理；删除检查引用 |
| `GET /public-models`、`POST /public-models`、`PATCH /public-models/{id}` | 客户公开模型别名、Workflow/渠道/规格和价格引用 |
| `POST /public-models/{id}/actions` | publish/pause/unpublish/rollback/sync_gateway，保存期望和确认的 Sub2API 映射版本 |
| `GET /queues/snapshot`、`GET /queues/jobs`、`GET /channels/{id}/queue` | 全局/渠道快照、互斥状态汇总、共享候选和分页任务列表 |
| `GET /jobs/{id}`、`POST /jobs/{id}/cancel`、`POST /jobs/{id}/retry`、`PATCH /jobs/{id}/priority` | 管理范围任务详情、受控取消、终态关联重试、待分配优先级调整 |
| `GET /operations`、`GET /operations/{id}`、`GET /alerts` | 探测/扫描/同步/发布等操作逐项进度、告警与失败原因 |
| `GET /events` | 具有通知游标和心跳的 SSE 失效通知；连接后拉快照，缺口/过期重取，不在前端累计计数 delta |
| `GET /admin-users`、`POST /admin-users`、`PATCH /admin-users/{id}`、`GET /audit-events` | 管理账号、角色、禁用与脱敏操作审计；仅授权管理员可写账号配置 |
| `GET /api-keys`、`POST /api-keys`、`GET /api-keys/{id}`、`PATCH /api-keys/{id}` | 由管理会话创建/编辑多业务 Key 的授权/限额，只返回脱敏详情；新秘密值仅首次成功响应显示 |
| `POST /api-keys/{id}/actions`、`DELETE /api-keys/{id}` | disable/enable/revoke/rotate 与撤销后归档；轮换保留逻辑 Key 主体 |
| `GET /api-keys/{id}/usage`、`GET /api-keys/{id}/access-events` | 按 Key 查看资源用量、剩余额度、调用/拒绝记录，受管理权限过滤 |
| `GET /content`、`GET /content/{id}`、`GET/HEAD /content/{id}/download`、`DELETE /content/{id}` | 管理会话授权范围内的内容检索、详情、下载与删除；不接受业务 Key |

管理契约必须遵循：

1. 配置写入返回 revision/ETag，更新与删除要求 `If-Match`；版本过期返回 `409 config_revision_conflict`，不得覆盖其他管理员的变更。凭证仅写入受保护存储并返回引用/指纹，GET 不返回明文。
2. 创建、发布、同步、取消和重试携带管理身份范围的 `Idempotency-Key`；同键同请求恢复原操作，同键不同请求返回冲突。
3. 探测、扫描、验证、同步和配置应用返回 `202` 与 operation_id。操作按目标渠道返回结果；整体成功不等同于能力已批准或公开模型已发布。所有异步操作可在进程重启后恢复。
4. Workflow 样本验证复用任务调度/租约/上传协议，明确标识内部验证任务、资源预算和授权渠道，不扣客户余额；仍记录内部算力成本。不得由后台直接无锁提交 `/prompt`。
5. 队列响应声明 filters、snapshot_id、采样时间和事件游标，列表使用稳定游标分页。共享候选、已分配、本机 pending/running 分字段返回，过期观测带 stale 标记，未知值为 null。
6. 终态 retry 创建新的关联 job 并重新校验准入，返回新 ID；未知执行中的重试返回 `409 task_state_conflict`。排空/取消的 HTTP 接收成功不表示 GPU 已停止。
7. 删除或改变仍有活动引用的配置返回 `409 channel_in_use` / `dependency_in_use` 及授权范围内的阻塞摘要；未验证绑定返回 `409 capability_not_verified`。删除不级联清除历史任务、模型文件或产物。
8. 管理审计记录身份、原因、目标、前后版本与结果；请求中的密钥和敏感素材不进入审计。批量操作逐项返回结果，不能将部分失败误报为全量成功。

上述接口是首版需求契约，完整交付仍须通过文档 05 的浏览器操作验收，接口可调用不能替代可视化页面。

## 14. 多业务 Key 契约

本地业务 Key 的有效权限为平台、租户/项目与 Key 当前策略的交集。所有创建入口归一化后同时检查 `jobs:create` 与 `images:create` 或 `videos:create`，再解析模型和冻结 Workflow/允许路由。每 Key 可分别设置图片/视频、模型/Workflow/渠道和规格上限，不能由原生 jobs 或公开别名转换绕过。

jobs/assets/artifacts/内容目录按可信 tenant_id、project_id、逻辑 owner_key_id 归属，独立 Key 默认隔离。Admin 可显式授权同租户的项目共享/指定创建 Key 内容范围；请求中的项目筛选、标签或自报身份不能改变归属。查询、计数、详情、取消、预览、Base64、HEAD/Range 下载、删除与签名刷新均检查相应操作和资源范围。

Key 策略变更要求版本条件；创建/轮换支持管理幂等键，首次响应丢失时重试只恢复标识，不再次返回原秘密值，管理员可另行轮换。仅保存校验摘要/展示指纹，接口、审计和日志不回填秘密。有效期、IP 和停用/撤销在授权点复核，返回受保护的长等待结果前再次校验。

Key 请求频率、每日新任务、未完成任务、生成并发及图片/视频资源额度分别限制；任务预留与幂等/内部重试协调，轮换和服务重启不重置。每日账期默认 Asia/Taipei 自然日。现有任务是否继续、下载模式、Sub2API 可信主体映射与唯一客户账本边界按文档 08 执行。

authenticated_proxy 的文件地址不包含长期 Key，GET/HEAD/Range 每次鉴权且不能被网关转存后换成无权限的公开 URL。显式 signed_url 模式可支持普通 URL 下载客户端，但签发后可持续有效至到期；发布网关兼容能力前需实测下载/转存是否保持所选策略。

## 正式范围补充：ComfyUI 音频产物

正式交付须支持 ComfyUI Workflow 音频产物，先纳入统一异步任务/查询/下载。音频任务标识、输入、媒体元数据、配额和兼容投影需显式版本化并同步 DTO/schema/fixture/OpenAPI/生成类型；不直接改写已发布 0.4 的枚举。兼容声明按实际验证范围发布，当前候选尚无音频接口验收，详见 [54](./54-ComfyUI音频产物范围与开发约束.md)。
