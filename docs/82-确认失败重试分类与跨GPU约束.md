# 82 · 确认失败重试分类与跨 GPU 约束

日期：2026-10-25（Asia/Taipei）。对应待办 05、DEV-06～13/20/23～32；在 [50](./50-有证据内部重试与计量保全.md) 的准备传输重试上扩充，真实故障验收 60 保持独立。本文先冻结实现约束，运行结果在验证后追加。

## 分类与预算

| 阶段/原因 | 固定处理 | 新生成预算与路由 |
| --- | --- | --- |
| journal accepted，明确从未提交；准备连接/超时/受控临时 HTTP 错误 | preparation_transport_failure，内部退避 | 共享最多 2 次重试，5/15 秒；仅原冻结渠道，避开本任务此前 OOM 的物理 GPU |
| 当前 prompt 的白名单 OOM，history error 且 queue 已无该 prompt | comfy_oom，有完整关联及停止证明后可内部退避 | 每任务最多 1 次 OOM 重试，仍占共享 2 次预算；必须有原冻结路由内另一已批准物理 GPU，规格/seed/Workflow 不变 |
| 无合格替代 GPU、OOM 已重试、预算耗尽、deadline 不足 | 明确停止则失败并唯一结算 | 0；不降尺寸、精度、质量或换 Workflow |
| 配置、鉴权、输入摘要、路径、依赖、模型、一般准备错误 | 明确未执行/已停止则失败，进入既有诊断/隔离流程 | 0；禁止把旧通用 preparation_failed 当临时错误 |
| 普通执行错误、非法产物、媒体/摘要/规格拒绝 | 明确停止则失败；原规格隔离规则保留 | 0；随机重跑不能修复契约错误 |
| 提交结果未知、租约/心跳丢失、执行超时、无法确认停止 | 核对原执行，保留未知占用 | 0；不根据空 history、HTTP 200 或断连释放 GPU |
| 进度/WebSocket/读取断连 | 重连/查询同一 prompt；必要时进入核对 | 0；不创建新生成尝试 |
| 已生成后的上传/对象服务/确认回执暂时失败 | 复用持久 journal、原文件和上传标识恢复 | 0；不因存储失败再次消耗 GPU |
| 取消、验证样本任务 | 既有取消/验证流程 | 0；不为验证样本自动换设备 |

所有分类共用原 job、逻辑 Key、准入自然日、输入引用、seed、执行摘要、deadline、网关意图与冻结价格。退避不占生成槽，但继续占未完成配额和原准入预留；最终只产生一次 UsageLedger 与网关结算。OOM 尝试即使进入 retry_wait，也必须记录在原 GPU/发布/规格的失败计数，不能被后续成功掩盖。

## 证据与兼容

Worker 协议仍为 0.4.0。新领取响应以 retry_policy=failure-retry-v1 显式声明能力；新 Worker 仅在该声明存在时发送可选 retry_evidence。旧 API 不声明时沿用旧失败事件；新 API 对旧 Worker 缺少此扩展的 OOM 只做既有确认失败，不自动重跑。

OOM 扩展只包含固定 kind、当前 prompt_id、durable association_id、白名单 exception_type、history/queue SHA-256 和 queue_absent=true。外层 evidence_digest 必须为该扩展的规范摘要；服务端核对当前 attempt 已记录的 prompt/association、状态、未生成稳定产物、关闭/释放和原始停止证据。不得上传异常全文、输入、节点堆栈或原始 history。

job-retry 四个字段形状不变，last_error 增加 comfy_oom；count/limit 仍为 0～2/2，next_at 仍可空。Admin 根据固定原因显示中文，不再把所有重试显示为提交前连接失败。

## 数据库、调度与恢复

新增向前迁移，扩展固定原因 CHECK；原历史迁移不改写。来源仍用同 job 复合外键。当前已关闭且确认释放的 OOM 尝试是持久物理 GPU 排除事实；同卡多个渠道共享排除。初选与锁内领取均检查，数据库在新 attempt 写入时再次拒绝原 OOM GPU，使旧领取进程也不能绕过。

失败事务先在原锁序内释放当前生成、关闭 attempt，再持久记录重试时间/原因/来源和失败观察；不在数据库锁中调用设备或存储 HTTP。替代 GPU 必须来自原 eligible_channels，保持当前配置/绑定验证、运行批准、节点/容量和规格隔离门禁；实际领取时重新核验。暂时被占用的合格替代 GPU 可等待，失效/禁用的替代 GPU不能借此获得批准。

