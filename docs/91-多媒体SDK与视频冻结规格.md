# 91 · 多媒体 SDK 与视频冻结规格

后续进展：本增量的结果描述第 91 时点；直接控制 API 视频投影缺口在 [92](./92-直接视频接口冻结投影与权限边界.md) 继续实现和验证。第 91 的报告、失败记录和未签收标志保持原样。

2026-10-26。关联完整待办 30～33、DEV-05/11/12/14/15/18/24/25/26/27/29/30/31/32。以下保留先登记的实现缺口、约束及各轮真实结果；最终结果见末段与 [汇总证据](./evidence/2026-10-26/multimedia-sdk-verification-91.json)。

## 变更约束

当前 Provider 视频创建/查询投影遗漏文档 03 已约定的 seconds、size，以及失败/取消/到期的安全错误对象。必须使用原请求冻结规格，不能按当前模型默认值重建旧响应；不得为补齐字段在客户查询中调用内部 Recover 或解密完整请求。

- 上游新增向前迁移 258，为 intent 保存可空、不可变的视频请求毫秒整数和尺寸。新请求在规范化、应用原版本默认值后，与预留/加密请求同一事务写入；输出 seconds 时精确转换为字符串。已有行保留 null，不猜测或批量解密旧请求；旧写进程升级前排空。已知秒数/尺寸才输出，旧行或未冻结尺寸明确保持缺失。
- 视频创建和状态查询复用冻结元数据；失败/取消/到期仅输出固定安全错误码与说明，不公开 prompt、节点、凭证或驱动文本。整体进度未知时继续省略，不伪造百分比；图片/音频任务与原账本、GPU、重试、鉴权和时间预算不变。
- 公开视频请求仍受原 120 秒上限及已发布 Workflow 离散时长约束；内部账本 86400 秒边界回归和历史响应契约上限不代表对外放宽时长。
- 固定 Python 3.37.0 / Node 7.43.0，实际验证视频创建/恢复/状态/内容和音频统一任务/恢复/内容。音频仍为 /v1/jobs 扩展，不增加 /v1/audio/speech。严格验证已冻结的项目视频响应；官方 SDK Video 的枚举/必填进度与本项目规格不一致时保留明确的不兼容事实，不关闭严格验证掩盖差异。
- 仅运行本次拥有的原生 loopback 服务、临时 PostgreSQL 和合成 ComfyUI/Redis/媒体；不请求 OpenAI 公网、不使用其他项目设备、不使用 Docker/WSL/VM。测试原始日志和凭证留在 app/.local，公开证据使用新目录，清理自己的进程/连接/数据库/素材。

## 验证出口

先以实际 Go/PG 回归证明遗漏，再验证冻结规格、默认值变更、重放、非视频隔离、数据库不可变及畸形数据拒绝；实际两个 SDK 对视频/音频执行 HTTP、幂等、授权下载/Range、字节摘要与唯一计量检查。执行匹配的契约、静态、类型、完整 CI 和精确候选安装，核对源码与发布范围后同步双语入口、更新说明及远端。

本机协议和合成媒体不能代替真实 Workflow、播放质量、S3/Redis、生产与 10～20 台设备验收；完整待办继续按各自出口追踪，不因本轮通过关闭整个 30～33 或完整首版。

## 已执行的组件回归

改动前的实际 Go/PostgreSQL 回归复现 Provider 视频响应遗漏规格，见 [原始回归报告](./evidence/2026-10-26/multimedia-sdk-go-before-91/sub2api-ledger-tests.json) 与 [脱敏说明](./evidence/2026-10-26/multimedia-sdk-before-91.json)。首次修正后保留两处新增测试夹具错误，以及既有并发退款测试在客户行锁上的 lock_timeout；见 [初次失败记录](./evidence/2026-10-26/multimedia-sdk-go-initial-failures-91.json)。后者没有完整阻塞链，不能声称已定位历史 rate_states 或审计超时根因。

修正夹具后，[完整 Provider 组件复验](./evidence/2026-10-26/multimedia-sdk-go-corrected-91/sub2api-ledger-tests.json) 通过 176 个顶层测试和 342 个子测试；保留预期的进程边界辅助测试跳过，临时数据库已清理。未增加原并发、时间预算或调整 PostgreSQL 全局设置。验证包括精确毫秒、已知尺寸、默认值切换后重放、请求密文不可用时独立读取、旧行未知字段省略、安全终态错误、非视频隔离及数据库拒绝修改。新上游累计 309 次迁移、112 表；控制面仍为 45 表/head 3da02e7b5941。

本轮响应契约专指 Sub2API Provider 的 rc_ 视频任务。直接控制 API 的 job_ 视频响应仍缺少同等顶层规格与统一安全错误投影，保留为待完善项；不能从 Provider 的通过推定两个入口完全一致。严格 Python 官方 Video 类型要求 progress，且不接受项目测试规格 1024x1024；通用 SDK 传输加项目 JSON Schema 是显式扩展用法，官方类型完整兼容仍不成立。

