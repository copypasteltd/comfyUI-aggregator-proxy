# 34 · Sub2API 生成兼容与产物访问

2026-10-23。接续 [32](./32-Sub2API公开任务接入.md)、[33](./33-Sub2API服务托管与生命周期.md)，涉及 DEV-07、11～19、23～27、29～32。本记录对应文生图同步/异步、视频 JSON 创建/查询和原任务产物访问增量；图片编辑和素材上传后续进展见 [35](./35-Sub2API素材与图片编辑接入.md)。模型发现及完整服务联调仍属原首版范围。

## 接口与归一化

文生图使用 POST /v1/images/generations 和同路径 /async，只接受 model、prompt、n、size、quality、response_format、user；n=1，默认 b64_json。视频 POST /v1/videos 接受 model、prompt、size、seconds、quality、references；seconds 可为受限十进制数值或同格式字符串，存在 references 时映射 references_to_video，否则 text_to_video。字段大小、十进制秒数、参考图顺序及角色沿用冻结原生解析器；拒绝重复/未知字段、流式参数和任意 URL/Workflow。

两类入口均归一化到原生 JobRequest，复用同一持久预留、客户规范摘要、规范 operation 和后台协调器；同 Key、同幂等键、同规范请求可以跨兼容/原生入口恢复同一任务。response_format 仅为读取投影选项，不影响生成或计价。不会在兼容入口连接内重复派发、直接确认准入或结算。视频创建采用已实现控制面契约的 202；文档 03 已同步修正较早的 200 示意。

图片异步及 GET /v1/images/tasks/{rc_id} 返回稳定公开 ID/task_id、image.generation.task、processing/completed/failed、原生 job_status 和 poll_url。视频对象采用 video、queued/in_progress/completed/failed，保留原生 job_status；GET /v1/videos/{rc_id} 不接受图片任务。错误不得暴露远端 ID、服务凭证、路径或提示词。既有任务查询使用原目标，当前模型/分组/Key 权限仍须成立。

同步图片只在获得最终产物后返回 created/data；默认 Base64，显式 URL 使用受信 public_origin 拼接需鉴权的内容下载路径，绝不使用客户 Host/Forwarded 头。默认等待 60 秒，同步等待最多 8 个；超过等待预算返回 504 sync_wait_timeout，带稳定 job_id 和 poll_url，原任务继续。过载在预留前拒绝，不造成不可见任务。等待、轮询、媒体下载均有时限；不以空白保活或 SSE 冒充同步兼容。

同步预留前通过新增 GET /gateway/v1/access-policy 读取当前受信映射的业务策略，要求生成、任务创建/读取、产物读取及内容读取/下载权限；缺少权限不得先创建计费任务。该只读端点沿用真实网关服务认证、显式客户映射与可信 IP，返回本人当前策略，不接受服务对账身份替代业务映射；对应机器端点清单同步登记。交付时继续逐次重新授权，预检不构成长期权限缓存。

## 产物归属与迁移

新增 251 向前迁移 render_control_content_bindings：公开 content ID 与原实例/intent 的不可变关联。只有经过当前主体授权读取的原远端 Job、匹配原模型/能力且通过客户端 ID 校验的产物可以登记；同 ID 绑定不同任务时拒绝，不能猜测路由或遍历其他网关。关联不存正文、秘密、签名 URL 或客户金额，也不改变资金和任务状态；短事务完成后才发 HTTP。

GET /v1/content/{id} 及 GET/HEAD /v1/content/{id}/download 按实例/user/Key 读取原意图，复查当前模型授权，再向原网关传递当前可信业务主体和 IP；控制面继续核查 content:read/content:download、服务归属及当前内容策略。DELETE 使用同一归属与当前删除授权，不将查到绑定视为永久授权。GET/HEAD /v1/videos/{rc_id}/content 先确认原视频任务成功及可读产物，再走同一下载实现。

当前 Go 关联目录只允许原实例/user/逻辑 Key 访问拥有的产物。控制面已有的显式共享策略不能据此视为已贯穿 Go 目录；共享绑定、任务/内容列表仍待实现。直接 content ID 访问必须先有经授权的原任务产物关联，不能靠猜测 ID 向全部网关探测。

