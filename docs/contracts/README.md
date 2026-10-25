# 开发设计契约

契约基线 `0.4.0`。这些文件是设计输入和静态验证对象；已登记实现路由的 OpenAPI 在 `app/config/openapi.json`，Admin 类型由其生成，迁移在 `app/migrations`。设计端点与实际登记差异在 implementation-inventory.json，不能从清单推断全部已实现。

| 文件 | 作用与边界 |
| --- | --- |
| [job-create.schema.json](./job-create.schema.json) | 原生 `/v1/jobs` 请求的封闭字段、五类输入和参数形状；具体模型规格仍由发布包限制 |
| [job-retry.schema.json](./job-retry.schema.json) | 同任务内部重试的次数、固定上限、可空下次时间和受控原因；不代替提交/停止证据与事务检查 |
| [dispatch-policy.schema.json](./dispatch-policy.schema.json) | 租户预计服务成本与等待老化的固定调度规则，Admin 只读公开；不代表客户收费、真实 GPU 时间或精确排队位置 |
| [resource-limits.schema.json](./resource-limits.schema.json) | 租户/项目七类限制、显式无限制与有界速率窗口；不等同于剩余额度或客户账本 |
| [key-policy-options-query.schema.json](./key-policy-options-query.schema.json) | Admin 内容授权目录的封闭分页/批量解析请求；creator 始终与项目交集，未知 ID 无范围泄露 |
| [key-policy.schema.json](./key-policy.schema.json) | 多业务 Key 的显式权限/范围/限额/内容策略；不替代服务端归属与授权上限检查 |
| [worker-event.schema.json](./worker-event.schema.json) | Worker 事件 envelope 与按类型 payload；不表示持有租约或事实已经核实 |
| [worker-capacity.schema.json](./worker-capacity.schema.json) | native-capacity-v1 容量心跳形状；DTO 另行验证预算、派生状态与原因，派单器检查 boot/时序/15 秒新鲜度，详见 [44](../44-Worker容量保护与本地产物保留.md) |
| [workflow-catalog-receipt.schema.json](./workflow-catalog-receipt.schema.json) | 目录页的有界元信息与规范摘要；路径/游标/排序、创建者和 boot/revision 仍由应用检查 |
| [workflow-source-receipt.schema.json](./workflow-source-receipt.schema.json) | 成功读取的源图/发布包与摘要、格式、字节数；规范化摘要和复杂度还需领域语义校验 |
| [model-discovery.schema.json](./model-discovery.schema.json) | 发布能力安全投影、版本摘要、规格与输出类型；不返回 Workflow 图或私有节点值 |
| [job-state-machine.json](./job-state-machine.json) | 允许状态边、触发动作和 guard 标识；guard 的运行证据由应用验证 |
| [endpoint-catalog.json](./endpoint-catalog.json) | 公共/管理/Worker/网关端点、身份、权限、幂等和 revision 条件清单；不代替完整请求/响应 OpenAPI |
| [worker-node-command.schema.json](./worker-node-command.schema.json) | Windows 节点创建、改名与固定生命周期动作；名称拒绝空白/控制字符，停用/轮换/归档的空闲及引用条件仍由数据库事务核验，见 [56](../56-Windows节点登记与凭证管理.md) |
| [admin-queue-query.schema.json](./admin-queue-query.schema.json) | Admin 多维队列查询；ID/状态/长度/分页/时间数值边界，时间先后关系由 DTO 语义校验 |
| [release-manifest.schema.json](./release-manifest.schema.json) | Windows CPython 3.13 候选清单、完整文件摘要、DB/Worker/网关版本和未签收边界；独立安装器另查 Windows 安全路径与字段对应的实际产物，见 [49](../49-原生发布包与离线安装验证.md) |
| [recovery-checkpoint.schema.json](./recovery-checkpoint.schema.json) | 独立恢复文档形状、行/对象计数与密钥 canary；签名、表名排序/唯一和计数交集由 DTO/恢复器核验，真实恢复另行验收，见 [60](../60-数据库对象与密钥联合恢复校验.md) |
| [admin-audit-query.schema.json](./admin-audit-query.schema.json) | Admin 审计封闭分页、精确交集和半开时间查询；时间先后关系由 DTO 校验，安全投影与权限另实测，见 [61](../61-管理审计检索与详情.md) |
| [admin-session.schema.json](./admin-session.schema.json) | 显式交互固定请求和 Unix 秒寿命响应；鉴权精度、并发/退出和浏览器时序另实测，见 [62](../62-管理会话空闲超时与交互续期.md) |
| [admin-audit-retention.schema.json](./admin-audit-retention.schema.json) | 当前配置与最近已提交清理批次的封闭只读响应；启用关系、权限、原子清理和图形状态另实测，见 [63](../63-管理审计保留与有界清理.md) |
| [development-traceability.json](./development-traceability.json) | 全部73项需求、32条规则、18组QC和15个工作包的追踪；完整出口仍未验收，当前进度见文档17 |
| [fixtures.json](./fixtures.json) | 正反例内联集合，包含图片、视频、音频任务、策略和 Worker 事件；为合成示例，无实际 Key 或媒体 |
| [../tools/verify_docs.py](../tools/verify_docs.py) | 离线验证 Markdown 链接、JSON/YAML示例、JSON Schema/fixture、状态/端点/追踪一致性 |

