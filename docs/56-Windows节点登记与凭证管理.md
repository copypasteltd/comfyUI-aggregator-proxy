# 56 · Windows 节点登记与凭证管理

日期：2026-10-24。状态：本地实现与分层验证通过；真实设备与生产出口未验收。对应 WP-02/05/08/13/14、ADMIN-02/03/11、DEV-08～11/14～17/23/25/27/29～32。

## 1. 目标与边界

现有 Admin 可以管理 ComfyUI 渠道，但新 Windows Worker 的登记及接入凭证依赖 `provision-node` 命令。本增量补齐首版图形入口「Windows 节点」：登记节点、查看上报的 GPU 与活动情况、改名、停用/恢复接入、空闲时轮换凭证和逻辑归档。渠道地址、Workflow 和模型仍在各自页面管理。不会自动安装 Worker、启动 ComfyUI、下载模型或执行远程 shell。

节点登记先取得一次性 Worker 凭证，再由用户将其写入 Windows 的私密凭证文件；Worker 主动登记硬件后，在「Windows 渠道」选择该节点/GPU 添加 ComfyUI 地址。云端新建节点不伪造 GPU、在线、能力或验证记录。

## 2. 身份与契约

读取使用 `nodes:read`，管理使用新增 `nodes:manage`，默认仅 administrator 具备；业务 API Key、Worker 凭证与网关服务身份均不能访问。管理写入沿用会话、CSRF、Idempotency-Key、expected_revision 和 If-Match；首次创建除外不需要旧 revision。新的元数据 revision 不随心跳变化。

新增 `/admin/api/v1/worker-nodes` GET/POST、`/{id}` GET/PATCH、`/{id}/actions` POST。列表支持按名称或完整节点 ID 搜索、按启用/停用/归档筛选、50 条分页；不改变现有 `/nodes` 供渠道选择的响应。列表与详情均不返回凭证摘要、pepper、原始清单路径或任何可还原凭证。硬件与活动统计采用一致数据库快照，未知占用不能显示为可用。

创建和 `rotate_credential` 返回 `{node, secret_available, secret?}`，明文只随首次成功响应返回。仅保存加 pepper 的不可还原 verifier；管理幂等记录仅保存不含 secret 的结果。同键重放返回相同节点/版本且 `secret_available=false`。响应丢失后应查看节点并在空闲时显式轮换，不能重放取回明文。秘密值不进入审计、URL、浏览器存储或日志；关闭凭证对话框、隐藏页面、会话退出及离页时清理内存和 DOM。

## 3. 生命周期与锁

增加 `nodes.revision`（旧行 1，正整数）和 `archived_at`（旧行 NULL）；已归档节点必须停用。CLI 初始登记继续兼容，不回填或增加旧凭证权限。归档保留节点、GPU、尝试、boot、journal 关联与审计，不删除磁盘文件。

改名只更新元数据。停用、轮换和归档必须确认该节点没有未关闭 attempt、任何 GPU occupancy（包括未知）、GPU management_hold 或 running 管理指令；过期租约及空 history 均不是空闲证明。待执行的 pending 指令保留，恢复接入并重新登记后沿既有指令协议处理。归档还要求节点已停用且所有渠道已逻辑删除；归档后不能恢复、改名、启用或轮换。恢复接入不释放任何槽位、不关闭 attempt、不取消任务。

生命周期事务先按稳定顺序对当前 GPU 行取得 NOWAIT 排他锁，再对节点取得 NOWAIT 排他锁并复核 GPU 集合；集合变化返回冲突。任一资源正在领取、上报或执行管理操作时立即返回 node_busy，不等待另一锁序形成环路。闲置检查和变更同事务，不能先在界面判断空闲再无条件写入。管理幂等记录、revision、凭证和审计同事务提交。

停用/轮换使旧 boot 失效，并清空节点当前观测；保留历史 boot 与 GPU 身份。恢复或更换凭证后须重启 Worker，以新 boot 登记并重新验证渠道/Workflow。Worker 注册、心跳、领取和管理指令领取在各自事务内重查启用/boot，不能只依赖路由较早完成的鉴权；注册还在节点锁内复查该请求的凭证标识，拒绝已经鉴权但跨越轮换提交的旧凭证注册。新 boot 的登记即使旧 current_boot_id 为 NULL，也须失效旧渠道/Workflow 验证。