## 客户端用法与升级

沿用 [90 的固定安装和运行命令](./90-固定SDK客户端兼容矩阵.md)，显式执行 --openai-sdk 即运行当前多媒体矩阵。入口必须指向自己的实际 Provider /v1，业务 Key 不用于 Admin；max_retries=0，首次请求前保存 Idempotency-Key，恢复原操作时复用。视频尺寸、时长和参考角色只能使用实际发布模型允许的值。

视频 SDK create 可负责 multipart 传输，但项目响应须使用 [sdk-video-response.schema.json](./contracts/sdk-video-response.schema.json) 校验。严格 Python 官方 Video 解析的已知拒绝不能解释为任务未创建；应保留原请求键和任务 ID，用 SDK 通用 JSON 方法恢复/查询，完成后使用视频内容下载方法。Node 类型声明不提供运行时校验，调用方同样需要验证实际 JSON。下载以及 Range、HEAD 继续使用原业务 Key。

多参考视频通过 workflow_options.references 显式保存有序 asset_id/role；先用 /assets 上传自己的图片，不把私有文件路径作为外部资源。音频使用 /jobs 的 text_to_audio，轮询原任务并从其 artifacts 获取内容 ID，再走 /content/{id} 与 /download；没有 /audio/speech 同步语音接口。音频毫秒按实际验证的产物结算，不能用请求上限代替实际时长。

升级 Provider 前核对备份可读、排空旧写进程，再向前执行 258 和启动新二进制。已有视频行保留未知，不回填猜测；新行规格在预留事务中冻结。回退应用必须评估旧版本是否丢失新响应字段，不能删除列/触发器或改写旧任务来制造兼容。

首轮实际 SDK 演练在 Python 视频 HEAD 调用处发现请求选项类型错误；前面的创建、冻结规格校验、重放、严格类型预期拒绝和内容下载已经执行。保留 [原运行](./evidence/2026-10-26/multimedia-sdk-native-91-first/openai-sdk-native-protocol.json) 和 [失败说明](./evidence/2026-10-26/multimedia-sdk-native-first-failure-91.json)。修正为固定 SDK 的 FinalRequestOptions 对象，不修改正式接口或放宽验证；原有预算保持。

第二轮在 Python /assets 上传得到 400 invalid_request：[原运行](./evidence/2026-10-26/multimedia-sdk-native-91-second/openai-sdk-native-protocol.json) / [失败说明](./evidence/2026-10-26/multimedia-sdk-native-second-failure-91.json)。固定 SDK 的通用 post(files=...) 需要显式 multipart/form-data，与其生成的文件资源方法一致；否则仍带默认 JSON 类型。补齐后由 SDK 自行生成 boundary，使用实际 SDK 的离线传输验证了编码，再进行原生复验。两次失败均清理所属进程、数据库、观测线程、凭证与媒体。

## 实际 SDK 结果

[第三轮原生报告](./evidence/2026-10-26/multimedia-sdk-native-91-third/openai-sdk-native-protocol.json) 通过 24 场景、Python 21 项与 Node 18 项检查，共 39 项。该数字包含严格官方 Video 类型在 progress/size 上的预期拒绝，不能称作 39 项官方接口完全兼容。Python 保持严格验证，Node 5 份实际视频 JSON 再通过项目 Schema 校验。

| 验证 | 实际结果 |
| --- | --- |
| 视频提交/恢复 | 两个 SDK 的 multipart 创建、JSON 原键恢复及已知冻结 seconds=5/size=1024x1024 一致 |
| 视频内容 | 实际 SDK 二进制方法、完整 SHA-256、Range 206 与 HEAD 长度通过 |
| 多参考视频 | 各上传两个自有素材，按 character/scene 有序发送，原键重放、任务完成与内容摘要通过；实际参考质量仍待真实 Workflow |
| 音频任务/内容 | /jobs 扩展原键恢复、完成、单一产物、WAV 元数据/完整摘要/Range/HEAD 及终态取消不改结果通过 |
| 唯一性与计量 | 新增任务、意图、用量日志各 12；6 图片、4 视频/20000 毫秒、2 音频/1000 实际毫秒，跨媒体未混记 |
| 保留与清理 | 23 个确认包文件及至少 3600 秒保留通过；仅测试收尾移除。进程、数据库、观测线程、凭证与素材已清理 |
| 来源 | 274 个原生运行输入前后不变，逐个与当前字节核对；Provider 精确构建与实际二进制绑定 |

[1703 项 Python 专项](./evidence/2026-10-26/multimedia-sdk-focused-91.json) 覆盖冻结契约、SDK 边界与发布安全；它在 HEAD 调用修正前执行，不代替上述真实 HTTP 或完整 CI。运行契约 64 份、61 schema/976 fixture 同步，新增 17 个视频正/反例。所有客户端只调用自有 loopback，未调用 OpenAI 公网、真实 ComfyUI/Redis/S3 或浏览器。

