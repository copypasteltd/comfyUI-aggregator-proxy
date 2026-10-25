# 更新日志 / Changelog

## 2026-10-25 · Increment 86 / 数据库故障脱敏诊断

- Add allowlisted request-owned database error metadata and handle DBAPIError without forwarding SQL/driver tracebacks to Uvicorn.
- 增加真实 PostgreSQL 锁等待/恢复验证及原生双库只读阻塞链采样，保持既有 500/503、超时与重试限制。
- 修复任务详情的 READ COMMITTED 跨语句竞争：状态、revision 和授权产物采用同一个只读快照；真实完成事务交错的回归在修复前失败、修复后通过。
- Fix inconsistent job-detail reads with a read-only repeatable snapshot; retain the real PostgreSQL before/after regression and strict Provider validation.
- 修复包含路由模板遗漏，覆盖嵌套、同源多前缀、隐藏接口和挂载路径；不记录客户原始路径或查询参数。
- Correct included-router metadata and private CI log draining/closure; enforce the existing byte budget on small flushed chunks and add private 120-second per-test stack diagnostics.
- Preserve failed attempts; historical slow-query and concurrent gateway roots remain open. See [86](docs/86-数据库故障脱敏诊断与并发观测.md).

- 最终验证：完整 16 门禁/3593 Python、29 并发窗口原生协议、28 候选安装及 8 浏览器通过；候选 rc-20261025.86.1 的 488 构建输入一致，全部自有测试进程及浏览器已清理。
- Final validation: 587 CI inputs unchanged, 40 frontend checks, 59 schemas/947 fixtures and 62 runtime contracts; 166 payload files/58 wheels. Retain prior failures and one-time test-database maintenance; 70 backlog items remain open.

## 2026-10-25 — 原生固定角色系统服务工具 / Native fixed-role service tooling

- 新增 Windows 离线服务部署契约、固定角色 CLI、WinSW 2.12.0 pin 与 MIT 许可。默认手动启动；独立虚拟账户、外部秘密/状态、确定性 XML 重建和实际 SCM 身份核对，拒绝覆盖同名服务或接管其他进程。
- Add an offline Windows deployment contract, fixed-role CLI and pinned WinSW 2.12.0 asset with its MIT license. Use manual startup by default, per-service virtual accounts, external secrets/state, deterministic XML reconstruction and SCM identity checks; refuse existing registrations and foreign ownership.
- 同部署 API 先于 Worker 启动并晚于 Worker 停止，保留回报窗口。支持显式 render/validate/status/install/start/stop/uninstall、全局互斥、有界停止、部分安装失败的归属回滚与停止后卸载；不弹 UAC、不自动修改 ACL、不删除 journal/对象/配置，不管理 ComfyUI 进程。
- Start the API before Workers and stop it after them to preserve the shutdown reporting window. Provide explicit rendering, validation, status and lifecycle commands, a global operation mutex, bounded stops and ownership-checked installation rollback. Require an existing elevated token for mutations; keep ACL changes explicit and preserve journals, objects and configuration.
- 安装器从宿主 CPython 复制带摘要的专用服务解释器，验证真实 venv 及相同离线依赖；服务入口使用独立隐藏控制台。Provider 监督器只运行包内固定程序、清理继承配置覆盖并转发有界停止信号。
- Prepare a hashed dedicated service interpreter from the host CPython installation and verify the real venv and offline distributions. Use a private hidden console; the Provider supervisor runs only the bundled executable with explicit configuration and bounded signal forwarding.
- 验证驱动逐项清理自有资源并持久记录失败；保留首轮路由错误、第二轮总报告缺失及非提升 wrapper 未就绪的限制，不将控制台或单元结果冒充 SCM/生产验收。
- Preserve failed attempts and cleanup evidence. Unit and direct application-entry checks do not establish WinSW/SCM, service-account ACL, boot recovery or production acceptance; backlog item 66 and the 70 remaining items stay open. Details and final validation facts are recorded in [85](docs/85-原生系统服务封装与受控生命周期.md).

