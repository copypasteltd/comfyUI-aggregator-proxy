# 41 · Workflow 渠道拉取与草稿导入

本增量已实现首版 Admin 反向同步中的指定文件读取、来源快照、差异预览及确认导入新草稿/更新现有草稿，并完成本地匹配验证。目录清单浏览和来源配置状态预先展示仍待实现；真实 ComfyUI 导出文件与实机联调仍待提供。

## 1. 资源与读取边界

- Worker 的每个本地渠道可配置可选 `workflow_export_root`。未配置即拒绝拉取；控制面不能设置或改变这个本地目录。不读取多重硬链接文件。
- 固定管理动作 `pull_workflow` 只接受目录内的相对 `.json` 文件名，最多 512 个字符。禁止绝对路径、反斜线、冒号、空段、点段、控制字符、Windows 保留名、尾部点/空格和符号链接、junction、reparse 跳转。读取前核对已打开文件的实际位置及普通文件类型，最多读取 1 MiB + 1 字节以检测超限。
- JSON 必须是 UTF-8 对象；拒绝重复键、非有限数、无法保留数值精度的浮点文本、超过 64 层或 100000 个值的结构。规范化文件和候选草稿各不超过 1 MiB，图节点不超过 2000。不会下载模型、安装节点、执行 shell、扫描任意目录或请求任意远程 URL。
- 支持 `format_version=1.0` 的完整发布包，以及 ComfyUI API 格式图。UI 格式的 `nodes/links` 导出明确拒绝，不做可能丢节点的隐式转换。API 图导入现有草稿只替换 `workflow_api`，保留其他设置；创建草稿只填写版本、任务类型和图，不猜测绑定、输出或依赖。
- 拉取是只读操作，不排空 GPU，不清除已有运行批准。仍检查来源渠道资源 revision 和 desired 配置版本。每渠道最多一个待执行或执行中的拉取。
- Windows 使用已打开句柄查询最终路径，先确认范围再读取；API 行为依据 [Microsoft GetFinalPathNameByHandleW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew)。本地文件权限须限制为受信任运维账号可修改导出目录与父目录。
- Worker 沿用 command journal：已记录回执后，重复投递返回同一快照，不重新读取已变化的文件。成功回执包含 `source_document`、`source_digest`、`export_format`、`source_bytes`；控制面独立重新校验并将快照保存到 PostgreSQL。普通操作详情仅展示后三项安全元数据，绝不返回完整图。
- 固定 Worker command 回执入口的 HTTP 请求上限为 8 MiB，以容纳 1 MiB 文档的 JSON Unicode 转义和封套；来源文档仍独立执行 1 MiB 规范化上限，不能通过较大封套放宽。

## 2. 管理接口与授权

| 接口 | 请求与结果 | 权限 |
| --- | --- | --- |
| `POST /admin/api/v1/workflow-pulls` | `channel_id,expected_revision,relative_path`；202 返回持久 operation | `workflows:write`，CSRF、Idempotency-Key |
| `POST /admin/api/v1/workflow-import-previews` | 来源 operation 与摘要；目标为 create 或 update；201 返回预览 | `workflows:write`，CSRF、Idempotency-Key |
| `GET /admin/api/v1/workflow-import-previews/{preview_id}` | 返回当前管理员持有的预览 | `workflows:write` |
| `POST /admin/api/v1/workflow-import-previews/{preview_id}/commit` | `candidate_digest`；200 返回 Workflow 和是否实际变更 | `workflows:write`，CSRF、Idempotency-Key |

create 目标包含 `name,task_type`；项目与租户由可信来源渠道继承，不接受客户端指定其他项目。update 目标包含 `workflow_id,expected_revision`，必须与来源同项目且未归档。反向拉取使用独立接口，原有 push 的 `workflows:publish` 权限不变。管理员角色是全局权限，预览另外绑定创建者，其他管理员不能读或提交。

来源操作必须成功且只有一个 `pull_workflow` 目标，并由当前管理员创建；渠道未删除，拉取时 desired 版本仍为当前值。预览确认不再次读 Worker 文件，提交的是已预览的摘要；如需最新文件，重新拉取。渠道地址/desired 配置变化、目标 revision 变化、来源摘要或候选摘要不符均拒绝，不自动覆盖。

## 3. 持久预览与原子提交

新增 `workflow_import_previews` 表：创建者、来源 command/channel、来源 desired revision、可选目标 workflow/revision、新建名称/任务类型、完整候选、候选摘要、差异、结构校验结果、到期时间和最终结果。候选只保存在鉴权数据库中，不进入操作摘要、审计、事件或日志。来源记录用外键保留。

预览有效 30 分钟，每管理员最多 100 个尚有效且未提交的预览；创建时以管理员行 `FOR NO KEY UPDATE` 串行检查，此锁兼容幂等记录 actor 外键持有的 `KEY SHARE`，避免两个请求同时升级为 `FOR UPDATE` 而死锁。过期记录仅保留不含正文的状态和摘要，创建新预览时有界清理该管理员至多 100 条过期候选。来源 Worker 回执本身沿既有操作记录留存，仍属敏感管理数据；后续统一保留策略不能破坏历史导入溯源。

