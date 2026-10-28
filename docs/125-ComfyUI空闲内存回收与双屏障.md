# 125 · ComfyUI 空闲内存回收与双屏障

范围：平台通过现有 ComfyUI HTTP API 回收模型缓存；不操作 GPU 主机、不装节点、不重启进程。取代文档 123 中因单次 `/free` 无执行回执而暂不自动回收的方案。实现和真实验证分别记录，设计本身不构成上线证明。

## 策略与计时

每个规范化 ComfyUI origin 一条持久策略，初始禁用，启用后默认 600 秒；可设置 600～86400 秒。同地址的不同渠道共享策略；互斥保护还递归覆盖共享地址或已登记 GPU 的渠道，包括跨项目渠道。地址别名指向同一进程时应登记同一物理 GPU；无法从 HTTP 证明任意两个地址是否同一进程。

Runner 每 10 秒以上轮询一次启用策略，最多 100 条策略、1000 渠道。观察 `/queue`、`/history?max_items=1` 和 `/system_stats`。业务领取/排队、未归还生成占用、未知占用、其他管理锁、Worker 心跳超过 15 秒或启动变化均阻止回收。轮询间完成的业务可通过持久 Attempt 标记检测；直接进入 ComfyUI 的外部任务通过队列和最近历史变化检测。不能证明无人绕过平台并删除历史，因而需要保留 ComfyUI 的正常历史功能。心跳、查询、下载不算生成活动。

策略首次观察到已知空闲才开始计时；新活动、观测故障、配置更改会重新计时。已经回收且没有新活动时不重复回收。调度器与回收使用同一 PostgreSQL 全局领取锁及所有相关 GPU 行锁；持久 `management_hold` 提交后，Worker 无法领取这些槽位。外部 HTTP 均在数据库事务之外。提交 `/free` 前再次检查队列和业务请求，新请求优先，未发出释放命令时可安全跳过。

## 为什么需要两个 CPU 屏障

ComfyUI 0.33.1/0.42.1 原生 prompt_worker 在完成任务时先写 history，再处理 `/free` 设置的 unload/free 标记，随后进行 Python GC 和 PyTorch 空缓存。因此 `/free` HTTP 200 或第一个屏障 history 成功，均不能单独作为卸载完成证明。

1. 先在 PostgreSQL 持久化 `free_intent`，随后 POST `/free`，请求体 `{"unload_models":true,"free_memory":true}`。
2. 200 确认后持久化 `free_accepted`。
3. 提交固定 `EmptyImage(1×1,batch=1) → PreviewImage` CPU 图，记为屏障一。无模型、客户输入、业务计量或客户产物。
4. 查到屏障一 history 成功且不在队列后，才提交屏障二；两次 POST 前均持久化独立关联标识。
5. 屏障二 history 成功且不在队列，证明串行 prompt_worker 已越过屏障一之后的 flag 处理段。记录前后 `/system_stats`，持久化完成证明，再释放本操作拥有的所有 GPU 管理锁。

该确认适用于验证过的原生串行 prompt_worker。未知版本保持 `unsupported_comfy_version`，不发送释放。修改过的并行执行器需要另行适配。屏障使用核心节点；缺失则跳过。最多产生两张 1×1 临时预览，留在 ComfyUI 的 temp 生命周期中；不删除客户文件或历史。回收操作不是生产任务，不占业务额度，不进入客户内容列表。

`/system_stats` 是整机 RAM/设备 VRAM 观测，不是 ComfyUI 进程 RSS。执行完成与内存回升分别记录；不承诺归零，不释放其他进程持有的内存，不删除模型权重或生成文件。RAM 也可能因系统并发活动降低。

## 崩溃与不确定性

操作、阶段、关联、启动快照、前后指标和管理锁均在 PostgreSQL。执行权有 90 秒租约；接管者不能重新发出未知 `/free`。`free_intent` 崩溃或写响应丢失时进入 `unknown`，保留管理锁，不以超时推断成功。屏障提交响应丢失可以按独立关联查有界队列/历史，查不到则保持未知；禁止重提业务生成。读取失败可重试，最长观察 15 分钟，然后保留未知保护。进程重启/配置变化也要求核对。禁用策略不会清除在途或未知操作的锁。操作日志保留最近 30 天，Admin 只展示最近 20 条。

未知操作需由维护者核对原 ComfyUI 队列及执行证据后修复；首版不提供“强制解锁”或任意 shell 按钮，避免把未知执行变成误放行。

## 管理 API

| 方法与路径 | 权限 | 行为 |
| --- | --- | --- |
| `GET /admin/api/v1/channels/{id}/memory` | `channels:read` | 当前分组、策略、倒计时、观测时间、最近操作和指标 |
| `PATCH /admin/api/v1/channels/{id}/memory` | `channels:write` | `expected_revision`、`enabled`、`idle_seconds`；Admin CSRF、If-Match、Idempotency-Key、审计 |

未配置视图 revision=0，首次 PATCH 的 expected_revision/If-Match 使用 1。随后使用回执 revision。业务 API Key 不能修改策略。渠道配置修改后不会把旧地址的回收成功套到新地址。

## 与多设备统一模型的关系

PublicModel 已支持一个冻结 WorkflowRelease 绑定多个渠道；每个渠道都必须完成相同发布摘要的同步/验证及其 GPU 的真实规格批准。领取使用 PostgreSQL GPU 占用锁，多个 Worker 竞争同一任务时只允许一个成功；忙碌/离线/隔离/回收中的设备不会领取。此为按可用槽位的分配，不保证严格轮询或每台相同任务数量。不同发布摘要不能仅凭名称相同合并。当前线上成对 H3 流程摘要不同，仍需统一图或显式版本适配后才能对外发布一个别名。

相关实现：`app/packages/infrastructure/src/generation_infrastructure/memory_reclamation.py`；运行证据在文档 124 的交付表中补齐。实际验证尚未完成时不得写成已上线。
