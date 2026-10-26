# 更新日志 / Changelog

## 2026-10-26 · Increment 99 / 数据库等待分类与并发复验

- 修复一次性双测试库观测器的 I/O 等待信息丢失：增加按类型白名单的 wait_event，保留原 lock_event；同后端不同 I/O 阶段分别聚合，未知和错配事件仍为 other。原只读/范围/内存/语句预算和业务行为保持。
- Preserve the 23 expected before-fix failures and source-bound 73-check native PostgreSQL/ASGI and unit verification. The [scope and verification](docs/99-原生数据库等待分类与并发复验.md) keep synthetic observations distinct from actual I/O evidence. [Final verification](docs/evidence/2026-10-26/database-waits-verification-99.json) passes all 16 gates/3938 Python tests (702.257 seconds within the unchanged 1200-second budget) and 29 native scenarios entirely within that Python gate. All 633 CI/273 native inputs match commit 21d6ac2; 1585 bounded samples capture the injected locks but no slow I/O. Owned native resources are cleaned up. Candidate 97 is retained with its historical observer source manifest; no new installation/browser acceptance is claimed. Historical roots and all 69 full-acceptance items remain open.

## 2026-10-26 · Audit 98 / 当前项目与目标复核

- [最新审计第 8 节](docs/95-项目现状审计与目标产物差距.md) 重新核对实际代码、17 个 Admin 路由、66 运行契约、OpenAPI、原青柠来源、632 CI/521 候选输入及实际发行 ZIP 摘要；168 个 Python 文件的有界 AST 扫描未发现 pass-only/NotImplementedError 函数。静态/摘要核对不替代业务与实机验收。
- Keep all 69 remaining exits explicit: 67 first-release and two optional; 0/6 actual Workflows, physical capacity and production remain pending. Existing 16-gate/3915-test CI, 29 installation and ten installed-browser checks are historical source-matched evidence, not new executions.
- 保留两份同日诊断：20 线程/500 次真实鉴权得到 130 成功、370 合法限流、无非预期失败；保留库的独立克隆耗时 288.686 秒，45 表清理通过并回滚，随后移除克隆。原库未修改、预算未提高；原始并发/冷状态未复现，历史根因继续开放，本地诊断驱动尚未发布。
- Synchronize bilingual navigation and correct the current English gallery count to 30 PNG/six MP4/six GIF. No application, contract, UI or acceptance flag changes; no new browser, GPU run or virtualization.

## 2026-10-26 · Increment 97 / 发行来源与审计状态

- 新构建从昂贵步骤前固定源输入，要求真实 Git commit、普通已跟踪文件及 HEAD blob 字节匹配，构建结束前再次核对来源；拒绝未提交/未跟踪/忽略输入、Git stat-cache 隐藏变化和构建期间漂移。旧 null 清单读取保持兼容。
- Bind new release manifests to the actual source commit. Ninety native Git/archive checks pass; this does not approve real Workflows or production.
- 只读 DOM 测量修正 UI-96-01 的判断：原截图主要是异步状态入场动画中间帧。取消该状态动画并补正文留白/换行，保留原青柠源文件。19 项三宽度检查覆盖未启用、3650 天、读取失败、旧快照、键盘焦点及 reduced-motion。
- Preserve both failed UI-fixture runs caused by an incorrect Playwright callback signature. The corrected run closes all three contexts and its owned process tree. Add four screenshots and one original-speed MP4/GIF; the gallery now has 30 PNG, six MP4 and six GIF. [Final verification](docs/evidence/2026-10-26/release-provenance-verification-97.json) passes all 16 gates/3915 Python cases, 29 exact-installation checks and ten installed-browser checks. Candidate rc-20261026.97.1 embeds source commit 31f3798; all 632 CI inputs and 521 candidate inputs match. Python takes 520.229 seconds within the unchanged 1200-second budget. Local REL-96-01/UI-96-01 follow-ups close; all 69 full-acceptance items remain open. Audit 95 section 7 is rechecked against current code and new versioned audit evidence, retaining all original history and numbered exit conditions.

## 2026-10-26 · Verification 96 / 当前源码完整验证与原生候选

