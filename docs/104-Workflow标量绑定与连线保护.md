# 104 · Workflow 标量绑定与连线保护

2026-10-26（Asia/Taipei）。修复 [103 审计](103-当前版本系统检查与目标产物差距.md) 的 F01，归入既有待办 24/46，不增加重复编号。原图目标检查已实现，173 专项、完整原生 CI/跨进程及新候选隔离安装通过；本地 F01 出口已补，真实环境或完整首版验收不提升。

## 1. 问题与固定规则

原始 API 图的最终节点可达检查在参数覆盖之前执行。旧实现仅检查目标存在和预设新值为标量，允许绑定/预设将原有 `[node_id, output_index]` 连线覆盖为字符串或数值，使先前确认可达的 prompt/参考图节点脱离最终输出。

| 编号 | 约束 | 执行边界 |
| --- | --- | --- |
| WSG-01 | 参数绑定只能写入原图中已存在的 JSON 标量输入；null 可作为原占位值 | manifest 验证与 Worker 编译共用同一判定 |
| WSG-02 | 预设也只能覆盖原图标量输入，新值不能是数组/对象 | 所有预设在冻结时检查；执行时检查选中的预设 |
| WSG-03 | 连线、数组字面量和对象字面量均不得成为覆盖目标；未被覆盖的结构化输入保留 | 按原始 workflow_api 判定，不能用已覆盖的副本掩盖原连线 |
| WSG-04 | 旧冻结包在新提交前再次执行目标检查，包括本次未提供值的可选绑定 | 编译早于 journal submit_intent/begin_post 与 POST /prompt |
| WSG-05 | 正常标量覆盖、预设后绑定的优先级、参考顺序/权重和不可变发布包保持 | 六类能力正反回归 |

沿用 `invalid_binding` / `invalid_preset_target` 和 HTTP 422，返回固定说明，不包含原节点值、文件路径或客户输入。公开 DTO/JSON 结构、状态机、数据库与 Worker 协议版本不变；这是落实既有标量输入约束的修复，不引入任意图改写或自动修图。

## 2. 草稿、冻结与恢复行为

渠道导入支持未完成草稿，继续允许查看和编辑。完整来源包若包含非法覆盖，预览必须显示 `publishable=false`；冻结必须拒绝，事务不得新增 release、审计、outbox 或成功幂等记录。管理员修正草稿后才能重新冻结。

Worker 对尚未提交的旧冻结包拒绝编译，不能 POST 生成、发布产物或增加计量。沿用现有准备失败处理和 journal 证据。已经提交/结果未知的尝试仍核对原执行；不能因为新增结构规则而重发、释放 GPU 或改变旧执行事实。

旧冻结 manifest 不自动修改，不改摘要，不自动下架或重算。部署应先排空，更新控制面和 Worker，再对受影响版本创建修正草稿、冻结和重新验证。

## 3. 验证出口

1. 六类合法标量覆盖保持原连线、参考顺序与权重，发布包输入不变。
2. 六类绑定/预设覆盖原连线，在 manifest 验证和编译分别拒绝；数组/对象字面量也拒绝。
3. 实际 PostgreSQL/Admin 冻结失败原子回滚；导入预览正确标记不可发布。
4. 实际 Worker 执行旧包时，在 journal 提交意图和 HTTP /prompt 之前停止；恢复路径不新增自动执行。
5. 适用静态/契约、完整原生 CI、原生协议、来源精确候选与安装验证，按实际范围记录。

真实六类 Workflow、物理 GPU、Redis/S3、历史数据库根因、10～20 台和生产验收继续开放。无 UI 改动时保留原 04·青柠与既有媒体，不把协议样例当作真实生成。


## 4. 已执行的专项

[修复前回归](evidence/2026-10-26/workflow-scalars-before-104.json) 实际执行 41 项，6 个正常场景通过、35 个非法覆盖场景未拒绝；原失败保留。[修复后专项](evidence/2026-10-26/workflow-scalars-focused-final-104.json) 共 173 项通过，零失败/错误/跳过，640 个应用来源运行前后不变。