Runner 到期只核验已有关闭/释放/停止事实及对应分类的关联证据；不清理其他任务的 occupancy。重复事件、并发 Runner、取消、到期和重启仍共享 job/配额锁，原事件返回原回执，旧尝试不得覆盖新尝试。排除事实跨后续准备重试保留。

迁移前备份并校验归档；有新 OOM 重试事实时禁止破坏性降级。滚动顺序是数据库兼容扩展、API、Runner、Worker；旧进程遇到新事实应停留或拒绝，不能降为不受限制的重跑。真实设备的 OOM、质量和跨卡画像仍在待办 25/26/60 验收。

## 验证计划

领域分类/预算、严格事件与 DTO、真实 PostgreSQL 事务和数据库旧写入保护、同卡多渠道/跨卡路由、失败隔离、并发重复事件与 Runner、混合原因预算、取消/到期/重启、六类冻结输入/额度/账本、实际 Worker history 与 queue 证明、HTTP/原生跨进程、Admin 桌面/小屏及候选安装分别留证。没有真实设备时仅记录协议或本地验证结果。

## 当前实现与操作

领域分类位于 [retries.py](../app/packages/domain/src/generation_domain/retries.py)，Worker 固定证明投影位于 [failure_causes.py](../app/apps/windows_worker/src/windows_worker/failure_causes.py)。控制面在失败事务、Runner 恢复、派单候选和锁内领取中复核；[retry_routes.py](../app/packages/infrastructure/src/generation_infrastructure/retry_routes.py) 集中读取历史 OOM 物理 GPU 及替代路由。新迁移 [2c9f1d6a4830](../app/migrations/versions/2c9f1d6a4830_confirmed_failure_retry.py) 加入原因约束及数据库旧写入保护。无新增客户货币账本或任意重试策略入口。

Admin 在任务详情显示“显存不足，重试需使用其他 GPU”、已安排次数与最早可分配时间。等待重试仍属于未完成配额，退避期间不计立即候选或 GPU 占用；可按原权限修改优先级或请求取消，取消后 next_at 为 null。375px 小屏中的长任务/版本/归属 ID 换行，保持原始完整值。

在已准备的原生环境，从 app/ 执行：

```powershell
./.venv/Scripts/python.exe -m pytest -q tests/integration/test_confirmed_failure_retries.py
./scripts/check.ps1 -Suite Sub2APINativeRetry
./.venv/Scripts/python.exe scripts/test-confirmed-retry-browser.py
# 从 docs/evidence/<日期>/confirmed-retry-browser-fixture.json 读取 run_id。
# 录制要求原生 Playwright/Edge，转换要求锁定媒体工具。
./.venv/Scripts/python.exe scripts/render-readme-media.py --confirmed-retry --run <run_id>
```

原生跨进程模式为六类任务各注入一次明确停止的合成 OOM，图片编辑额外经历一次提交前 503，核对共享 5/15 秒退避、不同物理 GPU、原执行摘要与唯一计量。它仅使用本项目隔离数据库和明确协议替身，不可据此宣称真实显存不足、质量或实机容量通过。

图形证据：[桌面](./media/confirmed-retry/retry-desktop.png)、[375px 手机](./media/confirmed-retry/retry-mobile.png)、[取消](./media/confirmed-retry/retry-mobile-cancelled.png)、[GIF](./media/confirmed-retry/confirmed-retry.gif)、[MP4](./media/confirmed-retry/confirmed-retry.mp4)、[脱敏响应](./media/confirmed-retry/api-responses.json)。实际 HTTPS/PG 的 8 项图形检查及资源清理见 [浏览器报告](./evidence/2026-10-25/confirmed-retry-browser-fixture.json)。录屏不包含登录或认证值；数据和故障均标明合成。

## 迁移、候选及失败保留

开发与隔离测试库先完成原生 PostgreSQL 18.6 备份、归档清单和摘要验证，再升至 head 2c9f1d6a4830；[迁移记录](./evidence/2026-10-25/confirmed-retry-migrations.json) 不伪称备份当时已恢复。随后 [一致快照恢复](./evidence/2026-10-25/confirmed-retry-restore-rehearsal.json) 核对 45 张业务表/758 行，恢复库已删除；对象字节、主密钥及生产联合恢复仍未验收。

最终构建候选 rc-20261025.82.3：[构建输入](./evidence/2026-10-25/confirmed-retry-release-build.json)、[清单](./evidence/2026-10-25/confirmed-retry-release-manifest.json) 记录 460 个源码输入、155 个载荷和 57 wheels，SHA-256 为 b93929c6a201aa6cc842c9d4283f8f4036682c437bad9c35131401acba8ae8a6。validated_releases 为空，production/real_fleet/real_workflows/private_s3 等验收标志全部为 false；同机隔离安装的出口继续单独记录。82.1/82.2 已被替代，不能作为最终源码的安装证明。

