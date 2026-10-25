# 36 · Sub2API 模型发现与能力投影

2026-10-23，接续 [35](./35-Sub2API素材与图片编辑接入.md)，涉及 DEV-01～03、07、11～19、23～27、29～32。本增量先冻结模型发现、能力响应与上游共存约束，再同步控制面、Go 客户端、托管路由与验证。完整 Provider、列表/显式共享和真实资源验收继续属于首版范围。

## 模型与能力响应

公开与网关的 GET /v1/models（网关前缀为 /gateway）返回 object=list、data 数组；模型项保留 OpenAI 的 id、object=model、created、owned_by。新增 GET /v1/models/{model} 及对应网关路径，按同一当前权限投影；不存在与无权查看统一为 404。Key 必须具备 models:read；能力接口独立要求 capabilities:read，不能互相替代。所有发现均不创建任务、不预留资金。

GET /v1/capabilities 继续采用模型列表外形，增加 task_type、asynchronous=true、release（version、digest）、supported_parameters、presets 与 output（media_types、expected_count、max_bytes）。参数名与规格来自不可变发布清单；presets 只返回 quality/size/seconds，不返回节点赋值 values。output 不返回节点 ID、适配器名和对象路径。release 摘要用于标识版本，不返回 Workflow 图、依赖权重路径、运行节点、内部凭证或硬件清单。输入形态继续由 task_type 与 [原生请求契约](./contracts/job-create.schema.json) 确定，不能把固定的五类输入外形解释成所有 Workflow 都支持所有参数。

控制面先按租户/项目、published、模型/发布版本/渠道选择器过滤，再做安全字段投影。损坏或未验证的发布清单不能输出为可用能力；超过目录限额须显式报错，不能在权限过滤前截断并声称完整。模型基础目录最多 4096 项；能力目录最多 4096 项、每模型最多 128 个规格。模型列表不代表此刻 GPU 空闲或保证准入成功，真正创建仍检查当前授权、发布版本、参数、素材和容量。

## Sub2API 发现范围

Provider 只展示三个集合的交集：本机受信模型配置、当前上游用户/Key/分组及白名单、当前控制面显式客户映射和发布目录。每个目标网关按真实服务凭证、当前认证主体和可信 IP 请求，不能转发客户自报的身份、地址或服务凭证。模型集合请求按目标去重；能力的 task_type 还必须与本机路由匹配。配置外模型不进入 Provider 目录。

每次请求重新查询权限与目录，不用跨 Key 的缓存或陈旧目录兜底。目录元数据单目标最多 10 秒，整轮受 10 秒和入站 30 秒限制；每请求最多四个并行目标、最多八个并行目录请求，超出返回 429。所有 goroutine 在请求结束前退出。远端元数据沿用客户端字节限额，严格核对列表外形、重复 ID/JSON 键、字段类型、范围与五类能力，最后用固定类型重新投影，不直接转发未知字段。

远端 401/403 表示该主体在该网关不能发现模型，省略其条目；其他失败不能使用旧结果。普通合并模型列表可返回其他已核实条目和上游普通模型，并用 X-Render-Control-Catalog=partial 明示未完成；完整时为 complete。能力接口遇到任一候选目标不可核实时返回 503，不返回看似完整的部分能力。已配置模型的单项查询遇到目标不可用返回 503，不能退到普通模型同名项或另一个网关。

## 上游模型共存

只接管普通 GET /v1/models、已配置别名的 GET /v1/models/{model}、以及能力接口。配置外单模型和其他上游路由原样交回原 Handler。带非空 client_version 的模型列表仍由固定上游的 Codex manifest Handler 原样处理，包括请求、ETag、304 与元数据，不混入图片/视频模型。

普通集合先通过 Provider 的真实上游认证，再在同一进程调用原 Handler，保留原上游的认证与分组过滤。捕获原响应最多 4 MiB；合并时保留原列表外形的其他字段和普通模型的全部字段，按 ID 去重，所有本 Provider 配置别名从原列表移除后只加入当前核实条目。原 Handler 拒绝请求时保留其错误状态；损坏/过大的普通目录返回固定错误，不静默替换。合并结果不复用原 ETag、Content-Length 或条件请求结果，使用 private,no-store；发给原 Handler 的条件头和压缩协商清除，防止其 304 绕过当前 Provider 权限核对。此增量不扩大原账务鉴权豁免，也不修改第三处上游源码。

## 验证边界

2026-10-25 的 [90 · 固定 SDK](./90-固定SDK客户端兼容矩阵.md) 补充合并集合的标准核心字段：普通条目仅在缺少字段时补 object=model、created=0（上游未提供时间）、owned_by=sub2api（目录代理归属）。保留已存在的正确值与其他字段；核心字段已存在但无效时返回原 catalog_unavailable。Codex manifest、配置外单项透传、权限和别名冲突规则继续按本节原边界处理。首次 SDK 严格解析失败和修复后证据分别保留。