在项目根运行：

```powershell
python -X utf8 docs/tools/verify_docs.py
```

默认只检查并打印结果；`--write-report` 更新 [文档校验记录](../文档校验记录.json)。工具使用已有原生 Python 的 jsonschema 和 PyYAML；依赖缺失会明确失败，不自动安装、访问网络或启动服务。它不执行文档中的 shell 代码，也不接触 GPU、数据库、浏览器和真实网关。

JSON Schema 使用 draft 2020-12，引用限定本文件 `#/$defs`，禁止在线解析。所有正反例必须出现预期校验结果，新增字段/状态/路由需同步文字说明与运行测试。验证结构与追踪不等于验证分布式正确性。

执行边界和协议正文见 [09](../09-开发总纲与强制约束.md)、[11](../11-数据字典与数据库事务.md)、[12](../12-接口契约与Worker协议.md)、[14](../14-测试策略与质量门禁.md)。

集合游标契约见 [collection-page.schema.json](./collection-page.schema.json) 与 [37](../37-任务与内容游标及共享访问.md)；公开/网关响应、分页边界与共享路由须同步实现。

以下分增量保留的数字和当时状态是历史快照；最新契约汇总见文末第 84 增量。早期段落中的“待实现”不覆盖后续实现记录，当前开发与验收状态见文档 16、17 和 78。

模型文件回执契约见 [model-file-receipt.schema.json](./model-file-receipt.schema.json) 和 [45](../45-渠道模型清单与受控登记.md)：当前 15 份 schema、128 份 fixture（41 正例、87 反例）。JSON Schema 验证形状；目录摘要、路径与类别对应、当前 boot/revision/时效及登记事务仍由应用语义校验。OpenAPI 当前登记 155 个方法、122 个路径，匹配 148 项设计端点，不能替代整体验收。

规格隔离写入形状见 [spec-isolation.schema.json](./spec-isolation.schema.json) 与 [46](../46-生成失败归因与规格隔离.md)。服务端从任务冻结执行派生规格/归属，恢复的 GPU/发布/规格、boot/revision、时间和成功产物证明不由 Schema 代替。

## 第 47 增量冻结

当前 12 份 schema/100 份 fixture；分页选项契约由 endpoint-catalog 与应用 OpenAPI 共同描述。队列筛选、计数、时间和权限边界见 [47](../47-队列筛选与实时负载验收.md)。

## 第 48 增量冻结

内容查询见 [admin-content-query.schema.json](./admin-content-query.schema.json)，标签写入见 [content-metadata.schema.json](./content-metadata.schema.json) 及 [48](../48-内容检索标签与受控预览.md)。当前 14 份 schema/116 份 fixture、22 项管理权限；对外业务 scope 不增加 content:manage。JSON Schema 验证结构，NFC/首尾空白、时间顺序、授权、revision/幂等及对象可用性另外由 DTO/事务/HTTP 验证。16 份同步契约含 schema、端点目录和状态机；不得只修改运行时副本。

第 50 增量加入内部重试元数据，当前 16 份 schema、140 份 fixture（45 正例、95 反例），18 份同步运行契约。必需的可空字段在 API 序列化时仍需保留，不能因排除空值而偏离机器契约。详见 [50](../50-有证据内部重试与计量保全.md)。


第 51 增量加入固定 dispatch_policy，当前 17 份 schema、150 份 fixture（46 正例、104 反例）、19 份同步运行契约。9 个策略字段在扩展对象中全部必需；QueueView 整个扩展仍可缺省，保持旧快照可读。字段公开等份额与老化规则，不公开其他租户历史，不接受客户/Worker 自报成本与优先级。详见 [51](../51-租户公平调度与等待老化.md)。

第 52 增量增加 [resource-limits.schema.json](./resource-limits.schema.json)，当前 18 份 schema、167 份 fixture（51 正例、116 反例）、20 份同步运行契约、24 项管理权限。新写入限制容量，旧记录读取继续兼容；NFC 名称、版本/幂等、父级共享速率及分页归属由 DTO、事务和真实 HTTP 验证。详见 [52](../52-租户项目与分层配额管理.md)。


