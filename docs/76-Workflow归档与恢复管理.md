# 76 · Workflow 归档与恢复管理

2026-10-25，第 76 增量。第 75 增量候选 rc-20261025.75.1 的 427 源输入与当前文件逐项一致，上一目标轮次分类为 progress。本轮落实 ADMIN-04、WP-07/08/10/14 的 Workflow 生命周期；应用仍在 app，文档及契约在 docs，使用原生进程，不调用 Docker/WSL/VM、子代理或未提供的真实设备。

## 行为与事务

新增 POST /admin/api/v1/workflows/{identifier}/lifecycle，要求管理会话、CSRF、workflows:write、16～128 ASCII Idempotency-Key，If-Match 与 expected_revision 一致。请求为 expected_revision（1～9007199254740990）和 archived（严格布尔）；结果为 workflow_id、archived、previous_revision、revision，revision 恰好增加一次。机器契约先冻结为 admin-workflow-lifecycle / admin-workflow-lifecycle-result，再同步 DTO、运行契约、OpenAPI 与前端类型；不新增表或迁移。

归档前以 Workflow 排他行锁核对 revision 和状态，拒绝仍被已发布 PublicModel 引用或仍有非终态 apply_workflow_release 指令的对象，返回固定 409，提示先下架模型、核对同步。已下架模型、历史绑定、冻结版本和审计保留；归档不删除 Windows 文件、权重或云端内容，不取消、改版或释放已接收任务的 GPU/配额，也不生成管理指令。解除归档只恢复可维护状态，不自动发布别名、同步、运行批准或生成任务。同状态请求用新键提交返回 409；原键同正文先返回原事务结果，即使后来已解除归档，原键不同正文冲突。

事务先登记幂等、再锁 Workflow，父对象只读取元数据，不读取 draft/manifest。归档阻塞检查只查 EXISTS/ID，不加载图、指令 payload 正文或所有映射。成功只修改 archived/revision、AdminMutation、一次 workflow.archived / workflow.unarchived 审计及既有失效 outbox。锁等待 1 秒、语句 5 秒；数据库异常固定 503，保留原请求核对，失败回滚。不得把驱动异常、路径或图正文投影到客户端。

## 归档门禁与并发

草稿 PATCH、冻结、历史恢复草稿、渠道导入提交在锁内拒绝归档对象。新模型创建/改版/发布、新同步/绑定/重试、批准与任务准入读取 Workflow 并持有 FOR SHARE 至本事务结束，检查未归档；与归档 FOR UPDATE 串行。模型下架、只读历史、草稿查看、版本导出和已接收任务的执行/核对/回传继续可用。原幂等结果优先回放，不能因归档破坏已有请求身份。归档不会新增绑定或改变旧绑定 digest/批准，旧任务仍用冻结版本。

新发布先取得共享锁并提交后，归档会看到已发布引用而拒绝；归档先提交后，新发布/同步/验证/准入拒绝。归档期间不锁 PublicModel/Channel，避免与模型、渠道事务建立反向锁依赖。新模型选择器的浏览只列有效 Workflow 的发布版本；精确解析仍可读取历史引用，不能绕过写入门禁。同步选择器对归档父对象返回 409；公开模型发现过滤归档父对象。

## Admin 与验证

沿用当前蓝灰界面。行内提供归档/解除归档，明确显示名称、ID、当前 revision 与目标状态，并二次确认；观察者只读。归档行保留查看草稿、发布历史和导出；编辑、冻结、从渠道更新与同步禁用。动作提交冻结正文/键/revision；未知响应仅允许用原请求核对，不自动换键或 revision。只渲染一个核对区域。确认框关闭、隐藏、离页或注销即清理；恢复可见先核对 session，旧选择不能在新会话提交。自建浏览器、context、page 均在 finally 释放。

验证包括严格 schema/DTO、真实 PostgreSQL/HTTP 角色/CSRF/If-Match/幂等、归档阻塞与解锁、同键/异键并发、与 PATCH/模型发布/同步/验证准入竞争、锁预算、图字段不读取、已接收任务及绑定/配额/指令/账本不变；桌面与 390px 的生命周期/错误恢复/会话/观察者，以及历史恢复和参数表单回归。后端变化后新执行完整 Python、原生协议和候选离线安装。真实 Windows/六类 Workflow/模型质量、S3/Redis、生产系统服务/联合恢复、10/20 台实机容量仍独立验收，不标为完成。

## 验收记录

