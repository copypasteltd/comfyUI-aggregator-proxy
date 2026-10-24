# 75 · Workflow 历史版本恢复草稿

2026-10-25，第 75 增量。第 74 增量候选 rc-20261025.74.3、424 源输入及最终验证已按当前源码核对，上一轮取得进展。继续落实 Admin 的 Workflow 版本管理与编辑，覆盖原五类及 ComfyUI 音频；公开模型切换/回滚与网关映射确认仍单独验收。

## 行为与契约

新增 `POST /admin/api/v1/workflows/{identifier}/draft-restorations`，需要独立管理会话、`workflows:write`、CSRF、16～128 位 ASCII Idempotency-Key，以及与正文 expected_revision 相同的 If-Match。正文仅包含 expected_revision、source_release_id、source_digest。返回 200 及 workflow_id、source_release_id、source_digest、previous_revision、revision，不返回图、凭证、运行画像批准或客户内容。

历史页明确选择一个冻结版本，显示版本 ID、摘要与将被替换的草稿 revision，并二次确认“确认恢复草稿”。替换整个已保存草稿并将 revision 增加一次；名称、项目与能力保持原登记。恢复不创建新冻结版本，不改动已接收任务、别名、渠道绑定、Worker 文件或运行批准。后续沿用编辑 → 冻结 → 验证/批准 → 公开模型发布流程。不能把恢复草稿标为能力已回滚或已上线。

来源必须属于同一个 Workflow/租户/项目，结构校验标记通过，摘要匹配且按当前发布契约及六类语义重新校验。归档父对象拒绝变更。不存在/跨父版本返回 404；revision 或摘要冲突返回 409；无效来源返回 422。所有错误固定投影，不回显图正文、驱动异常或私有执行信息。

同一管理员/Workflow/原键与同一正文先返回首次事务结果，即使后续草稿已再次改变也不能重复替换。原键配不同正文冲突。失败事务不留下幂等行或成功审计。成功只写一条 `workflow.draft_restored` 审计、已有 AdminMutation 结果及一条只含失效原因的 outbox，记录来源 ID/摘要及 revision，不记录图或旧草稿正文。不新增业务表或迁移。

## 并发与资源边界

先取得幂等记录，再以排他行锁检查 Workflow 当前 revision；只读取父对象元数据，不加载将被替换的草稿。来源版本加共享行锁并在 SQL 中预检 JSONB 文本字节数，超过 10 MiB 返回 413。来源读取后执行 100000 值/64 层预算、当前结构/语义及实际内容摘要核对。与并发 PATCH、冻结、其他恢复保持单次 CAS 顺序；相同请求并发只替换一次。锁等待 1 秒、单语句 5 秒，数据库超时/不可用固定 503，由客户端保留原请求核对，禁止自动换键或提升 revision。

图中的 64 位整数经现有 Python/JSONB 整数路径保留，浏览器查看和导出沿用第 74 增量精确 JSON；浮点仍遵守既有后端 JSON 规范化边界，不宣称任意精度小数。已有验证/批准字段只是被冻结包内的数据，恢复操作不授予新的渠道运行批准。

## 图形状态与验证

沿用现有蓝灰 Admin 历史页，以“冻结版本 → 草稿 revision”显示目标。观察者无恢复入口；有写权限但已有未确认操作时禁用恢复。提交前冻结来源及 revision，提交后关闭历史并显示请求核对。丢响应/超时沿用原正文、键和 If-Match，成功结果只代表原恢复事务，不代表当前草稿仍是该 revision；409 可只读查看当前草稿，再明确关闭原记录重新操作。

关闭历史、隐藏、离页、注销清理尚未确认的本地选择。提交后关闭页面不撤销服务端事务。恢复可见时先核对 session；旧确认面板不能在新会话自动提交。所有自建浏览器/context/page 在 finally 释放，不启动 Docker/WSL/VM 或子代理。