- 验证/Validation：161 项专项、完整 16 门禁/3524 Python（零失败/错误/跳过）、40 前端单元、162 文件 mypy、76 包锁、七个应用 wheel、28 项精确候选安装、8 项浏览器全部通过；581 项 CI 输入一致，四个应用角色退出码为 0、清理前无子进程，journal 与资源清理已核对。
- 最终候选/Final candidate：rc-20261025.85.3，281259923 字节、484 源输入、166 载荷、58 wheels、61 运行契约；SHA-256 20c5a79d1d439fcccaa76f424bd1fc982a70b17a7ab7abbb3b8c352736a2979d。58 schema/937 fixture 同步，45 业务表/head 3da02e7b5941，无新迁移。70 项仍开放（首版 68、可选 2）；复用既有明确范围的 Go/协议证据，未重跑或提升真实/生产标志。
- All listed local checks and cleanup passed. Keep the first audit-query 503, interrupted CI, failed installer attempts and unready wrapper probe visible. Actual WinSW/SCM, gateway supervision, boot recovery, production resources and the prior concurrent authentication 502 remain unresolved; see the [verification](docs/evidence/2026-10-25/native-services-verification.json).

## 2026-10-25 — 监控指标与请求关联 / Monitoring and request correlation

- 新增两个默认关闭的只读监控入口，使用独立 Bearer 摘要及实际连接 CIDR，支持双凭证轮换；业务、管理、Worker 和网关身份不可代用。安全配置响应不公开凭证、网段、DSN 或客户/设备标识。
- Add two disabled-by-default read-only monitoring routes with separate Bearer digests, actual connection-source CIDRs and two rotation slots. Keep business, Admin, Worker and gateway identities isolated; configuration responses expose no credentials, networks, DSNs or customer/device IDs.
- 增加固定标签的 Prometheus 进程/HTTP、队列、物理 GPU、节点容量、结算、待删对象与 PG 指标；专用只读池、五秒缓存、两秒 SQL 总预算，观测失败省略数据库指标并明确标记不可用。多实例全局数据库快照使用 max 去重。
- Add bounded Prometheus process/HTTP, queue, physical GPU, capacity, settlement, deletion and PG metrics. Use a dedicated read-only pool, five-second cache and two-second SQL budget; unavailable snapshots omit database gauges. Deduplicate shared database snapshots across instances with max.
- 纯 ASGI 包装保留上传/下载/SSE 背压，区分响应头与完整流时长；500 最终响应同样记录字节与服务端请求 ID。脱敏 JSON 日志采用有界异步队列，不记录正文、提示词、凭证、查询串或路径参数。
- Observe streaming uploads/downloads/SSE without buffering, separate header latency from full-stream duration, and correlate final 500 responses through bytes and server-generated request IDs. Bounded queued JSON events exclude bodies, prompts, credentials, query strings and path parameters.
- 新增 gen-monitoring 一次性文件凭证、显式外部配置和规则生成命令，以及原生 Prometheus/CA/凭证文件示例。向前迁移 3da02e7b5941 新增四个观测索引；先备份验证，再迁移，业务表数仍为 45。
- Add exclusive-file gen-monitoring provisioning, explicit external configuration, rule generation and native Prometheus/CA/credential-file examples. Forward migration 3da02e7b5941 adds four monitoring indexes after verified backup; the business-table count remains 45.
- 验证/Validation：完整 16 门禁/3399 Python、87 项监控专项、40 前端单元、162 文件 mypy、76 包锁、27 组原生协议、10 条告警的 5 场景/50 个 PromQL 断言、26 项候选安装和 8 项安装后浏览器通过。57 schema、902 fixture、60 运行契约、190 设计端点与 156 OpenAPI 路径/197 方法同步，详见 [84](docs/84-监控指标与脱敏请求关联.md)。
- 候选/Candidate：rc-20261025.84.1，476 源输入一致、159 载荷、58 wheels；SHA-256 8b35d2405befed546a0e68cc28ee8d4466c0090d02d4faf5d977de17ac340c4c。浏览器与所属临时资源清理，第 10 项本地实现关闭，70 项仍开放（首版 68、可选 2）。真实生产采集/通知、Workflow、S3/Redis、系统服务和实机容量继续独立验收。
- Exact-candidate installation, browser cleanup and all listed local checks passed. Item 10 closes locally, leaving 70 items (68 first-release and two optional). Actual production monitoring/notification, Workflows, S3/Redis, system services and physical-fleet capacity remain open; retain the unresolved concurrent authentication row-lock timeout/502.

