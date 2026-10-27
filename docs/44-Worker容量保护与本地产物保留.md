# 44 · Worker 容量保护与本地产物保留

2026-10-28 设计补充：[121 · 原始产物移交即清理](121-ComfyUI原始产物移交即清理设计.md) 定义独立的 01 清理器：02 完整校验、持久化且可独立恢复后立即向 ComfyUI 请求删除，不等云端上传。该清理器尚未实现/启用；本文现有的 02 确认后一小时保留与容量门禁继续适用，下文不删除 ComfyUI 输出的描述指当前实现。未来 01 删除权限由 121 单独限定，不能扩大现有 02 到期清理器的路径范围。

本增量落实 [02](./02-系统架构与任务机制.md) 的磁盘/内存/上传积压硬过滤、[01](./01-需求规格与约束.md) 的云端确认后本地产物至少保留 1 小时，以及 [07](./07-Admin可视化管理后台.md) 的磁盘/回传告警。涉及 DEV-03、07～11、15、18、23～27、29～32；本节冻结实现边界并登记实际验证。

## 1. 容量观测与接单

原生 Worker 测量 state_root 及本地白名单 ComfyUI 根目录的可用磁盘空间，取最低值保守限制整台 Worker；通过 Windows 原生 API 读取可用物理内存。不得调用 Docker、WSL、远程 shell、模型安装或 GPU 生成。上报不含盘符、目录、机器登录信息或原始系统错误。

已存在的 ComfyUI input/output/temp 目录也逐个测量，以覆盖其映射到其他卷的配置；探测失败视为不可用。最低可用内存是本地配置预算，不代表已测得具体模型的 RAM 峰值；真实工作流画像和各设备配置仍需实测。

本地新增以下配置初值，均为开发预算而非实机容量承诺：`minimum_free_disk_bytes=5 GiB`、`minimum_available_memory_bytes=1 GiB`、`maximum_spool_bytes=20 GiB`、`generation_reserve_bytes=2 GiB`、`output_retention_seconds=3600`。已存在的 `maximum_uploads` 同时作为待回传积压上限。输出保留时间最少 3600 秒；所有容量值为有界整数，预算必须能容纳单个配置预留。允许管理员在 Worker 本地配置中调整，不能通过远程任意命令修改。

spool 统计只枚举 Worker 自有 inputs/outputs，最多 20000 条、8 层、1 秒，不跟随符号链接/reparse point。所有文件均计入预算，包括未写完、保留期内及未在产物包登记的文件；未知文件不能因过期自动删除。journal 中未确认生成任务按冻结发布包 `output.max_bytes × expected_count` 保守预留空间，无法读取时采用本地配置预留；已收集/待回传产物按实际 spool 加积压数量检查。门禁要求 `free_disk ≥ minimum_free_disk + 2 × (reserved + next_reserve)`、`spool + reserved + next_reserve ≤ maximum_spool`，另检查可用内存和回传数量；磁盘的两份预留覆盖 ComfyUI 输出与 Worker 副本位于同卷的情况。预留与当前磁盘余量同时参与本地门禁，不把文件当前很小当作最终空间足够。

状态为 ready / blocked / unavailable，固定原因包含 disk_space、memory、spool_limit、upload_backlog、observation_unavailable。已领取任务继续租约、核对及回传，容量不足只阻止新生成。新领取后、提交 ComfyUI 前再次检查当前任务的实际冻结预留；未提交时可按既有 preparation_failed 结束，提交后异常仍遵守未知占用及恢复协议。磁盘检查无法锁住其他应用的写入，不能据此宣称绝对不会发生空间不足。

## 2. 心跳与云端过滤

Heartbeat 新增可选 `capacity`，其协议为 native-capacity-v1，带当前 boot 内单调 sequence、样本年龄、固定状态/原因和有界容量数字。云端独立验证字段/派生状态，并在 Node.inventory 的 capacity 命名空间保存当前 boot 和服务器确认的采样时间；不新增 PostgreSQL 表或列。

线形契约见 [worker-capacity.schema.json](./contracts/worker-capacity.schema.json)。JSON Schema 限定形状，DTO 及派单器额外验证数值之间的预算关系、状态与有序原因一致；不能把 schema 形状通过视为预算已通过。无新任务沿用当前实现的 `200 {"lease":null}`。本地仍有未收到回复的 claim request 时继续重放请求，不因本地容量阻塞遗失已提交的领取结果。

缺失、不可用、超过 15 秒、boot 不匹配或乱序覆盖的观测不能用于新派单。旧 Worker 可继续注册、心跳及完成已有任务，但升级到本增量的控制面后，必须上报有效容量才能领取新任务。部署顺序为先排空或确认旧版本，再升级 Worker；不将缺少观测默认为健康。注册新 boot 清除旧观测；缺少 capacity 的心跳清除旧容量许可。

缺少容量时仍保留当前 boot 的最高 sequence；同 boot 重复 register 不清空该序号或刷新旧容量采样时间。更高 sequence 的旧采样也不能覆盖更新样本；重放低序号健康心跳不能恢复许可。新 boot 才重置序号。

云端在新领取事务内复查门禁；被阻止时返回无新 lease，不占用 GPU、额度生成槽或新尝试。原 request_id 已提交的领取结果仍可重放，恢复/续约/完成路径不受容量门禁阻断。该限制不改变 PostgreSQL 任务/GPU 权威源、租约或未知提交规则。

## 3. 云端确认后的本地保留

Worker 收到 complete 成功后在同一 SQLite journal 事务记录首次确认时间和最早删除时间，再标记 cloud_ack；产物文件至少保留配置时长。重复确认和重启不缩短原期限。产物包 JSON 增加保留元数据，不新增 SQLite 表/列，不清空旧 journal。

