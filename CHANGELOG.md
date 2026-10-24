# 更新日志 / Changelog

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