门禁包含冻结 schema/正反 fixture、真实 PG/HTTP 的角色/CSRF/If-Match/幂等/跨父/归档/大小/结构/摘要/锁预算、并发恢复及 PATCH、无任务/命令/占用/绑定/别名副作用；六类及音频整数恢复和原请求重放。桌面/390px 验证跨页历史选择、二次确认、失败/迟到/会话清理与观察者。后端实现有变化，重新执行对应及完整 Python 回归、原生协议、候选构建/离线安装/安装后浏览器。真实 Workflow/Windows/权重/质量、S3/Redis、生产恢复与 10/20 台实机容量保留为独立出口。

## 验收记录

第 75 增量完成 Admin 历史版本恢复草稿：六类 Workflow 共用来源归属/摘要/当前结构语义校验、二次确认、revision CAS、原请求幂等核对与安全审计。恢复替换已保存草稿，不创建发布版本或生成任务；冻结、批准和公开模型发布仍是独立操作。丢响应后重放首次事务结果，即使草稿后来改变也不会再次覆盖；并发修改返回冲突。桌面/390px 的 22 组恢复、30 组表单回归及 13 组历史回归通过，保留精确 64 位整数与未知字段；关闭/隐藏/离页/注销清理未确认选择，观察者只读。新执行完整 Python 2960 项、定向 1404 项、前端 33 项、mypy 151 文件、20 组原生协议/11 合成任务及隔离安装 24 项/安装后浏览器 8 组。未改 Go 客户端/账本按摘要复用既有 45 顶层/192 子测试和 491 项证明，明确不是本轮重跑。候选 rc-20261025.75.1，427 源输入、149 载荷文件、57 wheels、51 运行契约；49 schema/791 fixture（193 正/598 反），186 API 方法/146 路径、179 设计端点。无 DDL，两库 head 0a7d9b4e2136、42 业务表。真实 Windows/六类 Workflow/模型/质量、S3/Redis、生产部署与联合恢复、10/20 台实机容量继续独立验收，完整目标进行中。

候选 SHA-256：`cbd507a9275199e2b6366f43c63762e66459b54a4dd9d7076922eab3a443cdd9`。开发、数据库、编译、测试及安装均使用原生 Windows 进程；未调用 Docker/WSL/VM 或子代理。所有自建浏览器/context/page、临时 API 与测试数据库已清理；保留的九个已登记开发进程用于本地功能预览及后台队列处理，PostgreSQL 作为权威数据库继续运行。真实资源未提供，未连接任何真实 ComfyUI 设备。

完整回归后仅增强已有隔离测试的断言，使用真实登记、可访问 /v1/models 的业务 Key 验证恢复接口仍返回 401；单独复跑该项通过，应用运行源码未改变。完整回归与定向的计数不重复计入这次增强断言。

首次定向夹具尝试修改不可变冻结版本，被数据库约束拒绝；修正为插入独立历史夹具后 1404 项通过。首次恢复浏览器夹具漏设 JSON Content-Type，修正后 22 组通过。失败报告与清理证明保留，未关闭数据库约束或放宽产品校验。可取消的会话读取在 Playwright 内部记录取消诊断；最终页面错误列表为空，两个 context 与自建浏览器均已关闭。

可核对：[检查汇总](./evidence/2026-10-25/workflow-restoration-test-summary.json)、[恢复浏览器](./evidence/2026-10-25/workflow-restoration-browser.json)、[恢复数据库断言及清理](./evidence/2026-10-25/workflow-restoration-browser-fixture.json)、[表单回归](./evidence/2026-10-25/workflow-restoration-form-browser.json)、[历史回归](./evidence/2026-10-25/workflow-restoration-history-browser.json)、[原生协议](./evidence/2026-10-25/workflow-restoration-native-protocol.json)、[Go 证明复用](./evidence/2026-10-25/workflow-restoration-go-reuse.json)、[候选构建](./evidence/2026-10-25/workflow-restoration-release-build.json)、[隔离安装](./evidence/2026-10-25/workflow-restoration-release-installation.json)、[安装后浏览器](./evidence/2026-10-25/workflow-restoration-installed-browser.json)、[两库无漂移](./evidence/2026-10-25/workflow-restoration-alembic-check.json)、[最终核对](./evidence/2026-10-25/workflow-restoration-closeout.json)。

截图：[桌面确认](./evidence/2026-10-25/workflow-restoration-desktop.png)、[390px 确认](./evidence/2026-10-25/workflow-restoration-mobile.png)。
