# Documentation index

[124 · Deployed delivery](124-模型使用说明与公开Skill交付.md): ten model usage contracts, a published [Skill Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.0.0), 10-minute reclamation on three devices and four actual media jobs. [Installation](126-MediaGenSkill安装与调用.md) · [Release notes](127-MediaGenSkill-Release说明.md) · [Actual media](media/media-gen-124/README.en.md).

[123 · Idle resource release and multi-device model pools](./123-空闲资源释放与多设备模型池调度设计.md): distinguish GPU slot release from model/RAM/VRAM cleanup. No 600-second idle policy exists, and none of the ten published aliases has multiple channels. Specify instance-wide maintenance, durable idle epochs, conditional execution/completion acknowledgement, shared-instance races and global fair pool offers. Ten development items and fourteen acceptance groups remain pending; the [decision policy](contracts/comfy-idle-and-pool-policy.json) and [read-only audit](evidence/2026-10-28/runtime-idle-and-pool-audit-123.json) are design evidence, not runtime acceptance.

[122 · RTX 5070 HTTP onboarding](122-RTX5070纯HTTP接入与调用验证.md): select and publish only three complete Workflows out of 78. Six actual generation/download runs and 617 related regressions pass. A native Linux HTTP Worker requires no Windows-MCP; the platform now has three channels and ten published aliases. [Selected packages and English usage](examples/5070/README.en.md), [actual screenshots, recording and GIFs](media/5070-workflows-122/README.en.md) are included.

[121 · ComfyUI source cleanup after durable handoff](121-ComfyUI原始产物移交即清理设计.md): immediately request original-file cleanup after the complete verified spool package and recovery intent commit, independently of cloud upload. Specify the restricted extension, spool-first recovery, bounded retries, eight pending development items and ten acceptance groups. Design only; production originals are still retained and Worker/cloud retention stays as documented.

[120 · Reference generation and exact dimensions](120-参考能力发布与精确尺寸验证.md): four reference modes are published; existing text image/video aliases gain 720×1080. Seven aliases, six real validations, six business calls and nine negative checks are recorded with [actual media](media/reference-capabilities-120/README.md). Crop semantics, restart recovery, eight-hour retention and remaining acceptance boundaries are explicit.

[119 · Eight-hour cloud media retention](119-云端产物八小时保留.md): published image, video and audio outputs expire **8 hours after successful job completion**. New downloads are denied at expiry and GC deletes the exact object version. Save required files before expires_at. Existing outputs are shortened to the same deadline; completion replay never renews it. Worker spool retention remains one hour after cloud acknowledgement; ComfyUI originals and client downloads are managed separately. See the linked deployment and validation record.

[118 · Actual dual-GPU onboarding](118-双GPU真实ComfyUI接入.md): two native Windows GPUs, two channels, 241 candidate drafts, 51 model-file registrations and three published models verified through actual image/video/audio generation. All 588 related regression cases and eight Admin page/viewport checks pass. [Actual media](media/comfy-onboarding-118/README.md) is available. The third endpoint, reference modes, specialized Workflows, fleet capacity, gateway runtime and full acceptance remain pending.

[117 · Native ZeroTier and private-network access](117-ZeroTier原生客户端与私网接入.md): installation, startup, controller authorization and address assignment are complete. Actual cross-machine HTTP/SSH pass, existing routes/DNS and services remain intact. Public evidence excludes full network topology and secret files.

[115 · Native Linux deployment and disk cleanup](115-Linux服务器部署与磁盘清理.md) / [116 · HTTP entry and unrestricted origins](116-HTTP高位入口与任意来源访问.md): native PostgreSQL 18, private S3 and systemd services are deployed on plain HTTP port 39443 with unrestricted origins. All 309 related Python tests, two UUID tests, 55 final public checks and six browser page/viewport checks pass. [Actual deployment media](media/linux-deployment-116/README.md) is available. Cleanup reclaims about 3.43 GiB across two passes; actual generation and complete-product acceptance remain pending.