- 对不变的 628 个应用输入完成新一轮原生 CI：16 门禁/3899 Python 全通过、零失败/错误/跳过，595.039 秒且保留原 1200 秒预算；本轮没有手工数据库维护，旧库与历史失败保留。
- Build rc-20261026.96.1 from 518 matching inputs: 169 payload files, 58 wheels and 66 runtime contracts. Inspect actual wait-estimate wheel members and Admin bundle; reuse the unchanged pinned Provider binary with rechecked source hashes.
- 精确候选通过 29 项全新隔离安装和 10 项桌面/移动浏览器检查；4 个应用角色正常停止、2 个 context/browser 关闭、临时库/目录清理。补充两张安装后截图；既有 26 PNG/5 MP4/5 GIF 演示图库保持。
- [Versioned evidence and limitations](docs/96-当前源码完整验证与原生发行候选.md) fill audit 95 local release gap R05 without promoting physical Workflow, fleet, SCM or production acceptance. Keep 69 full-acceptance items open. Record REL-96-01 for manifest Git-revision embedding and UI-96-01 for visible mobile audit-badge clipping; neither is claimed fixed. Synchronize bilingual entries and change notes.

## 2026-10-26 · Audit 95 / 现状与目标差距审计

- [系统审计](docs/95-项目现状审计与目标产物差距.md) 逐项列出 69 个剩余出口及顺序：67 首版、2 可选；73 条需求、98 完整门禁和 15 工作包保持各自实现/验收口径，0/6 真实 Workflow 未提升。
- Inspect current code, Admin routes, Worker/GPU/journal/storage boundaries, source-bound evidence, contracts, release provenance and media. Separate current focused checks from increment 92 full CI/candidate; preserve concurrent 502, audit 503, CI/test-database root causes and strict official Video limitations.
- 修正完整清单中的过时“当前”端点/工作包说明和英文媒体导航数量，更新中英 README 与索引；本轮只读门禁结果另存版本化证据，应用输入保持不变。
- Documentation audit only: no functional changes, historical test reruns, browser or virtualization, database changes, service installation or new production acceptance. Commit and synchronize the audit and change notes to the authorized remote.

## 2026-10-26 · Increment 94 / 队列等待估算

- Admin 任务详情新增按需等待区间、样本数量、采样范围和 15 秒过期提示；样本不足、观测过期和执行不确定时显示未知。保持 04·青柠原始资源与共享主题。
- Add read-only allocation-wait scenarios using matching successful slot-runtime samples, current physical GPU occupancy, tenant fairness, concurrency quotas, retry readiness and deadlines. Four conditional P50/P95 scenarios preserve the distinction between elapsed wait, allocation, generation and upload.
- 新领取记录不可变等待依据摘要；发布、规格、GPU、渠道 revision 或批准变化时不复用旧样本。无数据库迁移或历史回填；既有 journal、fence、重放、配额和释放规则保持。
- Freeze the Admin response and synchronize runtime contracts, endpoint catalog, OpenAPI and frontend types. Add native PostgreSQL/claim regressions and three-width actual UI capture, including expiry, unknown state, focus, visibility and request cancellation.
- 新增 6 张截图、原速 MP4/GIF 和实际响应记录；[浏览器捕获](docs/evidence/2026-10-26/wait-estimate-94-bounded/summary.json) 通过 10 项检查并清理全部本次资源。旧测试库及失败现场保留；本轮专项与历史完整 CI 分开，真实等待误差/六类资源/生产和 69 项完整出口继续开放，见 [94](docs/94-队列等待估算与运行样本.md)。

## 2026-10-26 · Increment 93 / 六类 Workflow 离线发布准备