## 4. 图形流程与验证

页面沿用现有控制台样式，突出「登记节点 → Worker 上报 GPU → 添加 ComfyUI 渠道」的接入顺序；展示在线/未登记/过期/停用/归档、GPU 数、活动尝试与占用。动作显示其必要条件，服务器仍会在提交时复查。只读角色没有管理控件；失败保留草稿；同请求响应丢失重试复用幂等键，不自动重复创建或轮换。隐藏/离页取消本次请求与轮询，finally 关闭测试浏览器。

必测：新增/重放/并发唯一与无明文持久化；权限/CSRF/版本冲突；改名不影响活动任务；停用/轮换/归档分别拒绝活动、未知占用、管理保留和 running 指令；归档渠道引用保护；恢复不释放资源；旧凭证和旧 boot 拒绝、新凭证重新登记、GPU ID 不变、旧验证失效；领取与生命周期并发；分页/筛选/搜索；桌面/手机从界面登记节点并用实际 Worker HTTP 协议报告 GPU，再通过渠道表单接入。媒体六类和既有 Admin 行为回归。

迁移前原生备份并验证归档；升级和恢复演练单独留证。有管理 revision/归档事实时拒绝删除新增字段降级。运行证据未产生前不得把本页或整版标记完成；真实 Windows/ComfyUI、六类实际 Workflow、S3、Linux/系统服务和完整首版仍须按原出口验收。

## 5. Windows 接入操作

1. 用 administrator 登录 Admin，打开「Windows 节点」，填写节点名称并登记。首次响应中保存 Worker 凭证；节点此时没有 GPU，也没有在线事实。
2. 将「复制凭证 JSON」得到的内容以 UTF-8 保存为 Windows 私密凭证文件；文件同时包含 `node_id` 和 `token`，不能只写一行裸 token。按既有 Worker 配置填写 `control_origin`、`credential_file`、`state_root` 与 `gpus`。`gpus` 的 `hardware_id` 必须是稳定的物理 GPU 标识，不能把多个 ComfyUI 实例当成不同物理 GPU。现有 Worker 从配置上报硬件；此页面不自动发现硬件或安装服务。
3. 启动原生 Worker 后，回到节点页确认最近上报、GPU 与在线状态。在「Windows 渠道」选择该节点和物理 GPU，填写 Worker 可以访问的 ComfyUI 地址。配置、Workflow 和环境仍须经过 Worker 应用、验证及运行批准，登记本身不批准任何生成能力。
4. 调整名称不要求排空。停用、轮换或归档先排空节点关联渠道并等待所有活动、上传与管理操作完成；未知占用按恢复协议核对。轮换后更新 Windows 凭证文件并重启 Worker；恢复接入后也要以新 boot 登记，并重新验证渠道和 Workflow。
5. 删除不再使用的渠道后，先停用再归档节点。归档保留 GPU 与历史关系，不能反归档。响应丢失时重试同一请求只确认结果，不再显示凭证；未保存首次凭证时，在节点空闲后再次显式轮换。

每页 50 个节点，详情最多返回 100 个 GPU 和 100 个未删除渠道并标记截断。生命周期锁最多检查 101 个 GPU；历史 GPU 超过 100 个时有界拒绝并要求先核对硬件清单，不能遗漏历史占用继续操作。观测超过 15 秒显示过期；隐藏页停止请求并清空一次性凭证，返回页面时重新取快照。节点清单展示占用与活动事实；全局排队和逐渠道候选队列仍从原有总览、任务及渠道详情查看。

## 6. 本地验证与交付记录

当前候选 `rc-20261024.56.3`：280658302 字节，SHA-256 `f1f2b0d1e8b69087f74ad3a85209a66492eaea812d66b22c999400ab5013367c`；139 个 payload 文件、57 个 wheel、22 份运行契约、345 个构建源输入，均与当前源码核对一致。两库为 `d62f7b08a9e1` / 42 张业务表。此次仅补齐图形节点入口、身份与安全生命周期，图片、视频和 ComfyUI Workflow 音频继续共用现有异步任务、渠道、Key 与内容交付体系。

