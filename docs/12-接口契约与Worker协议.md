# 12 · 接口契约与 Worker 协议

第 57 增量新增可选内部请求头 `X-Worker-Node-Id`。新 Worker 固定发送其凭证 JSON 中的 `node_id`；服务端先核验 Bearer，头值存在时必须与真实认证节点一致，错配 401、格式错误 422，且不进入注册、boot 或资源变更事务。它不是身份选择器，不能代替 Bearer。缺省头兼容旧 Worker/0.4.0；新 Worker 同时校验注册回执的节点和本次 boot，错配/缺失即停止，后续不领取。升级先控制面再 Worker，旧控制面忽略该头时回执检查不等于注册前保护。单机节点 mutex、journal 身份及显式旧记录接管见 [57](./57-Worker启动身份与原生进程保护.md)。

本文把 [03](./03-接口与Sub2API接入规范.md) 的外部行为落实到开发契约。v0.4 的机器契约在 [contracts](./contracts/README.md)，覆盖原生任务、Key 策略、Worker 事件、状态机和端点权限。已登记路由的 OpenAPI 在 app/config/openapi.json；尚未完整实现的端点和 DTO 继续追踪，不能把设计清单作为运行验收。

## 1. 通用规则

| 项 | 开发约定 |
| --- | --- |
| 标识 | 内部 UUID v4；外部 `job_`、`asset_`、`artifact_`、`content_`、`key_`、`node_`、`gpu_`、`channel_`、`attempt_`、`operation_` + UUID hex；业务端不解析 ID；历史示例短 ID 仅示意 |
| 时间/数量 | 对外 Unix 整数秒；内部与审计 UTC；duration_seconds 可为最多 3 位小数，内部整数毫秒；bytes 为非负整数 |
| 未知字段 | DTO 默认 extra=forbid；明确定义的 SDK metadata 白名单除外；执行/价格相关未知字段 400 |
| Headers | HTTPS；X-Request-ID 服务端生成并验证客户值长度/字符；Authorization/幂等键不可记录原值；no-store 用于私有 JSON/文件策略 |
| 请求上限 | 普通 JSON 1 MiB、管理工作流导入 10 MiB 为初值；Worker command 回执封套 8 MiB，拉取源与候选仍各限 1 MiB；媒体用独立流式上限；先限制读取再解析，不等待无界 body |
| 分页 | limit 默认 50、最大 100；cursor 绑定排序/过滤/主体范围/版本并校验；过期 400 cursor_expired，不扩成全租户查询 |
| 幂等 | 生成建议提供、管理写操作必须提供；ASCII 16～128 字符；操作语义按规范化入口合并；窗口与重放见 11/15 |
| 配置并发 | GET 返回 ETag 与 revision；修改/删除要求 If-Match，缺失 428 precondition_required，过期 409 config_revision_conflict |
| 异步 | 创建 202 + Location/Retry-After；云端未确认准入不得对客户投影“可执行”；同步图片沿 03 返回最终结果或明确错误 |
| 删除 | 202 表示清理请求已持久化；204 表示无剩余公开资源；活动引用 409；不删除任务历史 |

错误统一 `{error:{message,type,param,code},request_id}`，不返回堆栈、磁盘路径、内网地址或原始网关错误。401 无有效身份；403 操作不足；404 资源不可见；409 状态/修订/幂等冲突；413 上限；422 已识别参数语义不符；429 限流/额度；503 依赖或容量不可用。422 与兼容入口历史 400 的映射由 Provider 明确测试，不能让框架默认错误形状泄漏到公共接口。

## 2. 三个信任入口与网关