- 新增原生 `workflow-package.py build/verify`：基于明确指定的现有 ComfyUI 和六类发布草稿，流式计算模型 SHA-256、固定本地 Git 版本，输出可直接导入现有 Admin 的 manifest、来源及校验和。
- Prepare all six Workflow capabilities from existing local dependencies. Preserve the frozen Admin import format, clear old runtime approvals, verify package integrity and optionally detect actual dependency drift. No model downloads, node installation, generation or database changes.
- 拒绝覆盖、依赖越界/junction、脏仓库、依赖重复、模型读取变化、篡改和伪造离线批准。修正首轮原生 Windows ctime 接口差异与 Git 系统换行策略造成的误判，保留失败记录。
- 70 focused checks pass, covering six-capability import/compilation and actual native CLI/Git execution. Keep this scope separate from previous full CI and physical-resource acceptance. Update bilingual usage and productization guidance; UI/media stay at their recorded revision. All 69 full-acceptance exits remain open.

## 2026-10-26 · Increment 92 / 直接视频接口冻结投影

- 直接视频创建、重放和详情使用冻结 seconds/size；毫秒精度、旧字段省略、畸形元数据固定 503、16 种状态和安全终态错误均有回归。Location 与 poll_url 对齐，当前权限和跨主体隔离继续生效，列表保持轻量。
- Preserve frozen direct-video metadata, exact durations, safe errors and scoped native extensions without changing job/GPU/accounting state. Keep strict official Video-type limitations explicit; see [92](docs/92-直接视频接口冻结投影与权限边界.md).
- 新增直接视频响应契约；累计 62 schema、1022 fixture（245 正/777 反）和 65 运行契约。发行验证器在第 65 份契约处的真实失败保留，修正为 128 项有界上限，继续核对全部成员及摘要。
- Retain the failed candidate and build a new release ID. README verification defaults to read-only; explicit reports create new evidence files and refuse overwriting prior results. Source-bound runs pass 28 video and 74 release-security cases. [Final verification](docs/evidence/2026-10-26/direct-video-verification-92.json) passes all 16 gates/3790 Python tests, 39 SDK checks/24 native scenarios, 29 exact-candidate installation checks and ten browser checks. Candidate rc-20261026.92.2 binds 508 source inputs, 169 payload files, 58 runtime wheels and 65 contracts. Preserve both CI timeouts, the bounded statistics experiment and native observation failure; no budget was raised.
- 再次直接下载 04 青柠完整源码并核对原 CSS、字体及组件摘要；运行入口继续导入原 CSS。来源证据与双语 README 同步，界面未变化，沿用现有截图/录屏/GIF。Direct source retrieval matches the locked original assets; owned test browsers and temporary resources are closed. Actual resources, production and all 69 remaining full-acceptance items stay open.

## 2026-10-26 · Increment 91 / 多媒体 SDK 与视频冻结规格

- 双语 README 突出当前方案与原始青柠源码，历史增量证据折叠保存；服务启停顺序归入运维段落，完整设计与使用内容保留。Organize both entry points around current behavior, original Lime sources and usage, retaining historical evidence in a collapsible section.
- Provider 视频创建/查询返回原请求冻结的 seconds/size，失败/取消/到期仅返回固定安全错误；向前迁移 258 与预留事务保存不可变元数据，旧行未知字段不猜测，不在客户查询中解密请求。
- Preserve frozen Provider video specifications and safe terminal errors. Keep unknown progress absent, legacy metadata unknown and all original authentication, accounting and GPU protections.
- 扩展固定 Python 3.37.0 / Node 7.43.0 的视频、多参考视频与音频统一任务/内容验证。严格官方 Video 类型的进度/尺寸限制明确保留；不关闭严格验证，不声明 /audio/speech。
- Exercise pinned SDK multimedia paths using the project response contract. Preserve actual regression/SDK failures and scoped cleanup evidence. Direct control video projection parity, actual resources and the 69 remaining exits stay open; see [91](docs/91-多媒体SDK与视频冻结规格.md).
- [91 最终验证](docs/evidence/2026-10-26/multimedia-sdk-verification-91.json)：Go/PostgreSQL 176 顶层/342 子测试、1703 项 Python 专项、39 SDK 检查/24 原生场景、完整 16 门禁/3713 Python、28 精确候选安装及 10 浏览器检查通过。611 CI/274 原生/505 候选输入一致；候选 rc-20261026.91.1 保留未生产签收标志，169 载荷/58 wheels/64 运行契约。
- Preserve the first CI budget failure and one controlled statistics-only test-database experiment. The successful Python gate takes 954.897 seconds under the unchanged 1200-second budget; this does not establish the complete historical cause. All owned verification processes, browser contexts and temporary databases/directories are cleaned up. Original Lime assets remain directly imported; no UI change or replacement recording is introduced.