[113 · Provider status and candidate closeout](113-Provider实现状态与真实运行验收边界.md) Corrected Provider implementation status and scope while retaining runtime_integration_verified=false for unaccepted real dependencies. Source 7935b7d passes all 16 gates/4280 Python tests and 29 installation/10 browser checks of rc-20261026.113.1. All 649/525 inputs and embedded metadata match; actual Provider sources, patches and binary pass provenance guards. Reuse 30 native scenarios from 112 after all 275 inputs match; no new native execution is claimed. Local V01/V02/R05 exits from audit 114 are closed; real capabilities remain accepted at 0/6, with 69 stable exits, 98 complete gates and historical root causes open.

[114 · Current workspace and goal audit](114-当前工作区系统审计与完整目标差距.md) rehashes 649 inputs, the last executed increment-112 reports and every candidate payload. Core/Admin implementations exist; 69 exits remain open (67 first-release, 2 optional), real capabilities are accepted at 0/6, and 98 complete gates remain pending. A pre-existing uncommitted Provider metadata correction still needs current-source CI and a new candidate/install; all 275 native inputs are unchanged and reusable only within their stated scope. This audit does not rerun suites or promote acceptance.

[112 · Inflight input publication and cleanup](112-输入素材发布未决事务与清理保护.md) Input cleanup waits for the API Key lock already held by publication before checking the exact object version; external deletion holds no database connection. Source e12f604 passes 122 focused cases (six new), all 16 gates/4280 Python tests, 30 native scenarios, and 29 installation/10 browser checks of rc-20261026.112.1. All 649/275/525 source inputs and actual content.py wheel bytes match. Preserve two valid pre-fix missing-byte reproductions and four ID-parsing test failures, which are not behavioral evidence. Owned temporary resources are closed; six real Workflows, 69 stable exits, 98 complete gates and historical database root causes remain open.

[111 · Current project and complete goal gaps](111-当前项目完整目标与逐项差距.md) Reaudit current source c20b765: all 16 gates/4274 Python tests, 30 native scenarios and 29 installation/10 browser checks of rc-20261026.110.1 match current sources and actual payloads. Publish all 69 open exits (67 V1, two optional); real Workflow acceptance remains 0/6, with 98 complete gates and historical root causes open. Core/Admin implementation does not establish production acceptance.

[110 · Uncertain output publication and object cleanup](110-输出发布提交不确定与对象清理.md) Protect published outputs when a durable database commit loses its acknowledgement: wait for the same upload publication lock, verify the exact object version, and delete only after a successful absence check. Query or deletion failures preserve bytes and the original error; deletion runs after the database transaction ends. Source c20b765 passes 116 focused cases (nine new), all 16 gates/4274 Python tests, 30 native scenarios, and 29 installation/10 browser checks of rc-20261026.110.1. All 648/275/525 source inputs and the installed output-publication wheel bytes match. Keep both original failed reproductions. Owned temporary resources are closed; all 69 stable exits, six real Workflows, 98 complete gates and historical database root causes remain open.

[109 · Current-source regression and candidate verification](109-当前源码全量验证与安装候选收口.md) closes audit 108 subtasks V01–V03: all 16 gates/4265 Python tests, 30 native scenarios and 29 installation/10 browser checks of rc-20261026.107.1 pass for 75db9a0. All 647/275/525 source inputs and actual Worker wheel bytes match, and owned temporary resources are cleaned up. Audit 108 retains its original historical source drift; current local validation is now complete while all 69 stable exits, zero of six real Workflow acceptances, 98 complete gates and historical database causes remain open.

[108 · Current workspace versus the complete goal](108-当前工作区与完整目标差距复核.md) rehashes all 647 application inputs and the actual previous candidate. The 248 focused checks cover current increment 107; two Worker sources differ from the 106 full CI/native/candidate, so a current full regression, native run and rebuilt installation remain pending. The numbered inventory retains 69 open exits (67 V1, two optional), zero of six real Workflow acceptances and 98 pending complete gates. Local implementation, historical tests and production acceptance are distinct.

[Project README](../README.en.md) · [中文文档索引](README.md) · [Changelog](../CHANGELOG.md)

The project README provides the complete English architecture and operating guide. Detailed engineering records below are currently written in Chinese; this index provides English navigation for every numbered topic. Historical records describe their own revisions. A written design or synthetic test does not establish production acceptance.

Start with requirements, development rules, engineering structure and the delivery checklist. For API changes, read the frozen contracts and Worker protocol before implementation. Code/configuration/scripts stay under `app/`; documentation/contracts/evidence/media stay under `docs/`. Native processes only: no Docker, WSL or VMs.