[最终候选隔离安装](./evidence/2026-10-25/confirmed-retry-release-installation.json) 的 24 项检查及 [安装后浏览器](./evidence/2026-10-25/confirmed-retry-installed-browser.json) 的 8 项检查通过；安装目录、临时数据库、自有进程/浏览器和配置已清理。安装使用实际候选 wheels、契约、迁移、前端和媒体工具，在项目目录外运行，不读取开发源码或隐式 .env；仍是同机验证。

[失败与修复记录](./evidence/2026-10-25/confirmed-retry-failure-notes.json) 保留旧 head 断言、不可变发布夹具、历史绑定选择、小屏裁切、状态机词义遗漏及并行 pytest 临时目录干扰。后者的受影响全量运行已停止，不能算通过；最终使用独立 basetemp。没有放宽业务约束、测试次数或故障预算。运行批准漂移、缺失/错误 profile、绑定摘要漂移、人工保留和隔离均须在实际领取时拒绝。

另保留 [并行运行时的网关鉴权锁超时](./evidence/2026-10-25/confirmed-retry-native-concurrent-lock-failure.json)：已观察到 rate_states 行锁超过既有等待限制，客户查询返回 502；根因尚未由事务阻塞链证明。隔离复验不能替代并发长稳，该现象继续列入真实网关/规模验收 29、62～64，不提高锁等待或生成重试预算。

只保留登记且按 PID/可执行文件/创建时间核对的开发 API、Runner、独立 GC 和回环 PostgreSQL；[HTTPS/授权检查](./evidence/2026-10-25/confirmed-retry-development-readiness.json) 记录证书验证及真实队列端点未鉴权 401。浏览器与临时夹具资源各自清理，不操作用户原有进程或标签。

## 本地分层验证

[最终完整 Python 回归](./evidence/2026-10-25/confirmed-retry-python.xml) 3232 项通过，747.03 秒，零失败/错误/跳过；既有 Starlette/httpx 弃用提示保留。[同步后契约与单元](./evidence/2026-10-25/confirmed-retry-contract-unit-final.xml) 2156 项通过。完整回归包含所有新增失败分类、恢复/取消、批准/绑定摘要漂移、数据库旧写入保护和降级拒绝用例，不用先前 57 项定向结果冒充新增用例已单独重跑。

Ruff、157 文件 mypy、75 包 uv 锁、40 项前端单元及 vue-tsc/Vite 生产构建通过；56 schema/865 fixture（207 正例/658 反例）、58 运行契约、188 设计端点、154 OpenAPI 路径/195 方法已同步。新状态机共 16 状态、64 转换、24 条件定义。原生跨进程、候选与生产资源的证据各自说明范围。

最终 [隔离原生跨进程](./evidence/2026-10-25/confirmed-retry-native-protocol.json) 在完整 Python 及候选安装结束后单独运行，228.59 秒、26 场景、11 合成任务通过。实际 Sub2API/API/Runner/Worker 与 PostgreSQL 贯通；ComfyUI/Redis/S3 明确使用协议替身。六类各有一次确认 OOM 并换到另一合成物理 GPU；图片编辑先经历准备 503，合计三次 attempt 和两次 5/15 秒退避。原执行摘要、输入、计量归属不变，11 次 Usage/结算、22 次副作用消费完成，冻结余额归零；11 份确认产物及最短保留规则已核对。临时进程、数据库、凭证和媒体均清理。

251 项运行源摘要、460 项候选输入均与最终源码逐项一致；未改 Go 客户端/Provider 源码与二进制按摘要复用 [80～81 的历史组件证明](./evidence/2026-10-25/provider-catalog-verification.json)，48 顶层/222 子测试和 172 顶层/325 子测试不冒充本轮重跑。[只读 PG 抽样](./evidence/2026-10-25/confirmed-retry-isolated-pg-samples.json) 不含 SQL/凭证，不能证明未采样时段无锁等待；隔离通过亦不关闭先前并行 502 或真实容量门禁。

本地实现出口关闭待办 05；[稳定编号复核](./evidence/2026-10-25/confirmed-retry-closeout-backlog.json) 保留 76 编号、01～05 完成、71 项开放（首版 69、可选 2）。[验证汇总](./evidence/2026-10-25/confirmed-retry-verification.json) 联结各层证据与失败；真实六类 Workflow、设备/GPU 故障、质量、S3/Redis、全新宿主、生产与 10～20 台容量均保持未验收，完整 goal 继续进行中。