- `/v1`：本地业务 Bearer Key；只认服务端派生的 tenant/project/key。`/admin/api/v1`：独立 HttpOnly/Secure/SameSite=Lax 会话，写操作 `X-CSRF-Token` 和 Origin 校验。`/internal/v1`：节点独立 Bearer 凭证 + 私网 HTTPS；凭证只绑定一个 node。
- 网关调用使用独立 `/gateway/v1`，不靠公网 `/v1` 自报 `X-Tenant-ID` 切换身份。首版采用私网 HTTPS + 每网关独立高熵服务凭证，服务端配置允许的 issuer；仅由已鉴权的 Provider 传 external_tenant_ref/external_key_ref。其可信性来自入口和服务身份，不来自客户头。入口不得记录客户原 Key，不支持缺失映射时回退共享主体。
- 网关 `/gateway/v1/admissions`、`/{id}/confirm`、`/{id}/abort` 及任务/文件代理只操作本 gateway 的显式绑定；确认/取消均幂等。`/gateway/v1/assets` 和内容代理用于 Provider 上传/读取多参考素材，仍归属可信客户逻辑 Key，不能用网关服务身份拥有全体素材。请求绑定 provider_request_id、external_task_ref、规范请求摘要、价格版本和授权引用，默认 pending 预留 120 秒，超时对账。详见 03 的跨服务准入和 11 的事务。
- GET `/gateway/v1/access-policy` 返回当前已认证且有显式映射的业务主体策略，用于兼容同步交付预检；仍检查网关身份、映射、业务 Key 与可信 IP，没有 customer 映射的服务对账身份不能调用。预检不能替代之后的任务/内容授权，不新增权限缓存；见 [34](./34-Sub2API生成兼容与产物访问.md)。
- Worker 与网关凭证不能进入浏览器；业务 Key 不换取管理会话。密码采用成熟密码哈希库；管理会话和 API Key 校验方案在实现中使用受审查库及恒定时间比较，禁止自制加密协议。

网关增量见 [20](./20-网关准入与对账实现约定.md)：每个外部主体映射到独立逻辑 Key；admission 行同时保存网关任务绑定，复合外键约束网关、绑定、租户、项目、Key 和 job 一致。后台 settlements 拉取/确认及 [30](./30-网关请求关闭与恢复协议.md) 中固定的请求最小查询/关闭允许在客户 Key 停用后清理本服务原意图；它们不开放内容、新建、confirm 或运行中任务取消。其他网关请求仍检查当前业务权限。

## 3. 原生创建与策略契约

`job-create.schema.json` 定义 `model/task_type/input/parameters/client_request_id`。CAP-01～05 分别是 `text_to_image`、`image_to_image`、`references_to_image`、`text_to_video`、`references_to_video`。生成入口全部解析为同一用例：规范化 → 操作 scope → 素材范围 → 具体 release/规格/路由 → 幂等/配额事务。references 数组有序，角色须符合发布包；多参考任务至少两个参考，单参考视频不能冒充 CAP-05。模型发布可以声明更小上限。

`key-policy.schema.json` 定义显式 allow 范围和限额结构。`mode=all_authorized` 仅表示继承上层已授权范围，绝不跨租户；空 selected_ids 表示无授权。`limit=null` 只在显式 unlimited 模式允许，0 表示禁止。内容默认 owner_only，shared_creator_keys/project_shared 需管理角色额外授权并在保存时检查租户和项目。

Schema 只验证形状，不证明模型、asset、channel 存在或有权，也不能代替 release 的参数范围/节点依赖验证。Schema 的宽上限是协议上限，运行时取平台、项目、Key 与发布画像的交集。

首版协议沿用8000字符提示词、最多4张参考、每job一个主图片/视频输出；mask、strength、负面词、参考权重、质量档及可变fps只有release明确支持才接受。input.source_image为`{asset_id}`，references为有序的`{asset_id,role,weight?}`；具体示例由fixtures覆盖。返回安全状态与返回产物/敏感字段分别检查权限，不能因jobs:read自动带出全部文件。

## 4. Worker 身份、启动与领取