以真实原生 PostgreSQL、上游认证/仓储、HTTPS 协议替身覆盖：当前权限撤销、模型/渠道/版本选择器、发布下线、同名冲突、无效/重复/超大响应、固定原目标、并行限额、条件头、Codex 原文保留与普通模型字段保留。控制面验证能力投影不泄露内部字段，公开与网关单项查询一致；OpenAPI、生成的 TypeScript 客户端和应用回归同步更新。

协议格式依据 2026-10-23 核对的 OpenAI [模型列表](https://developers.openai.com/api/reference/resources/models/methods/list) 与 [单模型查询](https://developers.openai.com/api/reference/resources/models/methods/retrieve)；能力字段为本项目扩展。上游共存行为依据固定的 7da09cab4e875463baa79fc150adde700c5438df 本地源码，不宣称覆盖其他 Sub2API 版本。完整上游/控制面运行、实际 Workflow/S3 和设备规模仍待独立验收。

## 当前实现与实测

基础模型列表和单项查询不加载完整 Workflow 清单；能力集合保留批量读取，再按白名单投影，避免逐模型再次查库。目录读取优化前的 [应用记录](./evidence/2026-10-23/sub2api-discovery-application-checks-before-catalog-load.json) 和 [收尾记录](./evidence/2026-10-23/sub2api-discovery-closeout-before-catalog-load.json) 已保留，当前报告对应优化后的完整回归与运行版本。 [目录回归失败记录](./evidence/2026-10-23/sub2api-discovery-catalog-regression-first-run.json) 保留 1 失败、256 通过：既有匿名日志采样夹具使用真实时钟，跨固定分钟窗口后写入 31 条。只替换该模块在测试中的时钟引用，保留同一分钟 30 条限额断言、真实 PG 写入，并增加下一分钟恢复验证；未替换全局或事件循环时钟，也未改变业务采样规则。

[进程登记修正](./evidence/2026-10-23/sub2api-discovery-process-registration.json) 保留一次 10 项身份均匹配、但数量校验失败的结果：递归登记把 conhost 下的 HP 桌面扩展计入 API 子树。核对可执行文件、启动时间与父进程后仅移除越界登记，没有终止该桌面扩展；登记脚本收紧为已核对的 Python/控制台直接子进程，最终 9 项应用进程身份与 0 项测试进程检查通过。

[Go 客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-listings.json) **35 项顶层测试、131 个子用例通过**，32.57 秒。[原生组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-listings.json) **114 项顶层测试、224 个子用例通过**，另 1 项子进程 helper 在主进程跳过；总耗时 62.10 秒，含原生测试程序编译 11.67 秒。本增量分别新增 4/29 与 8/15 项顶层/子用例，不与历史重复累计。完整上游迁移链仍为 303 项、108 张测试表，重放通过；备份可读已核对、随机测试库已删除，247～252 摘要不变。

[实际 cmd/server 编译](./evidence/2026-10-23/sub2api-provider-native-build-before-listings.json) **通过，23.60 秒**，不启动完整上游。[应用检查](./evidence/2026-10-23/sub2api-discovery-application-checks.json) 登记 Python **257 项通过，165.60 秒**，比上轮增加 12 项业务/HTTP用例和 4 项响应 Schema 正反例；锁文件、Ruff 和 mypy（80 项源文件）通过。OpenAPI 导出、TypeScript 客户端生成、vue-tsc 与 Vite 构建通过，Vite 为 24.42 秒。106 个路径、136 个方法路径注册，129 项设计端点全部匹配；保留既有 TestClient 弃用和 Vite 大块警告，本轮没有运行浏览器测试。

[源码与运行收尾](./evidence/2026-10-23/sub2api-discovery-closeout.json) 核对 49 项维护/生成文件、两处原版与补丁、验证入口、客户端与服务器二进制/报告摘要，以及 192 项应用源码、机器契约和生成产物。只读核对开发库保持 3e15b92f08dc、40 张业务表；9 项开发进程身份匹配，测试/恢复库、测试目录、上传暂存和编译/测试进程均为 0。只重启已登记 API 树加载新协议，Runner/GC 保留；项目证书核验 live/ready/Admin 为 200，匿名模型/能力/单项及网关单项读取为 401，运行中 OpenAPI 与导出一致。

保留 [首轮 Go 失败](./evidence/2026-10-23/sub2api-discovery-go-first-run.json) 和 [首轮 Python 失败](./evidence/2026-10-23/sub2api-discovery-python-first-run.json)。Go 视频夹具补齐必填时长；Python 夹具新建不可变版本，修正错误属性名与绑定 ID 前缀，再完成回归。数据库不可变约束、实际鉴权和发布校验没有被削弱。模型发现及既有组件标志按当前证据更新，完整 provider_implemented、runtime_integration_verified 仍为 false。未使用虚拟化或打开浏览器，主动保留原生 PG、HTTPS API、Runner 和对象 GC 供继续开发。