[107 · Final output metadata and Worker isolation](107-ComfyUI最终产物结构与Worker隔离.md) fixes controlled rejection of malformed history and recovery quarantine races, including failed tasks still cleaning up. All 248 strengthened focused cases, 16 gates/4265 Python tests, 30 native scenarios and 29 installation/10 browser checks of rc-20261026.107.1 pass. All 647 CI/275 native/525 candidate inputs and actual Worker metadata/runtime wheel bytes match 75db9a0. Preserve the 90/12/1 failed reproductions and clean up owned temporary resources. Real-resource exits 17/23/43/55/60, all 69 remaining items and full production acceptance stay open.

[106 · Interrupted Worker output-copy recovery](106-Worker产物中断复制与恢复.md) fixes existing spool files blocking recovery through full-prefix verification and bounded append/exact-copy reuse. Different, longer, hardlinked or reparse targets stay isolated without truncation, overwrite or regeneration. All 115 focused cases, 16 gates/4137 Python tests, 30 native scenarios and 29 installation/10 browser checks of rc-20261026.106.1 pass. All 644 CI/275 native/525 candidate inputs and actual Worker wheel bytes match application commit 1f62868. Initial failures and the native Windows timestamp diagnostic remain intact, and owned temporary resources are cleaned up. Real-device exits 42/55/61, all six real Workflows, 69 remaining items and complete production acceptance stay open.

[105 · Current project and goal gaps](105-当前项目检查与目标差距清单.md) rehashes 640 CI, 274 native and 524 candidate inputs plus all 169 ZIP payloads against current HEAD 316c540. Existing 16 gates/4074 Python, 30 native and 29 installation/10 browser results match the application source; complete tests were not rerun. The current in-memory compiler probe preserves six valid graphs and rejects 24 unsafe phase checks, confirming the 104 fix. All 69 remaining exits (67 V1, two optional), 0/6 real Workflows and 98 pending full gates remain explicit in numbered tables; historical database causes and strict Video SDK limits stay open.

[104 · Workflow graph-edge protection](104-Workflow标量绑定与连线保护.md) fixes audit 103/F01: bindings and presets may write only original scalar inputs, enforced at release validation and Worker compilation before submission intent or ComfyUI POST. Drafts remain editable and unsafe imports are unpublishable. All 173 focused cases, 16 gates/4074 Python tests, 30 native scenarios and 29 installation/10 browser checks of candidate rc-20261026.104.1 pass. All 640 CI/274 native/524 candidate inputs match application commit d2d53ff. Pre-fix and harness failures are retained; temporary resources are cleaned up. The local defect is addressed while all 69 complete exits, real-resource/production acceptance and historical database causes remain open.

[103 · Current-source goal audit](103-当前版本系统检查与目标产物差距.md) rehashes all 637 CI, 274 native and 524 candidate inputs plus the actual ZIP; they match the completed increment 101. There are still 69 open items (67 required, 2 optional), 0/6 accepted real Workflows and 98 pending full-acceptance gates. A new core validation defect was reproduced with all six synthetic capabilities: parameter bindings or presets can overwrite graph edges (12 unsafe variants accepted). It is prioritized under existing items 24/46. This audit changes no application code or acceptance state; passing engineering tests does not establish product readiness.

[Increment 101: dependency errors and safe recovery](101-依赖不可用错误与安全重试边界.md) completes redacted API/Provider 503 responses, request correlation and uncertain-write protection. The 148-case Python component, 178 top-level/356 Go subtests, all 16 gates/4013 Python tests and 30 native scenarios pass; 637 CI/274 native inputs match application commit 6e4b78f. Candidate rc-20261026.101.1 binds 524 sources and 169 payloads/58 wheels/67 contracts, passes 29 exact-installation/10 browser checks, and cleans up its temporary resources. The local Go/evidence/current-regression/candidate gaps recorded in [audit 102](102-当前工作区系统检查与目标差距.md) are addressed; its historical scope is retained. Actual Workflows, physical machines, real Redis/S3, production and historical database causes remain pending, with all 69 complete exits open.