2026-10-23 容量子协议新增 `Heartbeat.capacity`（native-capacity-v1）；完整字段与派生预算规则见 [44 Worker 容量保护](./44-Worker容量保护与本地产物保留.md) 和 [机器契约](./contracts/worker-capacity.schema.json)。新派单必须有当前 boot、15 秒内且 ready 的有效观测；缺少观测只允许既有任务恢复/完成与已提交 claim 重放。旧 Worker 升级顺序与一小时本地产物保留边界均见 44，不通过放宽为“默认健康”兼容旧版本。

| 方法与完整路径 | 请求关键字段 | 成功与限制 |
| --- | --- | --- |
| `POST /internal/v1/register` | protocol_version=0.4.0、boot_id、inventory（gpus 最多 32 个，可声明 audio-output-v1） | 200：node_id、boot_id、heartbeat_seconds、reconciliation_required；只允许已登记且启用的节点，事务内复查请求凭证标识，不能跨凭证轮换注册 |
| `POST /internal/v1/heartbeat` | boot_id、sequence、观察时间、GPU/queue 摘要、正在持有的 attempt、磁盘/上传积压 | 200：server_time、下次心跳间隔和恢复指令；不通过心跳直接续所有任务租约 |
| `POST /internal/v1/claims` | request_id、boot_id、已登记的可用 gpu/channel、可接上传预算 | 200 租约；204 无任务；恢复未完成 409 node_recovery_required |
| `POST /internal/v1/attempts/{id}/renew` | boot_id、fencing_token、request_id | 200 新 server lease_expires_at；旧代数/旧 boot/过期 409 lease_conflict |
| `POST /internal/v1/attempts/{id}/events` | Worker event envelope | 200 accepted/replayed、权威 state/version；冲突 payload 409 event_conflict |
| `POST /internal/v1/attempts/{id}/upload-authorizations` | 当前租约、输出名/类型/bytes/hash、分片恢复信息 | 200 任务/对象限定短时凭证，不允许自定义 bucket/key/ACL |
| `POST /internal/v1/attempts/{id}/complete` | 当前租约、完成 request_id、不可变 manifest、提交摘要 | 202 verifying 或 200 既有确认；不因 Worker 自报完成立即 succeeded |
| `GET /internal/v1/attempts/{id}` | 节点身份；查询 boot_id、fencing_token | 200 当前允许读取的最小状态；其他节点 404，旧 boot/错 fence 409；读取不续约或恢复权属，详见文档 26 |
| `POST /internal/v1/reconciliations` | boot、旧 attempt/fence、journal 条目、Comfy queue/history、进程/文件证据 | 202 核对操作；服务端决定恢复/新代数/隔离/可重试，不由 Worker 自决 |
| `POST /internal/v1/commands/claims` | boot、request_id、执行能力 | 200 固定动作命令；204 空；不可借 command 领取任意执行权限 |
| `POST /internal/v1/commands/{id}/receipts` | command_digest、boot、applied_revision、结果、证据摘要 | 200 幂等回执；摘要不同/错误目标 409/403 |

register 与 heartbeat 可以在恢复期间使用；claims 必须等该节点全部旧占用完成核对。重启产生新 boot_id；旧 boot 不自动获得新租约。旧结果存在时保留恢复路径，不能因为新 boot 注册成功就清空旧 attempt。

节点图形登记、凭证文件 JSON 与生命周期边界见 [56](./56-Windows节点登记与凭证管理.md)。停用/轮换清空当前 boot 与观测，不删除旧 boot 或 GPU 身份；重新注册须使用新 boot，并使旧渠道和 Workflow 验证失效。Admin 新增接口使用 nodes:read / nodes:manage、CSRF、幂等与配置 revision，Worker 凭证不能调用管理端点。

租约响应必须包含 job_id、attempt_id、node/gpu/channel、fencing_token、lease_expires_at、server_time、protocol_version、冻结 execution_snapshot、workflow_release/hash、channel_revision/secret_envelope、输入授权与上传预算。不包含其他客户的 Key、任意 shell 或完整平台秘密。Worker 接收先写 journal，再确认进入 preparing。