第 76 增量完成 Workflow 归档/解除归档图形管理，覆盖原五类及 ComfyUI 音频。二次确认、revision CAS、原请求幂等、元数据行锁、公开模型/待完成同步阻塞及安全审计已实现；新模型发布、同步/绑定/重试、批准、验证与任务准入持有 Workflow 共享锁并检查未归档。归档对象仍可查看草稿/历史/导出，已接收任务保留原版本并可继续领取，不新增任务或同步指令。未知响应重放首次事务结果，后来反向变更不会被重复覆盖；观察者只读，关闭/隐藏/离页/注销清理确认选择，恢复可见先核对会话。新执行 Python 3087 项、定向 1498 项、前端 33 项、mypy 153 文件；26 组归档图形检查、30 组表单/13 组历史/22 组恢复回归、20 组原生协议/11 合成任务、隔离安装 24 项及安装后浏览器 8 组通过。未改 Go 代码及二进制按摘要复用 45 顶层/192 子测试与 491 项账本证明，非本轮重跑。候选 rc-20261025.76.3，431 源输入、149 载荷、57 wheels、53 运行契约；51 schema/833 fixture（197 正/636 反），187 API 方法/147 路径、180 设计端点。无 DDL，两库 head 0a7d9b4e2136、42 业务表。真实 Windows/六类 Workflow/模型质量、S3/Redis、生产部署/系统服务与联合恢复、10/20 台实机容量仍独立验收，完整目标进行中。

候选 SHA-256：`613684e8dcd809981410292d96357f2a23e32880f81c310ee46b63ef35680bcb`。应用/脚本/构建均位于 app，文档/契约/证据位于 docs。使用原生进程，未调用虚拟化、子代理或真实 ComfyUI 设备。自建浏览器/context/page、临时服务、数据库与私密夹具全部清理；仅保留九个已登记的开发 Admin/API、Runner、独立文件 GC 进程及 PostgreSQL，供本地预览与持续开发。

初始新测试夹具误用不存在的登录路径、普通业务主体发起内部验证及仅一条渠道运行证据，修正为真实 session 路径、内部验证身份与全部映射渠道后通过；未放宽产品鉴权或运行批准。首次浏览器断言将独立错误提示误定位到内层请求区域，修正定位后 26 组通过。前端首次构建在 Windows Node 的结束阶段触发 UV_HANDLE_CLOSING 原生断言，保留非零失败；再次完成类型生成、33 项单元与生产构建，退出为 0。完整 Python/图形回归后，后端和前端实现未变；端点清单 inventory_revision 标签修正另经契约与原生协议重验。服务收尾发现 Windows PID 复用让一个早已存在的系统程序被误登记为子进程；只纠正记录，未停止该程序。dev-start 增加父进程 PID/可执行文件/创建时间复核，排除早于父进程的候选子进程，失败回收也逐个核对身份。六项原生进程身份检查、实际服务重启与无关进程保留通过。此后唯一应用源变化为 scripts/dev-start.ps1，原生协议输入摘要未变；最终候选另行构建和隔离安装。rc-20261025.76.1、rc-20261025.76.2 已由最终候选替代并保留。所有最终证据与失败/清理记录并存，失败不计作通过。

可核对：[检查汇总](./evidence/2026-10-25/workflow-lifecycle-test-summary.json)、[归档浏览器](./evidence/2026-10-25/workflow-lifecycle-browser.json)、[数据库断言及清理](./evidence/2026-10-25/workflow-lifecycle-browser-fixture.json)、[表单回归](./evidence/2026-10-25/workflow-lifecycle-form-browser.json)、[历史回归](./evidence/2026-10-25/workflow-lifecycle-history-browser.json)、[恢复回归](./evidence/2026-10-25/workflow-lifecycle-restoration-browser.json)、[原生协议](./evidence/2026-10-25/workflow-lifecycle-native-protocol.json)、[Go 证明复用](./evidence/2026-10-25/workflow-lifecycle-go-reuse.json)、[候选构建](./evidence/2026-10-25/workflow-lifecycle-release-build.json)、[隔离安装](./evidence/2026-10-25/workflow-lifecycle-release-installation.json)、[安装后浏览器](./evidence/2026-10-25/workflow-lifecycle-installed-browser.json)、[原生进程身份](./evidence/2026-10-25/workflow-lifecycle-pid-guard.json)、[启动脚本源差异](./evidence/2026-10-25/workflow-lifecycle-process-ownership-source-proof.json)、[两库无漂移](./evidence/2026-10-25/workflow-lifecycle-alembic-check.json)、[最终核对](./evidence/2026-10-25/workflow-lifecycle-closeout.json)。

截图：[桌面](./evidence/2026-10-25/workflow-lifecycle-desktop.png)、[390px](./evidence/2026-10-25/workflow-lifecycle-mobile.png)。
