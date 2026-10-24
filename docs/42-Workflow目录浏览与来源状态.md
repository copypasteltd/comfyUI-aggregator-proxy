# 42 · Workflow 目录浏览与来源状态

本增量补齐 [07](./07-Admin可视化管理后台.md) 的目录选择交互，承接 [41](./41-Workflow渠道拉取与草稿导入.md) 的读取、预览和原子导入。以下契约已在本地实现；验证边界见末节，真实 ComfyUI 文件和设备仍待提供。

## 1. 来源观测

Worker 在每渠道心跳观测中增加可选 `workflow_export`：`protocol=local-directory-v1`，`status=ready|not_configured|unavailable`。只上报固定状态，不上报导出目录绝对路径、图或节点参数。旧 Worker 不含此字段时保持兼容，显示 unknown。

控制面沿用当前 boot、渠道 desired=applied revision、观测年龄小于 15 秒及观测时间不倒退的检查；持久保存导出观测、时间、revision 和 boot。重注册新 boot 清除观测。来源状态独立于 ComfyUI 队列是否可达；过期、配置变化和节点失联显示 unknown，不能把旧 ready 当作当前可用。

`GET /admin/api/v1/channels/{channel_id}/workflow-exports` 返回来源状态、fresh/unknown、观测时间、渠道 resource revision 和是否支持目录浏览。需 `workflows:write`，不接受业务 Key。配置状态仅是只读提示，不能设置本地根目录。

## 2. 目录操作与分页

- `POST /admin/api/v1/workflow-catalogs`：`channel_id,expected_revision,relative_directory,cursor?`，202 返回持久 operation。`GET /admin/api/v1/workflow-catalogs/{operation_id}`：只允许创建者读取该目录操作的进度/结果。两者需 `workflows:write`，写操作检查 CSRF、Idempotency-Key。普通操作详情不增加文件清单或图数据。
- 复用固定 `pull_workflow` 管理动作，新增 `mode=catalog`；旧文件读取没有 mode 时仍按 file 执行。catalog 必须有当前 boot/revision 的 fresh ready 能力观测，命令绑定该 boot，重启后拒绝旧能力指令。每渠道仍最多一个待执行/执行中的 pull 指令。
- 只浏览 `workflow_export_root` 内的一个目录，空字符串表示根；用户主动进入子目录，不递归扫描。相对目录最多 512 字符、8 级，沿用文件路径的设备名/越界/控制字符/链接拒绝。每目录最多检查 2000 条目录项，超过则整体失败，要求运维划分子目录。
- 每页最多 20 个目录或 JSON 文件，按名称 Unicode 顺序排序。跳过非 JSON 普通文件、链接、junction、reparse、特殊文件和不合法名称，返回跳过数。每页最多读取 20 MiB 源文档，每文件仍限 1 MiB；目录扫描和逐文件检查设 15 秒检查预算，回执最多 64 KiB。超限不返回无标记的残缺目录。
- 条目只含相对名称/路径、类型、大小/修改时间，以及 JSON 验证后的格式、规范摘要、节点数、可用任务类型；无效或过大文件显示固定错误码，不能选择导入。条目不含完整图、提示词、目录绝对路径或原始异常文本。
- 游标使用有界 Base64URL JSON，绑定相对目录、目录元数据摘要及上一页末项。元数据摘要涵盖本目录可列项的名称/类型/大小/mtime_ns 和跳过数；下一页重新计算，不一致则拒绝并要求重载首页。游标不承担授权，不允许越过配置根目录。
- 成功目录回执包含 `success,applied_revision,catalog,catalog_digest`，控制面重新验证严格结构、目录/游标对应关系、排序/唯一性、相对路径及规范摘要后才持久化。Worker journal 重放原目录快照，重复投递不重新枚举变化中的目录。

## 3. 原生目录边界与选择一致性

目录和单文件读取的创建事务统一先锁 Node（`FOR SHARE`），再锁 Channel（`FOR UPDATE`），检查 revision/在途读取并写入管理操作；与心跳/注册的 Node → Channel 顺序一致。新增并发测试通过真实 PG 强制交错，确认原先 Channel → 指令外键 Node KEY SHARE 会与心跳形成 `40P01` 死锁，修正后两种读取均须正常提交。