claims 的 request_id 在该 boot 内至少保留到对应租约终结及协议重放窗口。相同 ID 已领取后只返回原 attempt 的当前投影，已终结则返回 closed 并要求新 request_id；空领取可在 2 秒后用新 ID 再试。Worker 不并发对同一 GPU 发多个 claim；服务端仍用锁保证正确性。

建议初值：心跳 5 秒、观测 15 秒后 stale、租约 60 秒、15 秒续约。server_time 是判断依据；Worker 使用单调时钟估算剩余时间。续约不确定时停止新提交并继续观察已有任务，不能自行宣布成功或释放槽位；恢复上报由 reconcile 处理。初值需按控制面压测和网络抖动调整，不直接承诺 SLA。

## 5. Worker 事件、状态和 journal

`worker-event.schema.json` 固定 envelope：protocol_version、event_id、node_id、boot_id、job_id、attempt_id、fencing_token、journal_seq、observed_at、type、payload。fencing 为服务器分配正整数，journal_seq 在本 boot 流内递增。去重按 node/boot/event_id，重复同内容返回原回执；同 ID 不同内容拒绝。journal_seq 仅用于发现本地缺口，不要求不相关上传任务串行。

首版 type：preparing、submit_intent、submitted、progress、outputs_collected、gpu_released、upload_progress、execution_failed、cancel_confirmed、reconcile_observation。payload 按类型验证；progress 不得直接指定 state=succeeded，execution_failed 不得凭超时声称已停机。云端决定状态转换，允许边与必要 guard 见 [状态机契约](./contracts/job-state-machine.json)。

journal 在本地受 ACL 保护目录，SQLite 单写、事务 FULL 落盘。至少记录冻结执行摘要、attempt/fence、提交意图、预定关联 UUID、Comfy prompt_id、状态事件待发送、产物路径/hash/稳定性、上传分片与云端回执。submit_intent 提交成功后才 POST `/prompt`；存储异常立即停止新提交。提交响应丢失不重发，核查原 prompt；WS 断开只重连/查询。

不能向 ComfyUI 增加未经验证的自定义字段并假定 history 会回显。WP-05 按固定版本测试预定 prompt_id/extra_data 的实际行为与取消语义；不支持关联时保守 reconciling。ComfyUI 不提供的幂等能力不能在适配器里口头声明。

状态中的 cancel_requested/reconciling 保留 resume_state 和资源事实，不靠状态名称推断 GPU 空闲。`gpu_released` 要附 GPU 计算/编码已停止与文件稳定证据；若异常路径先确认停止但没有产物，可由恢复用例释放槽位，再决定 retry_wait/failed。attempt 历史不删除。

## 6. 受控管理命令

允许动作仅有 probe_channel、scan_inventory、pull_workflow、apply_workflow_release、apply_channel_config、collect_diagnostics、inspect_model_file。payload 使用受控 URL/相对路径/发布摘要/版本，不带任意系统命令。下载模型权重、安装 pip/npm/自定义节点和修改驱动不在该协议内；需要时作为单独运维过程审查。

模型目录及选中文件核验见 [45](./45-渠道模型清单与受控登记.md) 和 [model-file-receipt.schema.json](./contracts/model-file-receipt.schema.json)。scan_inventory 增加仅本机 models 目录的有界元信息；inspect_model_file 只读流式计算单文件哈希。云端按签发快照的 boot/revision/目录摘要校验回执，登记事务另行复核当前来源和时效，服务端 completed_at 控制检查证据有效期。合法只读观测不会因读取期间的目录过期或配置变化阻塞后续指令；过期证据仍拒绝登记。不占用 GPU 管理槽，不把目录发现等同依赖哈希已验证。