## 2026-10-26 · Increment 90 / 固定 SDK 客户端兼容

- 使用实际 OpenAI Python 3.37.0 / Node 7.43.0 SDK 测试本次所属 loopback Provider，覆盖模型发现、图片生成与单/多图编辑、请求关联、错误、幂等、冲突重试和显式异步扩展；不调用 OpenAI 公网，不使用真实客户 Key。
- Exercise actual pinned SDK clients against the owned local Provider. Keep SDK dependencies in an explicit development group/isolated Node directory; production retains 58 runtime wheels without these SDK dependencies.
- 严格解析复现普通模型条目缺少标准字段：仅补缺省 object=model、created=0、owned_by=sub2api，保留已有正确字段和扩展；原有无效字段继续拒绝。新增模型列表契约及 12 个正/反例，63 运行契约同步。
- Normalize absent stock model metadata while preserving extensions and rejecting invalid values. Preserve Codex and unowned single-model passthrough behavior. Add a machine-readable model-list contract and versioned component/build reports without rewriting historical evidence.
- 保留四次 SDK 失败及修正、旧 CI 因改源码而主动中断的记录；第五轮 24 项原生场景与 20 项 SDK 检查通过，只新增预期的 6 个任务和计量记录，所属临时资源清理通过。
- Retain failed attempts and the interrupted CI scope. The successful SDK run verifies the internal authorized delivery path; it does not accept the public TLS frontend, real Workflows, storage/Redis, physical capacity or the complete client matrix. Item 30 and all 69 remaining exits stay open; see [90](docs/90-固定SDK客户端兼容矩阵.md).
- 最终验证：206 项 Python 专项、173 个 Go 顶层/331 个子测试、完整 16 门禁/3696 Python、28 项候选安装及 10 项安装后浏览器通过；606 CI/271 原生/501 候选源输入与 73 个 overlay 摘要已核对。63 运行契约、60 schema/959 fixture 同步，原始青柠 5 文件/2 字体保持。候选 rc-20261025.90.1；所属测试资源已清理，原生 PG 与已有开发预览保留。
- 首次完整运行的 3696 项 Python 无失败/错误/跳过，但触发原 1200 秒 CI 截止时间，整轮保持失败。受控普通测试库维护在第 10 表触发原语句预算并停止，保全核对通过；随后仅刷新 45 表统计，可见行数、filenode 和触发器保持，复验沿用原预算。
- Preserve the actual CI budget failure and the bounded maintenance failure. A subsequent explicit statistics-only refresh does not establish root cause or complete CI acceptance; no automatic maintenance or higher timeout is introduced.
- Final validation passes all 16 gates, including 3696 Python cases in a 1082.096-second gate under the original 1200-second budget. Exact source/binary/candidate bindings, installed-browser cleanup and remaining acceptance limits are recorded in [verification 90](docs/evidence/2026-10-25/openai-sdk-verification-90.json).

## 2026-10-25 · Increment 89 / 审计分页投影上限

- 真实 PostgreSQL 回归证明第二页、末页和空页会对被跳过的记录执行详情转换；修复为 MATERIALIZED 分页后再做 16 KiB SQL 脱敏投影，保留计数、稳定顺序、只读快照和超时预算。
- Bound expensive audit-detail projection to the selected page. Keep response filtering in SQL, preserve authorization and consistent pagination, and retain the failing regression as evidence.
- 需求证据支持显式绑定新 Python 运行的具名测试和源码；保留旧报告，拒绝未执行测试、源码漂移、失败/跳过和错误扩大验收范围。
- Bind changed implementations to a fresh, scoped Python execution without rewriting historical reports. Historical complete timeout causes, physical-resource acceptance and the 69 remaining items stay open; see [89](docs/89-审计分页投影上限与版本化复验.md).
- 验证/Validation：179 项专项、完整 16 门禁/3671 Python、28 项精确候选安装及 10 项安装后浏览器通过；600 项 CI 输入、499 项候选源输入及新证据绑定已核对。候选 `rc-20261025.89.1`，169 载荷/58 wheels/62 契约；所属测试资源清理完成，原生 PG 与受控更新后的开发预览保留。See [verification](docs/evidence/2026-10-25/audit-page-projection-verification.json); actual resources and complete release acceptance remain pending.