Windows 枚举时持有目录及各祖先的 `FILE_READ_ATTRIBUTES|FILE_LIST_DIRECTORY` 只读句柄，使用 `OPEN_EXISTING`、`FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT`，只允许 read share，拒绝 write/delete share，防止目录重命名或原地修改 reparse 属性；逐级检查最终路径和 reparse 属性，在本次枚举结束后关闭全部句柄。首轮原生测试发现仅请求属性权限不会阻止重命名，因此必须同时申请目录列举权限，并验证句柄存活期间祖先和当前目录均不能重命名、不能取得目录写入句柄，异常退出后恢复正常访问。依据 [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[目录句柄](https://learn.microsoft.com/en-us/windows/win32/fileio/obtaining-a-handle-to-a-directory) 与 [文件访问权限](https://learn.microsoft.com/en-us/windows/win32/fileio/file-access-rights-constants)。服务账号仍必须具备原本的目录读取权限，不提升系统权限。

文件内容继续使用 41 的普通文件/已打开句柄/硬链接/大小与变化检查。选择条目后，`POST /workflow-pulls` 可增加 `expected_source_digest`；Worker 和控制面都核对读取内容与所选条目一致，不一致返回 `workflow_export_changed`，要求刷新目录重新选择。手动输入路径不含此字段时保留原行为。目录快照本身不能直接作为导入预览来源，必须先完成单文件读取。

## 4. Admin 行为与验证门槛

选择渠道后读取一次来源状态，支持手动刷新；状态显示可用、未配置、不可用、未知/过期。可用时进入目录浏览，显示路径导航、20 条目分页、格式/节点数/文件大小、无效文件原因；选择有效文件填入原读取表单并绑定摘要，再走原预览/确认流程。

只保留当前一页清单；更换渠道清空目录和选择摘要。目录轮询与单文件读取共用 90 秒上限和取消机制；隐藏、关闭、切页后停止请求并释放定时器。状态查询也有独立超时/取消，旧渠道的迟到响应不能覆盖新渠道。关闭页面不取消已经持久化的只读管理操作。

验证须覆盖：原生 Windows 目录句柄与重命名/junction 拒绝、越界目录、分页不重不漏与目录变化拒绝、坏文件/读取预算/数量限额、回执篡改、选中文件变化、journal 重启；真实 PG/HTTP 的权限/创建者/boot/revision/旧 Worker/过期观测/幂等；前端类型/构建和浏览器目录选择、翻页、失败、切渠道、关闭/隐藏后的资源清理。不能用目录浏览测试替代真实 ComfyUI、五类工作流质量、私有 S3 或 10/20 台验收。

## 5. 使用和升级

沿用 [Worker 配置示例](../app/config/worker.workflow-pull.example.json)，导出目录由该 Windows 设备运维配置。先更新控制面，再更新 Worker；没有新增数据库迁移。旧 Worker 仍可使用已有单文件读取，未上报目录协议时 Admin 显示未知。

进入「Workflow 管理」→「从渠道导入」或行内「从渠道更新」，选择来源渠道并刷新来源状态，可用时点击「浏览导出目录」。目录支持进入、路径导航、上一页/下一页与重新加载首页。选择有效 JSON 后点击「读取来源」，再走 41 的预览和确认导入。文件已变化时重新浏览选择；目录操作可暂停等待或检查原操作，关闭窗口不会取消已保存的只读命令。

## 6. 本地验证与边界

- 完整 Python 回归 **374 项通过**，包括 27 项新增目录/来源测试和 6 项新机器契约 fixture；Ruff、uv 锁和 92 个源码文件 mypy 通过。覆盖真实 PG 的旧观测/旧 boot/revision、创建者隔离、同键并发、目录不能作为图来源、云端独立摘要校验和真实 HTTP 权限/CSRF/幂等。
- 原生 Windows 验证 20 条分页、目录变化、2000 项/时间预算、非法/UI/过大/硬链接文件、junction 拒绝、祖先与当前目录重命名保护、目录写句柄保护与异常后释放、Worker journal 重启快照和 ComfyUI 队列不可达时的独立来源观测。
- OpenAPI 114 个路径，实际 144 个方法/路径，137 项设计端点全部匹配；生成类型、vue-tsc、Vite 通过。主包体积提示仍在构建日志中。
- 原生 Edge 的 6 组目录交互与原有 5 组读取/预览/导入回归通过，无页面运行错误；最后关闭本次 context/browser。使用真实回环 HTTPS 登录与生产静态构建，Workflow 响应为明确合成 fixture。
- 无新增 DDL；开发库和固定测试库仍为 `c4120e20d721`、41 张业务表。本次核对 ORM/迁移源码与 41 的一致快照恢复证据未变化，未重做恢复演练。Sub2API 66 份维护/生成源码未变，沿用上轮 474 项 Go 组件及实际服务器编译证据。

首轮保留 22 项通过、2 项失败：属性句柄不能阻止目录重命名的真实边界问题已修正；HTTP fixture 的幂等键短于现有 16 字符约束，已修正测试输入。随后两种读取与心跳的强制并发均重现 `40P01`；统一 Node → Channel 锁序后通过，没有放宽鉴权、路径或数据库限制。见 [失败记录](./evidence/2026-10-23/workflow-catalog-failure-notes.json)、[完整 Python 报告](./evidence/2026-10-23/workflow-catalog-python-tests.xml)、[目录浏览器报告](./evidence/2026-10-23/admin-workflow-catalog-browser.json)、[导入浏览器回归](./evidence/2026-10-23/workflow-catalog-import-regression/admin-workflow-import-browser.json) 和 [最终核验](./evidence/2026-10-23/workflow-catalog-closeout.json)。

完整告警规则和跨服务运行继续开发；真实 ComfyUI/五类 Workflow、私有 S3、10/20 台设备仍未验收。不得把本轮有界目录和协议测试写为完整项目完成。