[Increment 99: database wait classification](99-原生数据库等待分类与并发复验.md) preserves allowlisted I/O and IPC events in the native test observer while retaining legacy lock fields, scope, capacity and redaction limits. Seventy-three focused checks and all 16 gates/3938 Python tests pass. The complete 29-case native run fits inside the Python gate; 1585 samples have no capacity truncation and all owned processes/databases/threads/media/credentials are cleaned up. All 633 CI and 273 runtime inputs match application commit 21d6ac2. No slow I/O or historical 502 is reproduced. Rate limits, budgets and generation behavior remain unchanged; historical causes and real-resource acceptance stay open. Candidate 97 is not rebuilt: its source manifest retains the prior observer script, so its installation evidence is not promoted to this increment.

The [historical systematic recheck (98)](95-项目现状审计与目标产物差距.md) verifies all 632 CI inputs, 521 candidate inputs and the actual archive digest against application commit 31f3798. It does not rerun the complete tests or promote acceptance. All 69 exits remain open (67 first-release, two optional), with 0/6 actual Workflows accepted. A 500-call authentication baseline and cleanup on a clone of the preserved test database pass, but neither reproduces the original failure or closes historical 502/503/timeout causes. Audit sections 4 and 8 and [checklist 78](78-完整待办与首版收口清单.md) retain every numbered exit condition.

[Increment 97: release provenance and audit readability](97-发行来源绑定与审计状态可读性.md) binds new builds to an actual Git commit and committed source bytes, rejecting changes during packaging. Commit application/CI changes before building; documentation may follow. Audit policy status appears immediately with readable padding and mobile wrapping in the original Lime system. Ninety release checks, 19 UI checks across three widths and all 16 gates/3915 Python tests pass. Candidate rc-20261026.97.1 embeds actual source commit 31f3798, contains 169 payload files, 58 wheels and 66 contracts, and passes 29 installation and ten installed-browser checks. Original-speed recordings and evidence are documented; actual resources and production remain pending.

[Current-source verification and candidate 96](96-当前源码完整验证与原生发行候选.md) pass all 16 gates and 3899 Python tests with zero failures, errors or skips; all 628 application inputs remain identical. Python takes 595.039 seconds within the unchanged 1200-second budget. Candidate rc-20261026.96.1 contains 169 payload files, 58 wheels and 66 runtime contracts and passes 29 fresh isolated-installation checks and ten installed-browser checks. Temporary resources are cleaned up. Actual resources, production and historical failure root causes remain pending; the Git-revision and status-display follow-ups recorded there are addressed by increment 97 above.

The [project audit at increment 95](95-项目现状审计与目标产物差距.md) expands all 69 remaining items (67 first-release, two optional), current source provenance and five unresolved defect/compatibility/release boundaries. At that audit, focused evidence and historical full CI/candidate were distinct; verification 96/97 subsequently fills the local full-CI/candidate gap; audit section 7 records current evidence and all 69 remaining full-acceptance items. All 98 full acceptance gates remain pending.

Current functional [increment 94](94-队列等待估算与运行样本.md) adds on-demand queue estimates to Admin job details. A consistent snapshot combines physical GPU occupancy, scheduling policy and matching runtime samples from the last 24 hours. It shows a conditional allocation-wait interval, sample counts and a 15-second validity window. Missing samples, stale devices or uncertain execution produce unknown; actual-device calibration remains open.

Previous [increment 93](93-真实Workflow离线发布包构建.md) implements offline package preparation for all six capabilities using existing local model hashes and Git revisions. It emits an importable manifest, clears prior runtime claims and supports dependency-drift checks. See [focused evidence](evidence/2026-10-26/workflow-package-python-93.json); physical-resource and full-release exits remain open.

Previous [increment 92](92-直接视频接口冻结投影与权限边界.md) aligns direct job_ video creation, original-key replay and detail reads with frozen specifications, exact millisecond durations and safe terminal errors. It preserves current scopes, revocation, sensitive-input controls, cross-key/tenant isolation and bounded list queries. Verification passes 28 source-bound video cases, 74 release-security cases, all 16 gates/3790 Python tests, 39 actual SDK checks within 24 native scenarios, 29 exact-candidate installation checks and ten browser checks. See [verification 92](evidence/2026-10-26/direct-video-verification-92.json). Preserve both CI timeouts, the bounded statistics experiment and other failures; the original 1200-second budget stays fixed. Original Lime sources and all 69 remaining full-acceptance items are preserved.