媒体使用固定 HTTPS 网关和单次 GET/HEAD，不跟随重定向、不接受客户地址、不转发客户 Authorization/Cookie 或服务响应中的 Set-Cookie/Location。只转发经过校验的媒体类型、长度、单区间 Content-Range、SHA-256 ETag 及本地构造的安全下载文件名；使用固定大小缓冲，单对象最大 2 GiB、单次请求最多 300 秒、并发下载最多 8 个。完整小图片 Base64 另限 32 MiB 并核验 SHA-256；范围流不声称已校验完整对象摘要。未知长度、压缩流、错误范围或类型拒绝。连接中途失败不能在已开始的文件流后追加 JSON 或伪造完整交付。

247～250 不改；迁移独立运行。开发库不因本轮测试迁移；测试仅在备份核对后的随机原生 PG 库中演练完整迁移链与重放。真实对象存储、Worker、ComfyUI 和完整上游启动仍待后续提供资源与联调。

## 宿主与认证

宿主按明确的兼容路径接入，rc_ 查询归本 Provider，其他供应商的图片/视频查询交原 Handler。生成请求读取有界 JSON 后，仅把受信配置列出的本 Provider 模型交本处理器；其他模型保留原路由。解析失败拒绝，不让畸形请求绕路。进入 Provider 后不因分组/权限失败回落到普通供应商。

生成入口继续真实上游新建计费鉴权；GET/HEAD 已有 rc_ 任务和 content_ 产物的读取仅免除新建余额检查，不免 Key/user/IP/分组认证。禁用/删除身份拒绝；旧任务可在余额耗尽或 Key 过期后恢复读取。Provider 的鉴权错误统一成固定 OpenAI error 外形，不能改变上游其他路由错误行为。

## 验证要求

使用实际上游认证/仓储、原生 PG 和 HTTPS 协议服务，验证跨入口幂等/并发、默认值冻结、未知字段/数值/体积边界、同步超时保留、受信 URL、异步终态、错误任务类型、范围/HEAD、当前权限收紧、跨 Key/实例隔离、绑定冲突、媒体头/重定向/过大/截断/哈希失败和资源清理；实际 cmd/server 另编译。失败记录保留，匹配当前源码的测试和收尾证据通过后才登记组件完成。

完整 Provider、运行联调及真实生成标志仍保持未完成；本轮不得把 HTTPS 协议夹具当成真实媒体交付或硬件验收。

## 生成兼容增量实测（素材/编辑前）

[Go 客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-assets.json) 25 项顶层测试、84 个子用例通过，5.27 秒。[原生组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-assets.json) 90 项顶层测试、182 个子用例通过，另 1 项子进程 helper 在主进程跳过；总耗时 41.41 秒，含原生测试程序编译 10.43 秒。实际重放 302 个迁移、106 张测试表，备份已核对、随机测试库已删除。

[实际 cmd/server 编译](./evidence/2026-10-23/sub2api-provider-native-build-before-assets.json) 通过，22.47 秒；只编译，不启动完整上游服务。[应用检查记录](./evidence/2026-10-23/sub2api-compatibility-application-checks.json) 登记 Python 235 项完整回归、锁文件/Ruff/mypy、OpenAPI 导出、TypeScript 生成和前端类型/构建通过；104 个 OpenAPI 路径、134 个方法路径注册，127 项设计端点全部匹配。路由注册不等于行为或硬件验收。

[源码与运行收尾](./evidence/2026-10-23/sub2api-compatibility-closeout-before-assets.json) 核对 39 项维护/生成文件、两处经审查补丁、脚本及二进制摘要一致，247～250 未改；另核对 182 项应用源码与生成产物摘要。开发库保持原迁移和 40 张业务表，9 项注册进程身份一致，无测试/编译进程或测试/恢复临时库遗留。项目证书验证的 live/ready/Admin 为 200，匿名策略预检为 401，运行中 OpenAPI 与导出文件一致；本轮未使用浏览器或虚拟化。

失败过程与修复见文档 17，原始报告保留；上述为素材/编辑之前的历史快照。后续素材上传、同步/异步编辑及受控 workflow_options 见文档 35；input_reference/workflow_options 的完整标准化适配、模型发现、任务/内容列表与显式共享、真实副作用消费者及套餐/价格倍率、完整服务运行仍待完成；真实 ComfyUI、五类 Workflow、私有 S3 和 10/20 台设备等待后续资源。
