# 25 · Sub2API 原生适配实施约定

本页保留初始增量及其历史证据；后续用量、缓存、倍率与套餐的当前组件行为及源码匹配报告见 [40](./40-Sub2API倍率与套餐计费.md)。完整 Provider 与运行验收仍未完成。

2026-10-23，设计基线 v0.4。接续 [网关准入与对账](./20-网关准入与对账实现约定.md)，记录固定上游的代码接入边界。本文和 HTTP 组件均不能作为完整 Provider 已实现的证明。

## 固定输入

采用 Wei-Shaw/sub2api 的 `7da09cab4e875463baa79fc150adde700c5438df`。完整源码归档、关键文件与原生 Go 1.27.3 已核对，摘要保存在 app/integrations/sub2api/upstream-lock.json 与 app/config/native-toolchains.lock.json。stock 编译前逐文件核对解压源码与固定归档，防止将修改过的源码记为原版构建。源码、模块缓存和编译产物在 app/.local；不执行上游容器测试。原生后端编译与 Provider/真实账本联调单独记录。

## Go 元数据客户端

app/integrations/sub2api/controlclient 是无第三方依赖的 Go 模块，负责 admissions 创建/确认/撤回、任务读取/取消、按持久目标网关绑定的请求最小查询/关闭、后台结算事件/确认，以及当前交付策略和有界媒体读取。账本到准入/恢复流程见 [31](./31-Sub2API恢复租约与协调器.md)，有界后台托管与组合根接线见 [33](./33-Sub2API服务托管与生命周期.md)。文生图同步/异步、视频 JSON 与原属内容访问组件已实现，范围与限制见 [34](./34-Sub2API生成兼容与产物访问.md)；图片编辑、媒体上传、发现/共享/列表及完整上游运行继续开发。

客户端只允许固定网关路径。生产使用 HTTPS 和有效证书；HTTP 仅可显式用于回环 IP 测试。服务凭证独立于客户 Key，格式严格检查、对象格式化脱敏；禁止跳转和自动重试，请求及解压后的 JSON 响应有字节与时间上限。错误不携带原响应正文、提示词、Authorization 或传输错误的原文。

外部主体由已认证的上游实例标识、user ID、API Key ID 与可信客户端 IP 构造，映射引用固定为 `<instance>:user:<id>`、`<instance>:key:<id>`；Admin 中必须显式配置对应绑定。调用处不得使用客户自报的外部主体头。结算后台方法不附带客户主体，独立于已停用客户 Key；查询和生成仍逐次附带主体并检查控制面策略。

admissions 的 provider_request_id、external_task_ref、price_version、authorization_ref 由持久化 Provider 意图提供。客户端不生成新的重试 ID；POST 发生网络、响应超限或解析异常时报告“结果可能已生效”，调用方使用既有意图核对。一次 HTTP 调用不代替预留、任务绑定和后台恢复事务。

## 上游接线出口

持久意图与同库账本事务的增量实现遵循 [29](./29-Sub2API持久意图与账本事务约定.md)。新增源码采用独立 additive overlay，固定上游目录不改动；其组件验证不能代替下列完整出口。

当前图片异步 Handler 使用 Redis 任务记录并在接收后启动后台执行；公开模型与视频路由另有平台限制。新接线需要持久化公共任务、逻辑主体和两步准入映射，并保留重启恢复。上游已有 UsageBillingRepository 及 batch image 的预留/捕获/释放接口，可供后续审查复用；尚未证明它们直接满足五类任务、失败回收、配额、缓存失效和审计语义。

Provider 完成必须同时具有上游路由/鉴权接线、持久公共 ID/请求意图、原子货币预留、confirm/abort 恢复、独立下载授权、终态幂等结算/退款及重启测试。控制面不新增客户余额。固定源码、Go HTTP 组件或 stock 后端编译成功只分别证明相应输入与组件。