每条命令携带 command_id、operation_id、target node/channel、expected_revision、deadline、payload_digest、requires_drain。Worker journal 去重；目标 revision 已应用且摘要一致直接返回 already_applied。旧 revision、过期和依赖不符明确拒绝。会改运行环境的动作必须在排空且未知占用清零后执行；命令领取不占用生成槽不代表可与推理随意并行。

pull_workflow 只读管理员授权的导出目录/API 来源；ComfyUI 默认 API 不保证存在“列出所有用户工作流”的接口。导入 API-format 与 UI-format 时明确转换支持范围，转不了就拒绝，不能将丢节点的 JSON 发布。push 先暂存、校验摘要、原子改名，再回执；部分目标失败逐项保留。

本地目录读取、受限文件名、源快照回执、管理员专属预览与确认导入见 [41](./41-Workflow渠道拉取与草稿导入.md) 和 [回执 Schema](./contracts/workflow-source-receipt.schema.json)。旧 Worker 未实现 `pull_workflow` 时须返回不支持，不能降级为远程 shell 或直接发布。客户端不能指定读取根目录、远程 URL 或任意绝对路径。

目录发现扩展见 [42](./42-Workflow目录浏览与来源状态.md) 和 [目录回执 Schema](./contracts/workflow-catalog-receipt.schema.json)。心跳每渠道可选上报 `workflow_export` 固定来源状态；旧 Worker 缺省时显示未知，不能据此启用目录模式。目录通过 `pull_workflow` 的 `mode=catalog` 执行，省略 mode 保留单文件方式；读取所选文件时可附 `expected_source_digest`。控制面先于 Worker 更新，无新增 DDL，当前启动/版本观测和目录回执仍需独立校验。

## 7. Admin API、SSE 与客户端

03 的完整管理端点进入 [endpoint-catalog.json](./contracts/endpoint-catalog.json)；每项规定 identity、scope 或 admin permission、幂等/版本条件及关联交付包。未列出的管理捷径默认不开放。当前清单只描述路由与权限，不代替全部 DTO 的 OpenAPI。

Admin SSE 只发经过角色/租户过滤的 invalidation、operation_summary、session_revoked 和 heartbeat，不直接推全量敏感详情。客户端连成功后取一致快照，通知触发合并刷新；快照在同一事务读取，带 snapshot_id、server_time、observed_at、stale、filters。恢复/缺口全量重取，5 秒安全轮询与合并节流遵循文档 13。事件不作精确计数 delta，避免竞态累加。参见文档 11 的事务说明。

WP-01 导出服务 OpenAPI，CI 比对稳定接口差异；WP-08 由其生成 TS 客户端并锁定生成器版本。类型生成产物有来源版本和 hash，不手工改 generated 文件。破坏变更需新协议/入口版本与兼容窗口，不能直接把旧 Worker 或客户 SDK 拒之门外；协议不兼容节点停止新派单并保留恢复查询。

Admin 内容目录接受 kind=input/output、page 与可选 key_id，返回规格和创建主体元数据；资源详情/下载/删除同时接受带类型前缀的 asset/content ID。原始输入下载另查 content:sensitive；删除请求返回 202，活动引用冲突返回 409。业务与网关删除保持 owner-only，不继承共享读取范围。具体保留与 GC 约束见 [文档 21](./21-内容保留与回收实现约定.md)。

模型发现的单项查询、能力响应安全字段与上游合并约束补充见 [文档 36](./36-Sub2API模型发现与能力投影.md)；能力响应机器契约为 [model-discovery.schema.json](./contracts/model-discovery.schema.json)。

公开及网关任务/输出内容集合采用 limit/cursor，不接受 page；先应用当前授权再取页。响应的 object、data、has_more、next_cursor，固定到期的游标、网关服务范围以及 Provider 显式共享路由见 [文档 37](./37-任务与内容游标及共享访问.md) 和 [collection-page.schema.json](./contracts/collection-page.schema.json)。Admin 内容目录仍保留上述独立分页契约。集合只返回安全元数据，不携带完整输入或 Workflow。