## 2026-10-25 — 原生 CI 与完整待办追踪 / Native CI and complete backlog tracking

- 新增专用原生 Windows x64 自托管 CI；仅受信主分支且显式启用时执行。固定 Action 提交、只读仓库权限、串行门禁、OS/数据库锁和独立目录；不调用 Docker/WSL/VM，不自动注册开发主机。
- Add opt-in CI for a dedicated native Windows x64 Runner, trusted main only, pinned Action commits, read-only repository access, serial gates, OS/database locks and isolated directories. No Docker/WSL/VM or automatic registration of the development host.
- 契约/OpenAPI 新增只读漂移检查；前端类型在私有目录生成比对；测试库备份并验证归档后迁移，完整回归、前端单元/构建及七个 wheel 构建纳入驱动。
- Add read-only contract/OpenAPI drift checks, private frontend type comparison, test-only migration after verified backup, full regression, frontend tests/build and seven-wheel construction.
- 原始日志/JUnit 私有保留，公开 JSON 仅含固定状态/计数/摘要。固定超时及 64 MiB 日志上限，使用所属 Windows Job Object 清理子进程；本地并发拒绝与失败 not_run 记录已核对。
- Retain raw logs/JUnit privately and expose only fixed status/count/hash facts in JSON. Enforce timeouts and a 64 MiB log limit, close owned Windows process trees, and verify concurrent refusal plus durable not_run results.
- 43 项专项检查通过。完整待办保留 76 编号，01～05 实现关闭，71 项仍待收口（首版 69、可选 2）；11 仍缺实际 Runner 启用和 GitHub 成功/失败 artifact。真实资源与生产验收状态不提升。
- All 43 focused checks passed. Preserve 76 backlog IDs, five completed implementations and 71 open items (69 first-release, two optional). Item 11 still requires actual Runner enablement and GitHub success/failure artifacts. Real-resource and production acceptance remain open.
- 保留首轮完整 CI 的锁冲突失败；修正 CI 专用 advisory key，新增与派单/清理/迁移锁编号不冲突的回归，并在真实本地 PostgreSQL 核对持 CI 锁时派单/迁移仍可领取。不改业务锁或等待预算。
- Preserve the first full CI failure, assign a distinct CI advisory key, and add a cross-module regression. Verify dispatch and migration locks remain available while CI holds its own lock, without changing business locks or wait budgets.
- 最终本地 16 门禁、3275 Python（零失败/错误/跳过）、40 前端单元、157 文件 mypy、Ruff/75 包锁、七个 wheel 全部通过；558 源输入一致、五份公开 JSON 白名单通过，原始日志/JUnit 私有。两库保持 45 业务表/head 2c9f1d6a4830，测试库归档先验证后迁移；本轮没有新迁移或实机验收。
- Final local validation passed 16 gates, 3275 Python checks with zero failures/errors/skips, 40 frontend units, mypy on 157 files, Ruff, the 75-package lock and seven wheels. Verify 558 source inputs and whitelist five public JSON reports. Both databases retain 45 business tables/head 2c9f1d6a4830; verify the test backup before migration. No new migration or physical-resource acceptance.

## 2026-10-25 — 确认失败重试与跨 GPU 约束 / Confirmed failure retries across GPUs