Previous [increment 91](91-多媒体SDK与视频冻结规格.md) adds immutable Provider video specifications, safe terminal errors and actual Python/Node multimedia checks. Go/PostgreSQL passes 176 top-level/342 subtests; actual clients pass 39 SDK checks, including the known strict-type rejection, within 24 native scenarios. [Final verification](evidence/2026-10-26/multimedia-sdk-verification-91.json) passes all 16 gates/3713 Python cases, 28 exact-candidate installation checks and ten browser checks. All 611 CI/274 native/505 candidate inputs match; 95 documents, 61 schemas/976 fixtures and 64 runtime contracts are synchronized. Historical failures, strict-type limitations, direct-control video projection parity and actual-resource exits remain explicit.

Previous [increment 90](90-固定SDK客户端兼容矩阵.md) passes 20 checks using actual Python 3.37.0 / Node 7.43.0 SDKs within 24 native scenarios. It fixes strict decoding of ordinary model entries with missing standard metadata, preserves failed attempts and records owned-resource cleanup. Actual resources, the complete client matrix and all 69 remaining items stay open.

[Final verification 90](evidence/2026-10-25/openai-sdk-verification-90.json) passes all 16 gates/3696 Python cases, 206 focused checks, 173 top-level/331 Go subtests, 28 exact-candidate installation checks and ten browser checks. All 606 CI/271 native/501 candidate inputs match. Preserve the CI budget failure and controlled statistics experiment; the original 1200-second budget stays fixed. Synchronization covers 94 documents, 60 schemas/959 fixtures and 63 runtime contracts.

The earlier [increment 89](89-审计分页投影上限与版本化复验.md) bounds audit-detail conversion to at most 51 selected page rows, with native PostgreSQL expression-count regressions and fresh versioned Python evidence. Snapshot consistency, SQL redaction and timeouts are preserved; complete historical causes and all 69 remaining items stay open.

[Increment 88](88-需求实现与分层证据追踪.md) maps 73 requirements and 98 gates to implementation, 99 named tests, six historical reports and remaining items. Its offline audit originally checked 141 referenced code/test files, report hashes, complete ID sets and the readable matrix. It rejects drift and false acceptance. Item 12 closes; the following older counts describe their historical revisions.

The [checklist](78-完整待办与首版收口清单.md) preserves 76 stable IDs. Local implementation/traceability items 01–05, 10 and 12 are complete, leaving 69 items (67 first-release, two optional). [Topic 79](79-网关模型目录持久发布协议.md) connects to [Provider consumption](80-Provider目录消费与版本快照.md) and [Admin mapping management](81-Admin网关目录映射与发布核对.md). [Topic 82](82-确认失败重试分类与跨GPU约束.md) defines confirmed-failure retries; [topic 84](84-监控指标与脱敏请求关联.md) implements separate read-only monitoring and sanitized request correlation. Physical-resource and complete release acceptance remain open.

The preceding engineering increment is [84: monitoring](84-监控指标与脱敏请求关联.md). All 87 focused checks, 16 gates/3399 Python checks, 27 native scenarios, 50 PromQL assertions, 26 exact-candidate installation checks and eight installed-browser checks passed. Synchronization covers 57 schemas/902 fixtures, 60 runtime contracts, 190 design endpoints and 156 paths/197 methods. Actual [GitHub Runner](83-原生CI门禁与失败证据.md) enablement remains open under item 11; production collection/logging/notification remains open under item 68. That increment left 70 items; consult the [complete checklist](78-完整待办与首版收口清单.md) for later closures.

The previous increment, [85: native service tools](85-原生系统服务封装与受控生命周期.md), passes 161 focused checks, all 16 gates/3524 Python checks, 28 exact-candidate installation checks and eight browser checks. It synchronizes 58 schemas/937 fixtures and 61 runtime contracts. [Verification](evidence/2026-10-25/native-services-verification.json) retains failed attempts and their cleanup. Actual WinSW/SCM, service-account ACLs, boot recovery and new gateway-supervisor lifecycles remain open under item 66.