固定源码的 [usage_billing_repo.go](https://github.com/Wei-Shaw/sub2api/blob/7da09cab4e875463baa79fc150adde700c5438df/backend/internal/repository/usage_billing_repo.go) 中，现有 hold/capture/release 方法分别自行开启和提交数据库事务，分别使用请求指纹去重；资金冻结量保存在用户的合计 frozen_balance。现有 release 会检查 batch hold 的去重记录，但这些方法没有共同锁定本项目的 Provider 意图。因此，直接调用它们再另行更新意图，不能证明崩溃恢复安全，也不能防止 capture 和 release 在两个独立请求 ID 下都被执行。

后续 Provider 必须满足以下接线约束，不能通过给 batch ID 换前缀绕过：

1. 在 Sub2API 自身 PG 事务中持久化公开任务/价格/规范请求摘要、受信主体、稳定请求标识及对应预留；预留和意图状态需共同提交。不得把两次独立提交描述为原子预授权。
2. 终态事件按持久意图行加锁，在同一账本事务中检查唯一终态选择、去重、捕获或归还冻结资金并记录结算引用。捕获与释放互斥；相同事件重放成功，不同指纹冲突必须核对，不能一概吞掉冲突。
3. 保留上游唯一客户余额与 Key/套餐计量。新路径必须明确避开普通 OpenAI handler 再次扣费；用量日志、缓存失效和异步通知若不能同事务完成，使用持久恢复记录，不能通过再次扣款补日志。
4. confirm 网络超时或结果未知时继续使用原意图核对；未确认停止或尚可能执行时不先退款并换 ID 重提。后台事件处理独立于客户 Key 是否仍启用，但仍校验服务身份、事件与意图归属。
5. 实测分别在“预留提交后”“准入接受但回执丢失”“confirm 后”“终态资金提交但 ack 前”停止原生进程；重启后检查公开 ID、冻结/余额、唯一结算和控制面任务一致。尚未完成这些验证时 Provider 标志保持 false。

## 验证

Go 客户端使用原生 httptest 的 HTTPS 服务检查主体头、服务鉴权、结算后台无主体、跳转不转发凭证、响应限额、错误脱敏、取消和无自动重试。Python 的真实 PG/HTTP 测试继续覆盖服务端两步准入及唯一结算。真实 Sub2API 的货币账本和实际媒体交付另行验收。

2026-10-23 早期实际验证：9 项顶层测试与 7 个子用例通过，入口总耗时 55.61 秒，包含原生编译和运行；源码未变化、模块下载关闭。另覆盖准入/任务状态矛盾、非法产物标识及重复结算事件拒绝。详见 [早期 Go 客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-request-fences.json)。这些用例使用本机 HTTPS 协议服务，不代表已连接上游 Provider 或真实客户账本。

账本内核在固定上游完整 298 个迁移、103 张表上通过 27 项顶层测试与 39 个子用例，稳定入口耗时 140.23 秒；实际原生子进程退出覆盖预留、派发与终态提交后的恢复，临时库已删除。详见 [首次稳定账本报告](./evidence/2026-10-23/sub2api-ledger-tests-before-request-fences.json) 与文档 29。该层验证使用生产仓储与真实 PG，但未运行完整 Sub2API 服务或完成上文的远端准入/confirm 恢复出口；完整 Provider 和运行联调继续保持 false。

stock 后端已用固定 Go 1.27.3 原生 Windows 编译通过，最后一轮耗时 290.40 秒；构建前核对完整归档的 4,360 个源文件，构建后确认 go.mod/go.sum 未变化。[原生构建报告](./evidence/2026-10-23/sub2api-native-build.json) 包含二进制摘要。此构建未嵌入前端或启动服务；Provider 和运行联调标志继续为 false。

stock 验证入口为 `app/scripts/verify-sub2api-native.py --timeout-seconds 900`；需先准备锁文件规定的源码归档、固定解压目录与已核对官方摘要的 Go 工具链。脚本不是安装器，不自动升级编译器；到期只停止本次拥有的编译进程树。第一次模块下载或编译耗时可能超过限时，失败结果按原样记录，不能只因输入已准备就设置构建成功标志。

元数据客户端的可复现入口为 `app/scripts/test-sub2api-client.py`，统一检查入口为 `scripts/check.ps1 -Suite Sub2API`。它只运行本项目 controlclient 模块的标准库测试，不运行固定上游的全套测试。账本内核另用 `scripts/check.ps1 -Suite Sub2APILedger`，使用真实上游生产仓储与完整迁移，仅选择本项目新增测试包；两类入口的证据分别记录。使用固定原生编译器、只读 go.mod、禁用模块下载，缓存与临时构建目录均位于 app/.local；整体进程限时，超时只终止本次拥有的进程树。公开证据记录编译器、源码摘要、顶层测试和子用例结果，原始日志留在忽略的本地目录。执行通过前不得把相应入口写成已验证。

上游依赖的大 ZIP 若完整 GET 停滞，可用 `app/scripts/cache-sub2api-large-modules.py` 对固定的两个公开模块进行有界 Range 预取，写入 app/.local/research/go-proxy；不直接修改 Go 模块缓存。分块必须严格核对 Content-Range 和预定字节数，完整文件仅作为下载缓存。后续原生 Go 仍按固定 go.mod/go.sum 和 checksum database 校验模块内容，不能把下载摘要当作可信的模块校验结果。

新增请求关闭与最小查询时，Go 客户端通过 14 项顶层测试与 28 个子用例，耗时 77.63 秒；六项请求身份、持久目标 gateway_id、状态/关闭标识一致性、HTTPS 与回执丢失不重试均已验证，见 [当轮客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-closure-consumption.json)。该历史记录对应关闭消费接入之前的协议组件。

关闭消费增量后，[当轮客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-recovery.json) 为 17 项顶层测试与 32 个子用例，[当轮账本报告](./evidence/2026-10-23/sub2api-ledger-tests-before-recovery.json) 为 37 项顶层测试与 55 个子用例通过。目标网关已随预留持久保存，HTTPS 私有字段关闭证明可在原资金事务中安全释放冻结，完整边界与历史未知目标处置见文档 29。

恢复协调器增量后，[客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-compatibility.json) 为 17 项顶层测试与 32 个子用例，[当轮账本及协调器报告](./evidence/2026-10-23/sub2api-ledger-tests-before-ingress.json) 为 52 项顶层测试与 62 个子用例通过。真实上游迁移链、原生 PG、HTTPS 协议测试服务及子进程退出验证了租约/退避、准入/confirm 回执丢失恢复和结算 ack 重放。该组件由宿主调用有界 Step 和 ReconcileSettlements，不据此声明完整 Provider 或上文四阶段服务验收完成。

原生任务处理器增量后，[当轮账本与接入报告](./evidence/2026-10-23/sub2api-ledger-tests-before-hosting.json) 为 67 项顶层测试与 127 个子用例通过（33.14 秒，其中原生编译 12.02 秒）。已验证真实上游 Key/user/IP/分组鉴权、冻结五类请求解析、原子幂等预留、当前策略查询和持久取消/恢复；301 个迁移、105 张测试表通过重放。新增源码与生成副本的单处鉴权补丁均核对摘要，固定原版目录不变，详见 [32](./32-Sub2API公开任务接入.md) 与 [当轮收尾](./evidence/2026-10-23/sub2api-ingress-closeout-before-hosting.json)。

服务托管增量后，[当轮组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-compatibility.json) 为 77 项顶层测试、152 个子用例通过（113.09 秒，含原生测试程序编译 53.91 秒）。有界后台循环与真实上游鉴权处理器安装在既有服务器；自动恢复/确认/结算、ack 重放、并发启动停止、在途调用取消及多网关故障隔离已验证。生成副本另对 cmd/server/wire_gen.go 应用组合根/清理补丁，[当轮服务器编译](./evidence/2026-10-23/sub2api-provider-native-build-before-compatibility.json) 通过（18.32 秒）；固定原版和模块文件不变，见 [33](./33-Sub2API服务托管与生命周期.md)。该历史记录未覆盖生成兼容与媒体。

生成兼容增量后，[当前客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-assets.json) 25 项顶层测试、84 个子用例，[当前组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-assets.json) 90 项顶层测试、182 个子用例通过；302 项迁移、106 张表通过重放，实际 cmd/server 重新编译通过。251 只登记经授权的原产物归属，247～250 未变。已覆盖同步交付预检、默认 Base64/受信 URL、超时保留、异步图片/视频、原目标 Range/HEAD/删除及权限收紧；共享目录和完整标准化输入等边界见文档 34。仍使用 HTTPS 协议测试服务，完整上游/控制面运行、实际副作用消费者、套餐与价格倍率和真实生成尚未验收，完整 Provider 与运行联调保持 false。

素材/编辑增量后，[当前客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-discovery.json) 为 31 项顶层测试、102 个子用例，[当前组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-discovery.json) 为 106 项顶层测试、209 个子用例通过；303 项迁移、108 张测试表重放通过，实际服务器原生编译通过。252 保存上传意图、原网关与不可变素材归属，编辑复用原任务预留/恢复；247～251 未改。原生 PG/HTTPS 协议和应用检查证据见 [35](./35-Sub2API素材与图片编辑接入.md)，仍不替代完整上游/控制面跨服务运行、真实 ComfyUI/Workflow/S3 及设备验收。

模型发现增量后，当前客户端为 35 项顶层/131 个子用例、组件为 114/224 通过，实际服务器原生编译和 Python 257 项回归通过。模型单项查询、当前授权目录交集、安全能力响应及上游/Codex 共存见 [36](./36-Sub2API模型发现与能力投影.md)；前面的数字与证据为对应增量历史，完整运行仍未验收。

任务/内容游标与显式共享增量见 [37](./37-任务与内容游标及共享访问.md)：客户端 41 项顶层/179 个子用例、组件 125/230 与 Python 277 项通过，实际服务器及前端编译通过；304 项迁移/109 张测试表重放通过。原目标共享读取每次重新授权，不能授予其他创建者的任务、素材或删除权限；55 项维护/生成文件和 196 项应用/契约/产物摘要一致，开发库已安全增加分页索引并完成恢复演练。完整 Provider、跨服务运行和真实设备/S3 仍待验收。

2026-10-23 标准化输入组件：视频 input_reference 支持 JSON 受控素材对象和 multipart 文件；Workflow 选项规范为同一 JobCreate 并保留多参考顺序/权重。131 项 Go 顶层/246 个子用例、293 项 Python 和原生服务器编译通过；元数据客户端复用未变源码的 41/179 报告，完整运行仍未签收，见 [38](./38-标准化参考输入与Workflow选项.md)。

后续增量将原生用量和缓存 effect 接到宿主后台，约束、统计账号、迁移及当前证据见 [39](./39-Sub2API用量与缓存持久消费.md)。原生缓存仓储使用进程内 Redis 协议服务验证，不能替代真实 Redis 或完整服务联调。