## 完整 CI 的预算失败与受控环境实验

首轮 ci91-20261026-multimedia-sdk 在 Python 阶段约 89% 时触发原 1200 秒预算，没有完整 JUnit；退出码为 0 也不改变 timed_out=true 的失败。后四道门禁未执行。已保留 [原 CI](./evidence/native-ci/ci91-20261026-multimedia-sdk/summary.json) 与 [失败摘要](./evidence/2026-10-26/multimedia-sdk-ci-budget-failure-91.json)，611 个输入与源码一致，所属进程树关闭。独立只收集确认共有 3713 项，不将收集数当作通过数。

持有原 CI 文件/数据库互斥的 [只读检查](./evidence/2026-10-26/multimedia-sdk-test-db-observation-91.json) 确认专用 render_control_test 的 45 表、35 条可见合成行；估计死元组 228546，距上次分析已有 206897 次修改，主要统计约 7552 秒未刷新。这说明统计存在陈旧状态，不能单凭相关性断言超时根因。

按 [86 的受控测试库边界](./86-数据库故障脱敏诊断与并发观测.md) 进行一次显式 [仅刷新统计](./evidence/2026-10-26/multimedia-sdk-test-db-analyze-91.json)，不清除数据或改写表文件，不调整全局配置、CI/请求预算或逐例重置，不加入每轮 CI 自动维护。前后可见行数、filenode 与触发器摘要一致；原失败继续保留。后续复验仍使用原 1200 秒限制，不能把一次环境实验或通过等同于历史完整根因已解决。

[固定后台设置的只读核对](./evidence/2026-10-26/multimedia-sdk-test-db-background-settings-91.json) 确认 autovacuum / track_counts 均为 on、没有表级 reloptions，观察时存在自动维护 launcher。未改动这些配置；瞬时观察不能证明后台维护的完整行为或超时归因。

## 最终门禁与精确候选

[完整 CI 复验](./evidence/native-ci/ci91-20261026-multimedia-sdk-analyzed/summary.json) 的 16 门禁全部通过，3713 个 Python 测试零失败、错误、跳过。Python 门禁为 954.897 秒，保持原 1200 秒预算；611 个输入运行前后和与当前代码一致，所有所属进程树关闭、原始日志完整。该结果证明本次复验通过，不撤销首轮超时或宣称历史完整根因已解决。

| 验证层 | 精确范围与结果 |
| --- | --- |
| [源与二进制绑定](./evidence/2026-10-26/multimedia-sdk-source-verification-91.json) | 611 CI、274 原生、75 overlay/生成文件及 73 维护文件逐一核对；Go、构建和实际 SDK 使用同一 Provider 二进制 |
| [候选构建](./evidence/2026-10-26/multimedia-sdk-release-build-91.json) | rc-20261026.91.1，505 源输入、169 载荷、58 wheels、64 运行契约；契约 wheel 与领域 wheel 的 JSON 合计逐字节匹配全部 64 契约，生产包不包含 openai/httpx2 SDK |
| [离线安装](./evidence/2026-10-26/multimedia-sdk-release-install-91/native-release-installation.json) | 同一 ZIP 的 28 项检查通过；独立解释器、备份先于迁移、恢复校验、HTTPS、原生服务入口及篡改拒绝均从安装包执行 |
| [安装后浏览器](./evidence/2026-10-26/multimedia-sdk-release-install-91/native-release-installed-browser.json) | 桌面/手机共 10 项通过，实际加载原始青柠样式、本地字体与许可证；两个 context 和所属 browser 已关闭 |
| 清理与保留 | 四应用入口正常退出且清理前无残留子进程，临时数据库/目录移除，cleanup_errors 为空；既有 9 个登记开发预览进程与原生 PG 保留供本地预览 |

候选 SHA-256 为 `b921624d5f0c64016e591ada80b46b6b75a1f37258375026aa2ececa6dfa98c2`，281315902 字节；Provider SHA-256 为 `20a022de876e245bb76d4fad0febc12469f85a155d32c742d903539fb7f465fb`。包内 manifest 保持 production_accepted=false，不把应用入口通过提升为 WinSW/SCM、网关监督器、生产、真实媒体质量或物理设备验收。

本轮没有界面代码/资源变化，继续直接导入 [87 已拉取的原始青柠 CSS 与字体](./87-青柠视觉系统与组件复用.md)，原始来源 5 文件/2 字体校验通过。沿用现有 20 PNG/4 MP4/4 GIF；候选安装的两张新截图仅作为安装证据。[完整清单](./78-完整待办与首版收口清单.md) 仍有 69 项开放，直接控制视频投影一致性与官方严格 Video 类型的限制明确保留。