2026-10-23 标准化参考输入：`references_to_image` 保留 2～4 图；`references_to_video` 增加单图 `first_frame` 的受控输入，须与冻结的连续参考槽位完全一致。兼容 JSON/multipart、Workflow 选项、只读上传前检查与事务内复核见 [38](./38-标准化参考输入与Workflow选项.md)。

失败归因与规格管理接口见 [46](./46-生成失败归因与规格隔离.md) 及 [spec-isolation.schema.json](./contracts/spec-isolation.schema.json)。execution_failed 保持原字段，新增固定 error_code comfy_oom/comfy_output_invalid；只有当前 prompt 的明确终结且 queue_running/queue_pending 均不含该 prompt，或已确认释放后的固定媒体拒绝，才确认停止。OOM 只识别 execution_error 的受控异常类型，不读取任意错误文本；通用错误保留旧语义。GET/POST /admin/api/v1/spec-isolations 与 POST /admin/api/v1/spec-isolations/{identifier}/actions 使用独立管理身份；写入强制 CSRF、幂等和 If-Match，恢复须校验真实成功验证任务与云端产物证明。
## 有证据内部重试的协议补充

第 50 增量保留 Worker 协议版本 0.4.0，新增固定准备错误分类 preparation_transport_failure；它只由 durable journal 尚为 accepted、没有提交意图时的受控网络/状态失败产生。submit_intent/posting、prompt 已知及未知提交继续走既有确认/核对路径，不能只按异常名称重新提交。云端还需独立检查租约、状态、无关联提交、停止证据、次数和截止时间，详见 [50](./50-有证据内部重试与计量保全.md)。

原生 job/授权集合/Admin 的可选 retry 扩展按 [job-retry.schema.json](./contracts/job-retry.schema.json) 返回 count、limit、next_at、last_error；旧持久响应允许缺少扩展。内部重试保留 job/seed/Workflow/准入归属，和 Admin 对终态创建关联新任务的人工重试分开。等待内部重试的任务可按现有权限、CSRF、revision 和幂等规则取消或调序；不增加业务 Key 的管理权限。


队列快照的可选 dispatch_policy 扩展见 [51](./51-租户公平调度与等待老化.md) 和 [schema](./contracts/dispatch-policy.schema.json)。Admin 只读显示固定份额、成本与老化规则，不泄露其他租户历史，也不承诺精确全局位置。客户/Worker 不能设置调度成本、权重或优先级；现有 Worker 0.4.0、业务和网关任务字段不增加必需项。

## 租户项目管理接口

| 路径 | 方法 | 权限 |
|---|---|---|
| /admin/api/v1/tenants、/admin/api/v1/projects | GET / POST | scopes:read / scopes:manage |
| /admin/api/v1/tenants/{identifier}、/admin/api/v1/projects/{identifier} | GET / PATCH | scopes:read / scopes:manage |
| /admin/api/v1/scopes/options | GET | scopes:read |

写入要求独立 Admin 会话、CSRF、16～128 位 ASCII Idempotency-Key；PATCH 同时要求 If-Match 与 expected_revision。列表 page 为 1～10,000、每页最多 50，支持 q、enabled 和归属过滤。options 默认列有效租户/项目组合，enabled=false 列任一层停用的组合；project_id 可解析已停用的当前归属。完整 DTO、状态码、限额及版本语义见 [52](./52-租户项目与分层配额管理.md) 与 [资源限制契约](./contracts/resource-limits.schema.json)。


## 音频范围的工程约束

后续音频任务/能力、输出 manifest、实际媒体元数据、输入模式和配额必须同步版本化；当前五类枚举保持现状直到新契约冻结和实现就绪。Worker 从冻结 Workflow 声明的输出节点取音频，校验真实文件并走既有上传/确认/保留协议。提交未知、断联、上传重试和事件去重保持相同语义，不能为音频增加重复执行旁路，详见 [54](./54-ComfyUI音频产物范围与开发约束.md)。