清理仅处理 journal 明确记录且 cloud_ack 已确认、期限合法并已过期的 Worker 输出文件，逐文件校验相对路径和当前文件系统边界。未确认、未知路径、旧记录缺少确认时间、软链接/reparse、读取失败均不能授权删除。时钟倒退时继续保留。每轮限量清理；失败可重试，不把删除失败记录为完成，也不删除 ComfyUI 自有输出或其他目录。

每 60 秒最多处理 50 个包，按 attempt ID 游标轮转，避免一个异常包阻塞后续到期产物。只接受 `outputs/<att_id>/<数字>.<扩展名>`；先检查整个包再删除，失败重试；只移除空的所属 attempt 目录。本地 state_root 的写权限仍须仅授予 Worker 服务账号与可信管理员；路径检查不提供对恶意本机管理员并发替换目录的隔离边界。ComfyUI 自有 output 目录仍由设备运维管理，不在此清理器的删除权限内。

保留文件占用 spool；达到预算时暂停新生成并继续回传/到期清理。不能为维持接单而提前删除唯一产物或缩短一小时要求。

## 4. Admin 与验证

增加 Worker 磁盘/暂存压力、内存不足、上传积压、容量观测不可用四类运行告警。沿用告警只读权限、筛选/分页/刷新和不可用快照提示；容量详情只展示数值、固定原因及样本时间，不展示本地路径。节点心跳已过期时沿用心跳告警，避免把失联解读成当前磁盘指标。

验证必须覆盖原生 Windows 磁盘/内存只读探测；预留、达到边界、恢复接单；有界枚举与越界拒绝；重启保留和确认前不清理；新旧 boot、时序倒退、缺失/过期观测；真实 PostgreSQL/HTTP 的领取拒绝及原领取重放；Admin 浏览器容量信息和四类筛选，finally 关闭本次 browser/context。完整 Python/前端检查以及已有 Sub2API 原生跨进程链路按变更风险回归。所有记录继续区分合成任务与真实设备、Workflow、Redis、S3 和 10/20 台容量验收。

## 5. 已运行的检查与边界

- 完整 Python 回归 **434 项通过，187.69 秒**；Ruff、uv 锁检查和 **95 个源码文件 mypy** 通过。覆盖当前预算精确边界、双份磁盘预留、多输出大小、磁盘卷探测、原生 RAM、Windows junction、未知文件、过早/失败/重启清理、坏包不阻塞后续清理、领取无副作用、原领取重放/续约、序号/缺失/旧 boot、HTTP 拒绝伪造 ready 及告警安全投影。
- OpenAPI 导出、TypeScript 客户端生成、vue-tsc 和 Vite 构建通过（Vite 13.02 秒）。路径数保持 114，实际方法/路径 144、设计端点匹配 137；本增量只扩展消息/响应字段。沿用锁文件现有 Starlette 测试适配弃用提示和 Vite 大块提示，不把提示计为运行失败。
- [Admin 浏览器报告](./evidence/2026-10-23/admin-worker-capacity-browser.json) **5 组通过、无运行异常**，验证四类告警/数值预算、四种筛选、失败保留最后快照、隐藏/离页停止轮询；使用真实 HTTPS 管理会话和合成告警快照。本次 page/context/browser 均在 finally 关闭。[页面截图](./evidence/2026-10-23/admin-worker-capacity.png) 不代表真实设备容量。
- [原生回归报告](./evidence/2026-10-23/sub2api-native-protocol.json) **15 组、10 个生成任务通过，94.22 秒**；运行 ID `cd1f8fcc2a10`。129 份控制面/Worker/协议源码与契约摘要在运行前后相同。额外打开真实 Worker journal，检查 10 个已确认产物包的保留期限至少 3600 秒、10 个文件仍存在并匹配 bytes/SHA-256；随后只通过测试资源清理移除它们。2 个隔离数据库在 DDL 前备份并验证归档，6 个本次拥有的原生进程根及数据库/媒体/凭证均清理。
- Go Provider/客户端及 66 份维护 overlay 无改动，本轮按摘要沿用第 43 增量回归和编译。PostgreSQL/SQLite 均无新增表/列或迁移；控制面仍为 41 张业务表、迁移 `c4120e20d721`，历史原生恢复证据按未变的 models/迁移源码沿用。

失败未覆盖为成功：[首轮原生报告](./evidence/2026-10-23/worker-capacity-native-first-run.json) 已通过 15 组及 10 份产物检查，但检查器使用 SQLite 连接事务上下文却未 close，Windows 拒绝清理临时目录，故报告保持失败。改用 contextlib.closing，先清理该轮遗留目录/凭证，再完整重跑；[修复与恢复记录](./evidence/2026-10-23/worker-capacity-failure-notes.json) 同时保留两次浏览器选择器修正，所有失败浏览器也在 finally 关闭。

以下均未因此验收：真实 ComfyUI/五类 Workflow、实际 Redis、私有 S3、公网 TLS/代理、真实设备内存/磁盘峰值、10/20 台吞吐、完整告警/访问日志和生产发布。九个已登记 API/Runner/GC 开发进程及原生 PostgreSQL 保留用于继续开发，新增测试资源均已释放。完整首版目标仍进行中。

[本轮最终核验](./evidence/2026-10-23/worker-capacity-closeout.json) 汇总 323 份当前源码/构建产物摘要、测试/原生报告摘要、9 个开发进程身份、数据库/HTTPS 只读检查及临时资源清理结果。[文档校验记录](./文档校验记录.json) 覆盖 46 份文档、9 份 schema、69 个正反例，不能替代上述运行检查。