[Increment 86](86-数据库故障脱敏诊断与并发观测.md) adds allowlisted database failures, request isolation, a dedicated database exception handler and bounded read-only blocker observation. Historical causes and real-resource acceptance remain open.

[Increment 87](87-青柠视觉系统与组件复用.md) imports the original 04 Lime CSS and exact decoded fonts, adapts all Admin components and overlays, and records the continuing visual constraint. Seventeen routes pass desktop and 390px/320px layout checks; the gallery contains 20 refreshed screenshots and four MP4/GIF pairs. Forty frontend and 71 release security tests pass, along with 28 final-candidate installation and ten installed-browser checks. [Verification](evidence/2026-10-25/lime-source-visual-verification.json) keeps the 70 backlog items and actual-resource acceptance open.

## Requirements and engineering baseline

| Topic | Document |
| --- | --- |
| 01 | [Requirements and constraints](01-需求规格与约束.md) |
| 02 | [Architecture and durable task mechanisms](02-系统架构与任务机制.md) |
| 03 | [API and Sub2API integration conventions](03-接口与Sub2API接入规范.md) |
| 04 | [Productizing ComfyUI Workflows](04-工作流产品化规范.md) |
| 05 | [Implementation roadmap and acceptance](05-实施路线与验收.md) |
| 06 | [Research sources and compatibility review](06-调研依据与兼容性核对.md) |
| 07 | [Visual Admin requirements](07-Admin可视化管理后台.md) |
| 08 | [Business Key authentication and access control](08-APIKey鉴权与访问控制.md) |
| 09 | [Development rules and mandatory invariants](09-开发总纲与强制约束.md) |
| 10 | [Technology stack and package boundaries](10-技术栈与工程结构.md) |
| 11 | [Data dictionary and database transactions](11-数据字典与数据库事务.md) |
| 12 | [API contracts and Worker protocol](12-接口契约与Worker协议.md) |
| 13 | [Admin frontend rules](13-Admin前端开发规范.md) |
| 14 | [Test strategy and quality gates](14-测试策略与质量门禁.md) |
| 15 | [Native deployment and operations](15-原生部署配置与运维.md) |
| 16 | [Work breakdown, delivery status and remaining gates](16-开发任务分解与交付清单.md) |
| 17 | [Implementation and execution records](17-开发记录与运行说明.md) |

## Control plane, Worker and initial integration

| Topic | Document |
| --- | --- |
| 18 | [Initial Admin implementation](18-Admin界面实现约定.md) |
| 19 | [Execution pipeline implementation](19-执行链路增量实现约定.md) |
| 20 | [Gateway admission and reconciliation](20-网关准入与对账实现约定.md) |
| 21 | [Content retention and collection](21-内容保留与回收实现约定.md) |
| 22 | [Job operations and administrator accounts](22-任务处置与管理账号实现约定.md) |
| 23 | [Model file directory](23-模型文件目录实现约定.md) |
| 24 | [Workflow batch synchronization and verification](24-Workflow批量推送与验证实现约定.md) |
| 25 | [Native Sub2API adapter plan](25-Sub2API原生适配实施约定.md) |
| 26 | [Read-only alerts, nodes and attempts](26-告警与节点尝试只读查询约定.md) |
| 27 | [Worker metadata response boundaries](27-Worker元数据响应边界约定.md) |
| 28 | [Business Key access records](28-APIKey访问记录实现约定.md) |

## Sub2API Provider and public API behavior

| Topic | Document |
| --- | --- |
| 29 | [Durable intents and ledger transactions](29-Sub2API持久意图与账本事务约定.md) |
| 30 | [Gateway request closure and recovery](30-网关请求关闭与恢复协议.md) |
| 31 | [Recovery leases and coordinator](31-Sub2API恢复租约与协调器.md) |
| 32 | [Public task ingress](32-Sub2API公开任务接入.md) |
| 33 | [Hosted Provider runtime and lifecycle](33-Sub2API服务托管与生命周期.md) |
| 34 | [Generation compatibility and artifact delivery](34-Sub2API生成兼容与产物访问.md) |
| 35 | [Assets and image edits](35-Sub2API素材与图片编辑接入.md) |
| 36 | [Model discovery and capability projection](36-Sub2API模型发现与能力投影.md) |
| 37 | [Cursor pagination and explicit content sharing](37-任务与内容游标及共享访问.md) |
| 38 | [Standardized references and Workflow options](38-标准化参考输入与Workflow选项.md) |
| 39 | [Durable usage/cache effect consumption](39-Sub2API用量与缓存持久消费.md) |
| 40 | [Rate multipliers and subscription pricing](40-Sub2API倍率与套餐计费.md) |