## 第 62 增量：管理会话空闲与交互

第 62 增量新增 POST /admin/api/v1/session/activity：仅当前 Cookie 管理会话、正确 Origin/CSRF，可提交固定 activity=interactive。登录和 GET session 增加可空 lifetime，包含绝对/空闲截止、空闲秒数及服务器 Unix 秒；普通查询与 SSE 不续期。业务 Key、Worker/网关身份和 Worker 0.4.0 协议不变，详见 [62](./62-管理会话空闲超时与交互续期.md)。

## 第 63 增量：管理审计保留

新增只读 GET /admin/api/v1/audit-retention，要求管理会话和 audit:read。响应包含当前 enabled/retention_days、batch_limit=500、snapshot_at 和可空 last_batch；读取不触发清理，不提供业务 Key 或浏览器写入入口。当前策略与历史批次按各自时点展示；见 [63](./63-管理审计保留与有界清理.md)。


## 第 64 增量：渠道检索与详情直达

GET /admin/api/v1/channels 使用 admin-channel-query，未知查询字段拒绝；无条件仍兼容原含历史登记的列表。现有详情响应增加强类型描述，不增加字段或管理权限。见 [64](./64-渠道检索与详情直达.md)。


## 第 65 增量：渠道删除保护

删除前查询有界阻断观测，DELETE 仍在 Node/GPU/Channel 锁内检查当前任务、未关闭尝试、在途命令、已发布模型和物理 GPU 占用/管理保留。图形入口列出公开 ID 和处理入口，失败不自动重试或释放资源。冻结契约与验证边界见 [65](./65-渠道删除约束与并发保护.md)。


## 第 66 增量：管理指令核对

完成结果与未知执行分开处理，过期/旧 boot 的已完成指令只关闭旧操作并要求重验证，未知不释放占用、不重跑；Admin 显示状态并允许明确重应用当前渠道配置。事务、兼容、预算与验证边界见 [66](./66-管理指令持久结果与恢复核对.md)。


## 第 68 增量补充

核对中任务可经授权 request_cancel 进入 cancel_requested，仅记录意图。原 attempt、未知 GPU occupancy、fence 和过期租约状态不因此改变，也不据此释放资源或允许重跑；Worker 沿原协议核对停止证据。批量取消复用相同事务与协议，见 [68](./68-任务批量取消与持久逐项结果.md)。


## 第 75 增量补充

新增 POST /admin/api/v1/workflows/{identifier}/draft-restorations，管理会话、CSRF、workflows:write、16～128 ASCII Idempotency-Key 与 If-Match 必须齐备。冻结请求/响应见 admin-workflow-draft-restore.schema.json 与 admin-workflow-draft-restored.schema.json。200 返回首次事务的来源及前后 revision，不能当作当前草稿仍处于该 revision 的证明；原键同正文先重放，再执行 CAS，原键不同正文冲突。只修改草稿、revision、审计/幂等与失效通知，无 Worker 指令或任务副作用。 详见 [75](./75-Workflow历史版本恢复草稿.md)。


## 第 76 增量补充

新增 POST /admin/api/v1/workflows/{identifier}/lifecycle，管理会话/CSRF/workflows:write/Idempotency-Key/If-Match 齐备，请求 expected_revision 与严格 archived 布尔，结果 workflow_id/archived/previous_revision/revision。新键同状态、CAS/公开模型/同步阻塞均 409，缺失 404；生命周期锁等待 1 秒/语句 5 秒，数据库异常固定 503。原键同正文优先回放原事务，即使后来反向变更也不再写；旧任务、版本与文件不删除，Worker 协议不增加动作。 详见 [76](./76-Workflow归档与恢复管理.md)。