- 完善安全失败分类：明确未提交的准备传输错误与有当前 prompt 停止证明的白名单 OOM 共享两次重试、5/15 秒退避；每任务最多一次 OOM，必须换到原冻结渠道内另一已批准物理 GPU，原 job/seed/Workflow/规格/输入/deadline/价格不变。
- Add strict failure classification, two shared retries with 5/15-second backoffs, and one evidenced OOM retry per job on another currently approved physical GPU within the original routes. Preserve job, seed, Workflow, specification, inputs, deadline and price.
- 新增向前迁移 2c9f1d6a4830、旧写入物理 GPU 保护和批准/绑定摘要领取复核；OOM 来源失败继续计入规格隔离，最后一次计量及 Sub2API 唯一结算保持不变。已有新事实时拒绝破坏性降级。
- Add forward migration 2c9f1d6a4830, database protection against reusing an OOM GPU, and claim-time approval/binding checks. Preserve source failure accounting and single final metering/settlement; reject destructive downgrade of persisted facts.
- Worker 0.4.0 使用显式 failure-retry-v1 声明和可选、脱敏的固定证明；旧 API/Worker 按原确认失败兼容。Admin 显示 OOM 原因、共享预算和下次时间，375px 长 ID 换行；补充双语操作文档、三张截图、MP4/GIF 和真实脱敏响应。
- Keep Worker 0.4.0 compatible through explicit capability advertisement and an optional fixed evidence projection. Show OOM cause, shared budget and dispatch time in Admin, wrap long mobile IDs, and update bilingual documentation with three screenshots, MP4/GIF and sanitized actual responses.
- 验证/Validation：3232 项完整 Python、2156 项契约/单元、157 文件 mypy、Ruff/75 包锁、40 前端单元、类型/构建、8 项重试浏览器、24 项最终候选安装和 8 项安装后浏览器通过；45 表/758 行一致快照恢复通过。上述范围均为本地或协议验证；完整原生链路结果及历史失败单独留证。
- 候选/Candidate：rc-20261025.82.3，460 源输入、155 载荷、57 wheels；SHA-256 b93929c6a201aa6cc842c9d4283f8f4036682c437bad9c35131401acba8ae8a6。真实设备、GPU OOM/质量、六类 Workflow、S3/Redis、生产和实机规模验收保持开放。
- 最终隔离原生链路 26 场景/11 合成任务通过，六类各一次 OOM、图片编辑混合准备失败共享预算、11 次唯一计量/结算及 22 次持久副作用消费核对通过；251 项运行源和候选 460 项源输入与当前一致。保留并行检查出现的鉴权行锁超时/502，未提升并发容量验收。待办 05 实现关闭，余 71 项（首版 69、可选 2）。
- Final isolated native verification passed 26 scenarios/11 synthetic jobs, including six OOM cases, a shared preparation-failure budget, 11 unique metering/settlement records and 22 durable effects. All 251 runtime and 460 candidate inputs match current sources. Retain the concurrent authentication lock timeout/502; capacity acceptance stays open. Implementation item 05 closes, leaving 71 items (69 first-release, two optional).

## 2026-10-25 — Provider 目录消费与图形映射 / Provider catalogs and visual mapping

- Provider 自动校验/持久化/确认目录，使用不可变请求快照，支持拒绝保留、丢回执和重启恢复、空目录下架及永久别名归属；Sub2API 新增向前迁移 257，旧迁移与唯一客户货币账本保留。
- Admin 增加网关/模型页目录入口，支持六类映射、价格、期望/确认、差异、历史、原请求核对及受控重试/回滚；models:read/publish 与网关凭证管理权限分开。64 位 ID 与八位价格使用精确字符串。
- Add durable Provider catalog consumption, activation/rejection, lost-receipt/restart recovery, empty withdrawal, immutable request stamps and permanent alias ownership. Add forward migration 257 while preserving prior migrations and the single customer ledger.
- Add visual gateway/model-page catalog management with six task types, prices, desired/confirmed versions, differences, history, original-request reconciliation and controlled retry/rollback. Separate read/publish permissions from credential management and preserve decimal precision.
- 新增文档 80/81、四张截图、一段 MP4/一张 GIF、脱敏响应及失败记录；更新双语 README、导航和稳定待办编号。Add topics 80/81, four screenshots, MP4/GIF, sanitized responses and retained failure evidence; synchronize bilingual documentation and stable checklist IDs.
- 验证/Validation：3148 Python、19 目录后端、40 前端单元、16 浏览器场景；Go 客户端 48 顶层/222 子测试、Provider 172 顶层/325 子测试、实际服务编译、20 组 legacy/25 组 managed 原生跨进程通过。目录媒体的 4 PNG/MP4/GIF 摘要及完整解码通过。All listed checks passed; physical GPU, production dependencies and fleet acceptance remain separate.
- 候选/Candidate：rc-20261025.81.1，456 源输入、154 载荷、57 wheels；24 项隔离离线安装及安装后 8 组浏览器通过，所属临时资源清理。456 source inputs, 154 payload files and 57 wheels; 24 isolated offline-install checks and eight installed-browser scenarios passed with owned-resource cleanup. This is not production acceptance.
- 待办/Pending：01～04 实现出口补齐，72 个跟踪项仍待收口，其中两项为可选增强。真实 Windows/Workflow、GPU 质量、S3/Redis、生产服务/恢复与 10～20 台容量未提升为通过。Implementation items 01–04 are complete; 72 tracked items remain open, including two optional improvements. Actual-resource/production acceptance remains open.