## 2026-10-25 · Increment 88 / 需求实现与分层证据追踪

- 将 73 条需求、98 项验收逐条关联实现、99 个具名测试、6 份历史报告和剩余稳定待办，明确六类生成的框架与真实产品化边界；保留 79 的原始状态，修正 Provider/Admin 尚未接线的过时结论。
- Map every requirement and gate to implementation, named tests, historical evidence and remaining exits. Preserve old status records, distinguish six capability frameworks from actual Workflow products, and correct stale Provider/Admin integration status.
- 新增只读有界校验器并接入 docs 门禁：核对 141 个引用代码/测试输入、报告摘要、真实执行测试名、编号全集和可读矩阵，拒绝路径越界/目录联接、漂移、缺项和错误提升验收。
- Add a bounded offline audit to the documentation gate. Verify referenced inputs, report hashes, executed test symbols and complete mappings; reject unsafe paths, drift, missing links and false acceptance. Export sanitized historical Python/installed-UI provenance without raw logs or credentials.
- 稳定待办 12 的追踪出口关闭；共 7 项完成、69 项开放（首版 67、可选 2）。历史 3593 Python、Go、浏览器及协议负载证据按声明范围复用，没有重跑或提升真实资源/生产标志。未改运行接口/数据库/UI，不更新旧候选或重复录屏；专项及文档校验见 [88](docs/88-需求实现与分层证据追踪.md)。
- Item 12 closes locally; 69 items remain. Historical runtime evidence is reused within its original scope. Runtime APIs, database and UI are unchanged; actual-resource and complete release acceptance remain pending. Synchronize both READMEs, navigation, status records and this changelog.
- 验证/Validation：57 项专项、92 篇文档/59 schema/947 fixture、62 运行契约与双语入口/现有 20 PNG、4 MP4/4 GIF 核验通过；保留首轮 Windows 换行误报，见 [88 验证](docs/evidence/2026-10-25/development-trace-verification.json)。These are new focused/offline checks; historical full Python, Go, browser and native runtime suites were not rerun.

## 2026-10-25 · Increment 87 / 原始青柠视觉系统复用

- 按用户指定直接拉取 html.cafe/x5b381ea1 的 04 · 青柠源码，原样保存第四套 CSS 和字体声明，登记完整页面、片段、字体及许可证摘要；原始文件与适配层分开维护。
- Pull and retain the original 04 Lime CSS and embedded font declarations. Reuse source variables, tickets, cards and typography across the Admin; keep a bounded pull/check tool and provenance lock.
- 统一所有页面、登录、导航、表格、表单、状态和弹层。原字体原样解码为本地 WOFF，保持现有 CSP；构建附带完整 OFL 许可，并锁定构建配置所需的 Node 类型。
- Share one Element Plus adapter, preserve semantic status text, keyboard focus, narrow layouts and reduced-motion behavior. Store the continuing visual constraint in AGENTS.md and [87](docs/87-青柠视觉系统与组件复用.md).

- 验证/Validation：原始 5 文件与 2 字体摘要一致；40 前端、71 发布安全、17 页面三种宽度、目录 16 项、重试 8 项、最终候选 28 安装 / 10 浏览器通过；20 PNG / 4 MP4 / 4 GIF 全部刷新并完整核验。候选 `rc-20261025.87.2` 对应 499 输入 / 169 载荷 / 58 wheels / 62 契约。
- Preserve original CSS/license whitespace, source hashes and failed attempts. Exact original WOFF bytes load locally under the existing CSP. All owned test browsers/resources close; full Python/native protocol suites were not rerun because backend inputs are unchanged. The 70 backlog items and physical/production acceptance stay open; see [87 verification](docs/evidence/2026-10-25/lime-source-visual-verification.json).

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