第 53 增量当前为 19 份 schema、194 份 fixture（57 正例、137 反例）、21 份同步运行契约；目录查询固定为只读 POST，CSRF/keys:manage 不豁免。完整规则与本地验证见 [53](../53-Key内容授权目录与分页选择.md)。

第 56 增量同步 Windows 节点管理命令、nodes:manage 与五项管理路由。当前 20 份 schema、218 份 fixture（65 正例、153 反例），170 个 OpenAPI 方法 / 131 个路径映射 163 项设计端点；22 份运行契约包含端点和状态机文件。形状校验之外，凭证/boot、事务锁、空闲事实和归档引用均由真实 PostgreSQL 集成测试及原生协议验证，详见 [56](../56-Windows节点登记与凭证管理.md)。


第 58 增量冻结 [统计查询](./admin-statistics-query.schema.json)、[统计响应](./admin-statistics.schema.json) 与 [阶段时长](./admin-job-timing.schema.json)。当前 23 份 schema、240 份 fixture（71 正例、169 反例）、25 份同步运行契约；171 个 OpenAPI 方法/132 个路径映射全部 164 项设计端点。JSON Schema 校验形状，DTO 另核对总数、分类、窗口连续性、成功率分母及分位数样本；事务/时间事实/权限与性能由 PG、HTTP 和浏览器分别验证。固定 Worker 协议仍为 0.4.0，详见 [58](../58-运行趋势与任务阶段耗时.md)。


第 60 增量新增 [recovery-checkpoint.schema.json](./recovery-checkpoint.schema.json)，应用契约仍为 0.5.0、Worker 仍为 0.4.0。当前 24 份 schema、252 个 fixture（73 正例、179 反例）、26 份同步运行契约；API 方法/路径和设计端点不变。12 个新增形状 fixture 同时经 JSON Schema 和严格 DTO 校验；签名、完整行、实际对象和密钥属于运行验收，不能从合成 fixture 推断恢复有效，详见 [60](../60-数据库对象与密钥联合恢复校验.md)。


第 61 增量新增 admin-audit-query 及 16 个正反 fixture。当前 25 份 schema、268 个 fixture（75 正/193 反）、27 份同步运行契约；应用 0.5.0、Worker 0.4.0 和 171 方法/132 路径、164 设计端点不变。查询数值的 HTTP 字符串转换、时间顺序、读权限、同事务快照和安全详情须运行验证，见 [61](../61-管理审计检索与详情.md)。

## 第 62 增量：管理会话空闲与交互

第 62 增量新增 admin-session 及 16 个 fixture。当前 26 份 schema、284 个 fixture（78 正/206 反）、28 份运行契约；172 方法/133 路径、165 设计端点。应用 0.5.0、Worker 0.4.0；契约静态校验不表示服务端时序或浏览器行为已验收，运行记录见 [62](../62-管理会话空闲超时与交互续期.md)。

## 第 63 增量：管理审计保留

第 63 增量新增 admin-audit-retention 与 16 个 fixture。当前 27 份 schema、300 个 fixture（82 正/218 反）、29 份运行契约；173 API 方法/134 路径、166 设计端点。应用 0.5.0、Worker 0.4.0 和迁移 head 0a7d9b4e2136 不变。只读形状不构成实际清理、恢复、浏览器或生产保留策略验收，见 [63](../63-管理审计保留与有界清理.md)。


## 第 64 增量：渠道查询

新增 admin-channel-query 与 24 个 fixture；当前 28 份 schema、324 个 fixture（88 正/236 反）、30 份运行契约。应用 0.5.0、Worker 0.4.0、173 方法/134 路径和 166 设计端点保持不变；实际检索/权限/取消行为仍需分层验证，见 [64](../64-渠道检索与详情直达.md)。


第 65 增量新增 admin-channel-deletion.schema.json 与 30 个 fixture（10 正/20 反），六类阻断 ID、数量边界及状态一致性冻结；总计 29 份 schema、354 个 fixture（98 正/256 反）、31 份运行契约。端点目录增加 channels:write 的只读预检，接口与真实实现分别验证。详见 [65](../65-渠道删除约束与并发保护.md)。


第 66 增量新增 worker-command-reconciliation.schema.json 与 28 个 fixture（7 正/21 反）；请求观测、服务端恢复保留字段及确认状态冻结。详见 [66](../66-管理指令持久结果与恢复核对.md)。


第 67 增量新增 [Workflow 重试请求契约](./workflow-sync-retry.schema.json)；专用重试只处理有失败证据的目标，当前版本、来源终态与唯一后继由 PG 事务验证，见 [67](../67-Workflow失败目标重试与操作关联.md)。