## Runtime operations, audio and recovery

| Topic | Document |
| --- | --- |
| 41 | [Channel Workflow pull and draft import](41-Workflow渠道拉取与草稿导入.md) |
| 42 | [Workflow directory and source state](42-Workflow目录浏览与来源状态.md) |
| 43 | [Native cross-process Sub2API integration](43-Sub2API原生跨进程联调.md) |
| 44 | [Worker capacity protection and local output retention](44-Worker容量保护与本地产物保留.md) |
| 45 | [Channel model inventory and controlled registration](45-渠道模型清单与受控登记.md) |
| 46 | [Generation failure attribution and specification isolation](46-生成失败归因与规格隔离.md) |
| 47 | [Queue filtering and live-load acceptance](47-队列筛选与实时负载验收.md) |
| 48 | [Content search, tags and authorized preview](48-内容检索标签与受控预览.md) |
| 49 | [Native release bundles and offline installation](49-原生发布包与离线安装验证.md) |
| 50 | [Evidence-based internal retries and metering preservation](50-有证据内部重试与计量保全.md) |
| 51 | [Tenant fairness and waiting-time aging](51-租户公平调度与等待老化.md) |
| 52 | [Tenant/project administration and hierarchical quotas](52-租户项目与分层配额管理.md) |
| 53 | [Key content authorization and paginated selections](53-Key内容授权目录与分页选择.md) |
| 54 | [ComfyUI audio output scope and constraints](54-ComfyUI音频产物范围与开发约束.md) |
| 55 | [Audio contracts and compatible migration](55-音频契约与兼容迁移.md) |
| 56 | [Windows node registration and credentials](56-Windows节点登记与凭证管理.md) |
| 57 | [Worker startup identity and native process protection](57-Worker启动身份与原生进程保护.md) |
| 58 | [Runtime trends and job-phase duration](58-运行趋势与任务阶段耗时.md) |
| 59 | [S3 object versions and range delivery](59-S3版本与范围交付校验.md) |
| 60 | [Joint database/object/credential recovery verification](60-数据库对象与密钥联合恢复校验.md) |

## Admin maintenance and repository delivery