其中新增 61 项：41 领域/六类正反例、8 PostgreSQL/Admin 冻结与草稿导入、12 实际 Worker 提交边界。HTTP 非法冻结重复两次仍为 422，release/成功幂等/审计/outbox 计数不增加；导入草稿可编辑而不可发布。旧包在 Worker 中没有 /prompt 请求、association/prompt_id 或提交事件，按已证实未提交关闭 journal；没有重复执行、产物发布或额外 GPU 释放。其余既有 Workflow、来源导入、离线包与历史恢复测试也通过。

最初聚合专项因三个测试文件同名导致收集失败，随后修正名称；第二次 HTTP 夹具错误使用默认 host 和非本项目会话路径，修正可信 base_url/Origin 与 /admin/api/v1/session 后通过。原 [收集失败](evidence/2026-10-26/workflow-scalars-focused-104.json)、[HTTP 夹具失败](evidence/2026-10-26/workflow-scalars-focused-corrected-104.json) 和 [类型初检](evidence/2026-10-26/workflow-scalars-types-initial-104.json) 保留，不改旧报告。

实际通过报告已纳入新的 [104 来源索引](evidence/2026-10-26/development-trace-baselines-104.json)，绑定六类能力、API 相关编译实现及 Admin 冻结/导入；CAP 本地 framework 标签、98 完整门禁和 69 稳定待办保持原状态。


## 5. 完整工程与发行结果

[最终验证](evidence/2026-10-26/workflow-scalars-verification-104.json) 核对当前来源、实际运行和候选 ZIP。

| 层级 | 实际结果 | 边界 |
| --- | --- | --- |
| 完整原生 CI | 16 门禁/4074 Python 通过，零失败/错误/跳过 | Python 630.81 秒，原 1200 秒预算；640 来源前后一致，应用提交 d2d53ff |
| 原生跨进程 | 30 场景通过，274 来源一致 | 实际 API/Provider/Worker/PG；ComfyUI/Redis/S3 为夹具；与本次 Python 门禁同窗口运行 |
| 当前候选 | rc-20261026.104.1；169 载荷/58 wheels/67 契约 | 524 来源匹配；ZIP 大小/摘要与每个载荷逐项核对；真实批准清单仍空 |
| 实际 compiler wheel | generation_domain/workflows.py 与当前源码逐字节一致 | 原图标量目标、冻结/编译共用检查进入发行物 |
| 精确隔离安装 | 29 项及桌面/移动浏览器 10 项通过 | HTTPS、备份/迁移、四角色原生入口；两个 context/browser 和临时数据库/目录全部清理 |

[构建核对](evidence/2026-10-26/workflow-scalars-release-build-104.json) 与 [安装记录](evidence/2026-10-26/workflow-scalars-installed-104/native-release-installation.json) 保留实际结果。Provider 本轮无代码改动，构建时经过既有 overlay 与二进制摘要守卫复用第 101 构建；跨进程实际运行和归档内二进制仍核对，不宣称重新编译或重新运行 Go 组件。

103/F01 的本地代码出口已补，24/46 的真实发布包/同步验收没有关闭。69 项（67 首版、2 可选）、0/6 真实 Workflow、98 完整门禁及历史根因均保持开放。没有修改公开路由、DTO、迁移、UI 或原青柠资源；既有媒体不重录，安装验证的截图留在证据目录。原项目原生 PostgreSQL 保留，其余所属测试进程/线程及资源清理；全程无 Docker/WSL/虚拟化。

归档辅助核对脚本曾错误假定来源根目录和顶层 contracts 布局，均在不改应用或归档的情况下按实际布局修正后通过；[根目录初检](evidence/2026-10-26/workflow-scalars-release-probe-initial-104.json) 与 [契约位置初检](evidence/2026-10-26/workflow-scalars-release-probe-contract-path-104.json) 独立保留。正式构建、全量 payload/来源/编译器字节和隔离安装结果见上方通过证据。