第 68 增量新增 [任务批量取消请求](./admin-job-cancellations.schema.json)，冻结 1～50 个唯一明确任务及固定原因，16 个 fixture（2 正/14 反）。累计 32 份 schema/414 个 fixture、34 份运行契约；意图状态边同步到运行状态图，逐项结果、配额和未知 GPU 约束由真实 PG/进程验证，见 [68](../68-任务批量取消与持久逐项结果.md)。


第 69 增量新增 [告警筛选](./admin-alert-query.schema.json) 与 [已确认失败上下文](./admin-alert-failure.schema.json)，40 个 fixture（12 正/28 反）；累计 34 份 schema/454 个 fixture（121 正/333 反）、36 份运行契约。GET 路径不变，15 类告警及只读快照/角色/三类媒体/预算由实际 PG 与图形验证，见 [69](../69-已确认生成失败告警与定位筛选.md)。


第 70 增量新增模型目录查询、映射选项查询与映射选项响应三份 schema，65 fixture（18 正/47 反）；累计 37 schema/519 fixture（139 正/380 反）、39 运行契约。实际项目范围、分页、六类冻结规格、只读预算与图形写入核对见 [70](../70-模型目录分页与冻结规格选择.md)。


第 71 增量新增 Key 生成资源目录请求/响应两份 schema 和 68 fixture（21 正/47 反）；累计 39 schema/587 fixture（160 正/427 反）、41 运行契约。安全项目目录与图形原请求核对见 [71](../71-Key生成资源授权选择与请求核对.md)。


第 72 增量新增 Workflow 目录/同步选项四份 schema 与 85 fixture（18 正/67 反），累计 43 schema/672 fixture（178 正/494 反）、45 运行契约。见 [72](../72-Workflow检索与分页同步选择.md)。


第 73 增量新增发布历史/版本差异四份 schema、75 fixture（11 正/64 反），累计 47 schema/747 fixture（189 正/558 反）、49 运行契约。见 [73](../73-Workflow发布历史与版本差异.md)。


第 74 增量仅实现既有 Workflow 参数/元数据图形配置与前端数值保护，不新增 API、Schema、DDL 或发布格式；49 运行契约、47 schema/747 fixture 不变。见 [74](../74-Workflow参数与元数据表单.md)。


第 75 增量新增 admin-workflow-draft-restore.schema.json / admin-workflow-draft-restored.schema.json，供同 Workflow 冻结历史恢复草稿的请求与原事务结果使用。请求包含 expected_revision/source_release_id/source_digest；结果包含 workflow_id/source_release_id/source_digest/previous_revision/revision，revision 恰好增加一次。49 schema、791 fixture（193 正/598 反），51 运行契约，179 设计端点；不新增 DDL。见 [75](../75-Workflow历史版本恢复草稿.md)。


第 76 增量新增 admin-workflow-lifecycle.schema.json / admin-workflow-lifecycle-result.schema.json，供归档/解除归档的严格布尔、CAS 与原事务结果使用；结果 revision 恰好增加一次。51 schema、833 fixture（197 正/636 反），53 运行契约、180 设计端点，无 DDL，见 [76](../76-Workflow归档与恢复管理.md)。

第 79 增量新增网关目录发布、重放、准入 stamp、服务回执及完整配置五份 schema，增加 22 个 fixture（8 正/14 反）；累计 56 schema、855 fixture（205 正/650 反），58 运行契约、187 设计端点。期望与确认版本独立，首次确认前兼容 legacy；服务配置端点不接收客户委托身份。79 阶段控制面持久化与版本校验已有实际 PostgreSQL 证据；当时待实现的 Provider 自动消费者和 Admin 映射 UI 已分别由 [80](../80-Provider目录消费与版本快照.md) 和 [81](../81-Admin网关目录映射与发布核对.md) 补齐，保留 [79](../79-网关模型目录持久发布协议.md) 的设计历史。

## 第 84 增量：监控配置与指标目录

新增 [monitoring-configuration.schema.json](./monitoring-configuration.schema.json) 和 [monitoring-metrics.json](./monitoring-metrics.json)，分别冻结封闭安全响应/严格阈值与固定指标/标签/预算。37 个新增 fixture（3 正/34 反）与严格 DTO 对照；累计 **57 schema、902 fixture（210 正/692 反）、60 运行契约、190 设计端点、156 OpenAPI 路径/197 方法**。监控两个 GET 入口使用独立只读身份，默认关闭，原四类身份不可代用；来源、只读事务、流式观测和失效语义须运行验证，不能从 JSON Schema 推定通过。应用契约继续 0.5.0、Worker 协议继续 0.4.0，16 状态/64 转换/24 条件不变；详见 [84](../84-监控指标与脱敏请求关联.md)。
