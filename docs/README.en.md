# Documentation index

[Project README](../README.en.md) · [中文文档索引](README.md) · [Changelog](../CHANGELOG.md)

The project README provides the complete English architecture and operating guide. Detailed engineering records below are currently written in Chinese; this index provides English navigation for every numbered topic. Historical records describe their own revisions. A written design or synthetic test does not establish production acceptance.

Start with requirements, development rules, engineering structure and the delivery checklist. For API changes, read the frozen contracts and Worker protocol before implementation. Code/configuration/scripts stay under `app/`; documentation/contracts/evidence/media stay under `docs/`. Native processes only: no Docker, WSL or VMs.

The [checklist](78-完整待办与首版收口清单.md) preserves 76 stable IDs. Local implementations 01–05 and 10 are complete, leaving 70 items (68 first-release, two optional). [Topic 79](79-网关模型目录持久发布协议.md) connects to [Provider consumption](80-Provider目录消费与版本快照.md) and [Admin mapping management](81-Admin网关目录映射与发布核对.md). [Topic 82](82-确认失败重试分类与跨GPU约束.md) defines confirmed-failure retries; [topic 84](84-监控指标与脱敏请求关联.md) implements separate read-only monitoring and sanitized request correlation. Physical-resource and complete release acceptance remain open.

The preceding engineering increment is [84: monitoring](84-监控指标与脱敏请求关联.md). All 87 focused checks, 16 gates/3399 Python checks, 27 native scenarios, 50 PromQL assertions, 26 exact-candidate installation checks and eight installed-browser checks passed. Synchronization covers 57 schemas/902 fixtures, 60 runtime contracts, 190 design endpoints and 156 paths/197 methods. Actual [GitHub Runner](83-原生CI门禁与失败证据.md) enablement remains open under item 11; production collection/logging/notification remains open under item 68. The [complete checklist](78-完整待办与首版收口清单.md) retains all 70 open items and their exit conditions.

The latest increment, [85: native service tools](85-原生系统服务封装与受控生命周期.md), passes 161 focused checks, all 16 gates/3524 Python checks, 28 exact-candidate installation checks and eight browser checks. It synchronizes 58 schemas/937 fixtures and 61 runtime contracts. [Verification](evidence/2026-10-25/native-services-verification.json) retains failed attempts and their cleanup. Actual WinSW/SCM, service-account ACLs, boot recovery and new gateway-supervisor lifecycles remain open under item 66.

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
| 85 | [Native system service plans, controlled lifecycle and dedicated interpreter](85-原生系统服务封装与受控生命周期.md) |

## Contracts, examples and media

- [Machine contracts](contracts/README.md): frozen JSON Schemas, fixtures, state machine, endpoint inventory and development traceability.
- Complete native requests: [image](examples/create-image.json), [video](examples/create-video.json), [audio](examples/create-audio.json).
- [Demonstration gallery](media/README.md): 18 screenshots, four recordings in MP4/GIF, selected actual API responses, explicitly labeled receipt/failure fixtures, hashes and reproduction steps.
- [Contribution/synchronization rules](../CONTRIBUTING.md): update both READMEs, change notes and applicable media, run checks, commit and push.

Production acceptance remains open for actual devices and Workflows, model quality, real storage/gateway dependencies, system services, joint recovery and physical-fleet capacity. The delivery checklist is authoritative for completion status.