本文件记录面向使用者的变化、验证范围和迁移事项。日期使用 Asia/Taipei；源码推送不等于发布生产版本。

This file records user-facing changes, validation scope and migration notes. Dates use Asia/Taipei. Pushing source is not a production release.

## 2026-10-25 — 网关目录控制面 / Gateway catalog control plane

- 新增 [持久发布协议](docs/79-网关模型目录持久发布协议.md)：不可变模型/规格/价格目录、期望与确认分离、严格服务回执、历史、管理 CAS/幂等、重试与回滚；迟到确认不覆盖新版。
- 网关准入增加可选 `catalog`，首次确认后校验确认版本、主体范围、模型、Workflow、渠道与价格；原请求幂等和账本恢复沿用冻结身份，原生业务 Key 接口行为不变。
- 迁移 `1b8e0c5f3247` 新增三表、准入复合外键和数据库保护；原生备份后升级开发库，45 表一致快照恢复通过。修复原生 pytest 入口的共享测试模块路径配置。
- Add immutable catalog publication, desired/confirmed revisions, strict service receipts, history, CAS/idempotency, retry and rollback. Late receipts cannot overwrite a newer confirmation.
- Add an optional admission `catalog` stamp. Managed admission checks the confirmed revision, customer scope, model, Workflow, channel and price binding; existing intents and ledger recovery keep their frozen identities.
- Add migration `1b8e0c5f3247` with three tables, admission foreign keys and database guards. Back up and upgrade the development database; rehearse a consistent 45-table restore. Fix shared-test imports through the native pytest entry point.
- 验证/Validation：[完整记录](docs/evidence/2026-10-25/gateway-catalog-verification.json)：3146 项 Python、20 组原生 legacy 协议、45 表恢复、静态/类型/锁文件和前端构建通过；保留初始迁移和收集失败，最终回归通过。3146 Python checks, 20 native legacy protocol scenarios, a 45-table restore, static/type/lock checks and the frontend build pass. Initial migration/collection failures are retained with their fixes; actual-resource and production gates remain open.
- 待办/Pending：Provider 自动消费和 Admin 映射 UI 尚未接线，当前 Provider 保持启动配置和 legacy 模式；完整首版、真实资源与生产验收仍未完成。Provider consumption and Admin mapping UI remain open. No new offline candidate or production acceptance is published in this increment.

## 2026-10-25 — 完整待办评估 / Remaining-work assessment