| Topic | Document |
| --- | --- |
| 61 | [Management audit search and details](61-管理审计检索与详情.md) |
| 62 | [Session idle timeout and interaction renewal](62-管理会话空闲超时与交互续期.md) |
| 63 | [Audit retention and bounded pruning](63-管理审计保留与有界清理.md) |
| 64 | [Channel search and direct detail access](64-渠道检索与详情直达.md) |
| 65 | [Channel deletion constraints and concurrency](65-渠道删除约束与并发保护.md) |
| 66 | [Durable command results and reconciliation](66-管理指令持久结果与恢复核对.md) |
| 67 | [Workflow target retries and operation lineage](67-Workflow失败目标重试与操作关联.md) |
| 68 | [Batch job cancellation and durable per-item results](68-任务批量取消与持久逐项结果.md) |
| 69 | [Confirmed generation-failure alerts and focused filters](69-已确认生成失败告警与定位筛选.md) |
| 70 | [Paginated model catalog and frozen specification selection](70-模型目录分页与冻结规格选择.md) |
| 71 | [Key generation-resource selection and request reconciliation](71-Key生成资源授权选择与请求核对.md) |
| 72 | [Workflow search and paginated synchronization targets](72-Workflow检索与分页同步选择.md) |
| 73 | [Workflow release history and differences](73-Workflow发布历史与版本差异.md) |
| 74 | [Workflow parameter and metadata forms](74-Workflow参数与元数据表单.md) |
| 75 | [Restore a release into a new draft](75-Workflow历史版本恢复草稿.md) |
| 76 | [Workflow archive and unarchive](76-Workflow归档与恢复管理.md) |
| 77 | [Repository preparation and bilingual documentation delivery](77-仓库整理与双语文档交付.md) |
| 78 | [Complete remaining-work and first-release checklist](78-完整待办与首版收口清单.md) |
| 79 | [Durable gateway catalog publication protocol](79-网关模型目录持久发布协议.md) |
| 80 | [Provider catalog consumption and frozen request versions](80-Provider目录消费与版本快照.md) |
| 81 | [Admin gateway mappings and publication reconciliation](81-Admin网关目录映射与发布核对.md) |
| 82 | [Confirmed failure classification, retry budget and physical GPU constraints](82-确认失败重试分类与跨GPU约束.md) |
| 83 | [Native CI gates, process cleanup and safe failure evidence](83-原生CI门禁与失败证据.md) |
| 84 | [Monitoring metrics, isolated read-only identity and sanitized request correlation](84-监控指标与脱敏请求关联.md) |
| 87 | [Original 04 Lime source, typography and shared Admin component system](87-青柠视觉系统与组件复用.md) |
| 88 | [Requirement implementation, named tests and layered acceptance evidence](88-需求实现与分层证据追踪.md) |
| 89 | [Bounded audit-page projections and versioned Python verification](89-审计分页投影上限与版本化复验.md) |
| 90 | [Pinned Python/Node SDK clients, catalog normalization and compatibility matrix](90-固定SDK客户端兼容矩阵.md) |
| 91 | [Immutable video specifications and actual multimedia SDK clients](91-多媒体SDK与视频冻结规格.md) |
| [93-真实Workflow离线发布包构建](./93-真实Workflow离线发布包构建.md) | Offline package preparation, native dependency locking and import/compile verification |
| [94 · Queue waiting estimates and runtime samples](94-队列等待估算与运行样本.md) | Matching observations, bounded read-only queue scenarios, conditional intervals and Admin UI |
| [95 · Current project audit and goal gaps](95-项目现状审计与目标产物差距.md) | Evidence at that revision, all 69 open items, known defects, release differences and delivery order |
| [96 · Current-source verification and native candidate](96-当前源码完整验证与原生发行候选.md) | 3899 Python tests, rc-20261026.96.1, exact installation/browser evidence and remaining acceptance boundaries |
| [99 · Native database wait classification and concurrent verification](99-原生数据库等待分类与并发复验.md) | Allowlisted I/O/IPC waits, preserved legacy fields and contained native/CI verification |
| [97 · Release provenance and audit policy readability](97-发行来源绑定与审计状态可读性.md) | Actual Git/blob identity, build drift rejection, three-width policy UI and original-speed recordings |
| 92 | [Direct video projection, scope boundaries and installed verification](92-直接视频接口冻结投影与权限边界.md) |
| 86 | [Sanitized database failures, request isolation and native lock observation](86-数据库故障脱敏诊断与并发观测.md) |
| 85 | [Native system service plans, controlled lifecycle and dedicated interpreter](85-原生系统服务封装与受控生命周期.md) |

## Contracts, examples and media

- [Machine contracts](contracts/README.md): frozen JSON Schemas, fixtures, state machine, endpoint inventory and development traceability.
- Complete native requests: [image](examples/create-image.json), [video](examples/create-video.json), [audio](examples/create-audio.json).
- [Demonstration gallery](media/README.md): 30 screenshots, six recordings in MP4/GIF, selected actual API responses, explicitly labeled receipt/failure fixtures, hashes and reproduction steps; all use synthetic business fixtures.
- [Contribution/synchronization rules](../CONTRIBUTING.md): update both READMEs, change notes and applicable media, run checks, commit and push.

Production acceptance remains open for actual devices and Workflows, model quality, real storage/gateway dependencies, system services, joint recovery and physical-fleet capacity. The delivery checklist is authoritative for completion status.

## Increment 86 final evidence

[Database diagnostics and concurrency](86-数据库故障脱敏诊断与并发观测.md) passed all 16 gates/3593 Python checks, 29 native cases within the complete concurrent CI window, 28 exact-candidate installation checks and eight installed-browser checks. The candidate matches 488 build inputs; 587 CI and 264 native runtime inputs are unchanged. It contains 166 payload files, 58 wheels and 62 runtime contracts. Failed attempts, one-time test-database maintenance and unresolved historical roots remain visible. See the [verification](evidence/2026-10-25/database-diagnostics-verification.json). All 70 pending items retain their acceptance boundaries.