| 验证层 | 本轮结果 | 覆盖与边界 |
| --- | --- | --- |
| PostgreSQL / Python | 完整 1,177 项通过，267.63 秒；新增节点集成 33 项 | 并发登记唯一、凭证无明文持久化/重放、角色/CSRF/版本、空闲与未知占用守卫、NOWAIT 并发回滚、归档引用、旧凭证及注册跨轮换拒绝、GPU 身份保留、历史清单有界拒绝、旧五类与音频回归 |
| 静态与构建 | 123 文件类型检查、Ruff、75 包锁与前端类型/构建通过 | Python 完成后仅修改凭证 JSON 复制的 Vue 页面及原生测试驱动；应用 Python、迁移和运行契约未变，发布源摘要已复核 |
| Admin 桌面/手机 | 21 组通过，3 个上下文关闭 | 零预置节点/GPU；界面登记→实际 Worker HTTP 协议上报合成 GPU→图形渠道创建；改名/轮换/停用/恢复/归档、只读角色、响应丢失同键重试、JSON 凭证、隐藏页超过刷新间隔无轮询；复制接口使用测试替身，避免覆盖用户剪贴板 |
| 原生协议 | 18 组 / 11 个任务通过 | Sub2API、API、Worker 与 Runner 为原生进程；六类任务和五项图片/视频兼容调用完成，音频实际 500 毫秒；ComfyUI/Redis 是明确协议替身。新 boot 后只对六个 test_only 发布包恢复合成确认，不作为物理运行批准 |
| 候选隔离安装 | 14 项通过；基础页面 4 组、节点页 6 组，4 个上下文关闭 | 从离线 wheel 安装、非 editable、显式备份迁移、HTTPS、Worker journal 保留、独立 Runner/GC、四种音频完整解码；安装后的节点登记/JSON 复制/轮换/归档均使用真实候选接口 |
| 一致快照恢复 | 42 表 / 753 行核对一致 | 节点 revision/归档事实及音频字段/计量一致；临时恢复库删除；未覆盖对象字节与主密钥联合恢复 |
| 文档与契约 | 20 份 schema / 218 fixture（65 正、153 负）通过 | 170 个 OpenAPI 方法、131 个路径与 163 项设计端点映射；静态通过不代表真实生成验收 |

Go 客户端 45 顶层/192 子项和账本/Provider 491 项沿用第 55 增量结果，本轮逐项核对维护/生成源码与候选二进制摘要后复用；不把旧测试写成新运行。QC-15 仍为第 51 增量历史证据，20 台物理设备未验收。原生任务确认后的 11 个产物包按一小时保留策略核对；临时进程、数据库、素材、安装目录和私密配置已清理。保留原生 PostgreSQL 与 9 个登记的 API/Runner/GC 进程身份，供 https://127.0.0.1:8443/admin/ 本地预览；无剩余测试进程，全部自建浏览器均在 finally 关闭。

首轮浏览器关闭断言及原生测试替身的 Workflow 确认遗漏均保留原始失败证据。旧候选 56.1/56.2 仅构建、未作为最终安装验收版本；最终 56.3 完成隔离安装。真实 Windows/ComfyUI、六类实际 Workflow/模型与质量吞吐、私有 S3/真实 Redis、公网、Linux/系统服务、生产升级回退和对象/密钥联合恢复仍未验收；14 个工作包保持进行中，完整开发目标未完成。

证据：[完整 Python](./evidence/2026-10-24/worker-nodes-full-python-tests.xml)、[Admin 浏览器](./evidence/2026-10-24/worker-nodes-browser-fixture.json)、[原生协议](./evidence/2026-10-24/worker-nodes-native-protocol.json)、[候选构建](./evidence/2026-10-24/worker-nodes-release-build.json)、[隔离安装](./evidence/2026-10-24/worker-nodes-release-installation.json)、[安装后的节点页面](./evidence/2026-10-24/worker-nodes-release-installed-browser.json)、[迁移](./evidence/2026-10-24/worker-nodes-migrations.json)、[恢复](./evidence/2026-10-24/worker-nodes-restore-rehearsal.json)、[Go 摘要复用](./evidence/2026-10-24/worker-nodes-go-reuse.json)、[失败与替代版本](./evidence/2026-10-24/worker-nodes-failure-notes.json)、[进程](./evidence/2026-10-24/worker-nodes-process-check.json)、[最终核对](./evidence/2026-10-24/worker-nodes-closeout.json)。