- 新增 [76 项编号待办](docs/78-完整待办与首版收口清单.md)：5 项开发、6 项完善、7 项真实 Workflow 产品化、46 项实测、7 项配置、3 项交付与 2 项非首版阻断增强；同步中英入口和开发记录。
- 对照代码明确 Admin 模型发布与 Sub2API 启动模型/价格配置尚未形成持久同步、确认及失败恢复闭环；180 项端点已注册不能替代行为验收。
- 依据第 58 增量负载记录注明领取 P95 2.837 秒和页面更新 P95 3.9301 秒的历史/协议边界，不将更早的 7.682 秒视为当前测量。
- Add a numbered checklist of 76 known remaining items: 5 implementation, 6 refinement, 7 actual Workflow productization, 46 actual-environment checks, 7 configuration, 3 delivery and 2 optional improvements. Synchronize Chinese/English entry points and development records.
- Clarify the missing durable synchronization/confirmation/recovery between Admin publication and Sub2API startup model/pricing configuration. Route registration does not establish behavioral acceptance.
- Label increment 58 latency figures as historical protocol evidence rather than fresh measurements; the earlier 7.682-second figure does not describe later results.
- 验证/Validation：执行编号/分类核对、文档/契约与入口链接/示例检查、发布审查；无运行代码、UI、依赖、契约或数据库迁移变化，不重跑完整 Python 或重新录制未变化的媒体。Documentation/checklist-only change; no runtime/UI/dependency/contract/database changes or new hardware acceptance.
- 待办/Pending：完整首版与真实资源/生产门禁保持未验收；原有工作包状态不提升。Full release and actual-resource/production acceptance remain open; work-package status is not promoted.

## 2026-10-25 — 仓库首发 / Initial repository publication

### 新增 / Added

- 初始化 `copypasteltd/comfyUI-aggregator-proxy` 的源码仓库，保留 `app/` 代码与 `docs/` 设计/契约/证据分工。加入忽略规则、精确字节策略和编辑约定，排除运行时、数据库、凭证、模型、安装包及依赖缓存。
- 提供完整 [中文 README](README.md)、[English README](README.en.md)、英文文档导航和中英协作约定，说明架构、六类 Workflow、设备接入、启动、接口、Key/内容/额度、恢复与尚未验收范围。
- 新增 11 张真实 Admin 截图、2 段 MP4、2 张 GIF，以及经过范围限制的实际管理 API 响应。全部来自隔离 PostgreSQL 中的合成演示数据，不包含实际 GPU 生成或客户内容。
- 新增可复现录屏/媒体转换脚本与源码发布审查工具。约定后续代码、文档、演示及更新说明同步提交和推送。
- Establish the source repository while retaining code under `app/` and design/contracts/evidence under `docs/`. Exclude private runtimes, databases, credentials, weights, archives and dependency caches.
- Add complete Chinese/English READMEs, English documentation navigation and bilingual contribution guidance, covering architecture, six Workflow types, onboarding, startup, APIs, authorization, quota and recovery.
- Add 11 real Admin screenshots, 2 MP4 recordings, 2 GIFs and selected actual management API responses. All use isolated synthetic fixtures rather than GPU generation or customer content.
- Add reproducible capture/encoding and publication-audit tools; persist the requirement to synchronize future code, documentation, media and change notes.

### 已有功能基线 / Existing implementation baseline

- 第 76 增量：六类 ComfyUI 任务、持久调度、Windows Worker journal、图形 Admin、多业务 Key、内容管理、Sub2API Provider 集成及 Workflow 历史/归档。完整验收记录见 [76](docs/76-Workflow归档与恢复管理.md)。
- Increment 76 includes six ComfyUI task types, durable coordination, Windows Worker journals, visual Admin, multiple business Keys, content management, Sub2API Provider integration and Workflow history/lifecycle. See [recorded verification](docs/76-Workflow归档与恢复管理.md).

### 验证与迁移 / Verification and migration

- 本次为仓库与文档/演示工具整理，无数据库迁移、无运行接口或任务协议变化。执行文档/链接/请求示例、静态检查、合成录屏、媒体完整解码、发布内容审查及 Git 远端核对；不冒充重跑已有 3087 项完整 Python 回归。
- This delivery changes repository/documentation/demo tooling, with no database migration or runtime API/task protocol change. It checks documentation/links/request examples, static analysis, synthetic capture, full media decoding, publication candidates and the Git remote. It does not claim a fresh run of the historical 3087-check Python suite.
- 真实 Windows/Workflow/模型质量、S3/Redis、生产服务/联合恢复、10～20 台实机容量仍待验收。Actual devices/Workflows/model quality, S3/Redis, production services/joint recovery and 10–20-machine capacity remain unaccepted.

后续每次修改在本文件顶部增加对应日期的 Changes / Validation / Migration / Pending 说明。详见 [CONTRIBUTING](CONTRIBUTING.md)。