提交锁顺序为幂等记录 → 预览 → 来源渠道（共享）→ 目标 Workflow。锁内检查创建者、有效期、来源版本、候选摘要和目标 revision；更新草稿/创建草稿、消费预览、保存结果和审计在同一事务提交。重复提交同一预览返回原结果，不能产生第二个 Workflow；不同摘要仍拒绝。无变化时不增加 Workflow revision。

差异展示新增/删除/变化节点数、各类最多 50 个节点 ID、是否截断和变化的顶层字段。结构校验沿用现有发布门禁，返回固定错误代码及通用提示，不回显用户图内容。缺少元数据允许作为待完善草稿导入；冻结仍须通过全部既有验证。

导入不创建发布版本、不绑定渠道、不更新模型别名、不执行生成、不写运行批准。不可变发布版本和已有任务使用原冻结快照继续工作。

## 4. 图形界面与验收

Workflow 页面提供「从渠道导入」和单行「从渠道更新」。对话框依次展示渠道/相对文件、来源读取状态、目标、差异、发布包完整性和「确认导入草稿」。选择变化清空旧预览；读取和提交期间禁用重复操作。轮询有 90 秒上限，关闭、切页、隐藏页面时停止本次轮询；读取操作保存在操作记录，可重新选择来源 operation 恢复，不把关闭页面当作取消 Worker 命令。

必须验证：真实 PostgreSQL 并发幂等、目标 revision 冲突、跨项目/跨管理员/错用身份拒绝、回执摘要和大小限制、回执原样重放、过期与预览限额、导入后的冻结门禁；Worker 的路径越界、重复键、UI 格式、大小/层级、链接/junction 和原生文件读取；前端类型/构建及浏览器新建、更新冲突与关闭清理。浏览器必须 finally 释放本次创建的 page/context/browser。测试导出均为合成文件，不能算五类真实 Workflow 验收。

实现状态与验证证据在 [开发记录](./17-开发记录与运行说明.md)、[任务清单](./16-开发任务分解与交付清单.md) 及本文件后续记录，不以冻结设计代替完成。

## 5. 使用与验证记录

Worker 配置示例见 [worker.workflow-pull.example.json](../app/config/worker.workflow-pull.example.json)。先把 GPU 标识、渠道 URL、ComfyUI 根目录和导出目录替换为该设备实际配置；目录必须已经存在，且不能是链接/junction。控制面不能远程指定根目录。保存 API 格式 JSON 后，进入 Admin「Workflow 管理」→「从渠道导入」，填写目录内相对路径，读取、预览，再确认导入草稿。行内「从渠道更新」沿用目标项目和草稿 revision；发生冲突须刷新目标后重新预览。失败或暂停的操作可从操作中心复制读取 ID 恢复检查。

本轮验证结果：

- 完整 Python 回归 341 项通过；Ruff、uv 锁检查、88 个源文件 mypy 检查通过。包含真实 PostgreSQL 的同键并发、不同确认键重放、预览限额竞争、审计失败事务回滚、版本/归属/过期拒绝，以及新接口的真实 HTTP 正反例。
- Worker 原生 Windows 测试验证本地读取、junction/硬链接/路径越界拒绝、已打开句柄越界、文件大小/JSON 复杂度/精度限制和 journal 重启后返回原快照。合成节点图不证明 ComfyUI 实机质量。
- OpenAPI 导出 111 个路径，登记 141 个方法/路径并匹配 134 项设计端点；前端生成类型、vue-tsc 和 Vite 构建通过。现有主包体积提示保留在构建日志中。
- 原生 Edge 的 5 组图形交互检查通过：必填校验、来源/目标摘要与确认、新建草稿、恢复来源与更新冲突、未配置目录提示、隐藏/关闭停止轮询；无页面运行错误，finally 关闭本次 context/browser。图形交互使用合成响应，登录与生产静态构建为本地 HTTPS 实际服务。
- 开发库和固定测试库迁移前分别备份并校验归档；升级至 `c4120e20d721`。开发库 41 张业务表的一致快照恢复演练通过，临时库已删除；对象字节和主密钥联合恢复仍未验收。

首次验证保留了两类测试组织问题，以及一次真实的 PostgreSQL 锁升级死锁；原因和修正见 [失败记录](./evidence/2026-10-23/workflow-import-failure-notes.json)。修正后的回归和来源摘要见 [增量核验](./evidence/2026-10-23/workflow-import-closeout.json)、[Python 报告](./evidence/2026-10-23/workflow-import-python-tests.xml)、[浏览器报告](./evidence/2026-10-23/admin-workflow-import-browser.json) 和 [恢复报告](./evidence/2026-10-23/workflow-import-native-restore.json)。

后续目录增量已实现来源配置状态、单目录分页导航和文件摘要绑定选择，见 [42](./42-Workflow目录浏览与来源状态.md)。本节 341 项及 5 组结果保留为本次目录扩展之前的历史验证，不覆盖真实设备验收。
