# 演示素材 / Demonstration media

[中文项目入口](../../README.md) · [English project entry](../../README.en.md)

所有画面来自实际 Admin 和 Control API，使用独立、临时的原生 PostgreSQL。账号、租户、模型、Workflow、12 个排队任务和 3 个完成的示意产物均为合成夹具；没有运行真实 ComfyUI、GPU 推理或实机容量测试。示意产物由本地程序绘制，不能作为模型质量样例。当前 Admin 使用中文。

All captures use the actual Admin and Control API with disposable native PostgreSQL. Accounts, scopes, models, Workflows, 12 queued tasks and 3 completed illustration artifacts are synthetic fixtures. No real ComfyUI execution, GPU inference or physical-fleet capacity test is recorded. Illustrations are drawn locally and are not model-quality examples. The current UI is Chinese.

第 87 增量按 [87 · 青柠](../87-青柠视觉系统与组件复用.md) 从原始源码统一 Admin，重新拍摄全部 20 张截图、4 个 MP4 和 4 个 GIF。17 个页面通过桌面、390px / 320px 布局检查；原字体本地加载，保留实际响应与合成数据边界。

Increment 87 adopts the original 04 Lime source throughout the Admin. All 20 screenshots, four MP4s and four GIFs are refreshed. Seventeen routes pass desktop and 390px/320px layout checks with local original fonts, explicit synthetic data and actual HTTP responses.

## 视频与 GIF / Videos and GIFs

| 素材 / Media | 内容 / Content |
| --- | --- |
| [巡览 GIF / Tour GIF](admin-tour.gif) | 总览、渠道、节点、任务、Workflow、模型、Key、内容 / Overview, channels, nodes, jobs, Workflows, models, Keys and content |
| [巡览 MP4 / Tour MP4](admin-tour.mp4) | 同一实际页面巡览，H.264 / The same actual UI tour in H.264 |
| [Workflow GIF](workflow-lifecycle.gif) | 音频 Workflow 的二次确认、归档、解除归档和发布历史 / Confirmation, archive, unarchive and release history for an audio Workflow |
| [Workflow MP4](workflow-lifecycle.mp4) | 同一连续操作录屏 / The same continuous operation recording |
| [网关目录 GIF / Gateway catalog GIF](gateway-catalog/gateway-catalog.gif) | 六类映射、价格、原请求核对、拒绝、回滚和下架 / Six capability mappings, prices, original-request recovery, rejection, rollback and withdrawal |
| [网关目录 MP4 / Gateway catalog MP4](gateway-catalog/gateway-catalog.mp4) | 同一实际页面连续操作 / The same continuous actual-UI recording |

![Workflow lifecycle demonstration](workflow-lifecycle.gif)

## 截图 / Screenshots

| 文件 / File | 页面 / Page |
| --- | --- |
| [login.png](login.png) | 空白登录页，不录制凭证 / Empty login page; no credentials recorded |
| [mobile-queue.png](mobile-queue.png) | 原始票根组件的移动队列 / Mobile queue using original ticket components |
| [overview.png](overview.png) | 全局队列与 GPU 占用 / Global queue and GPU occupancy |
| [channels.png](channels.png) | ComfyUI 渠道 / ComfyUI channels |
| [nodes.png](nodes.png) | Windows 节点 / Windows nodes |
| [queue.png](queue.png) | 任务与队列 / Jobs and queue |
| [workflows.png](workflows.png) | 六类 Workflow / Six Workflow types |
| [models.png](models.png) | 模型别名与路由 / Model aliases and routing |
| [api-keys.png](api-keys.png) | 多业务 Key / Multiple business Keys |
| [content.png](content.png) | 内容、标签与所有权 / Content, tags and ownership |
| [workflow-confirmation.png](workflow-confirmation.png) | 状态变更二次确认 / Lifecycle confirmation |
| [workflow-history.png](workflow-history.png) | 不可变版本历史 / Immutable release history |
| [mobile-overview.png](mobile-overview.png) | 390px 移动端 / Mobile at 390px |

## 响应与可复现性 / Responses and reproduction

[api-responses.json](api-responses.json) 保存录制期间真实归档/解除归档的请求体、HTTP 状态与响应；认证头、Cookie、CSRF 和登录响应不发布。[capture.json](capture.json) 记录浏览器清理与页面错误；[manifest.json](manifest.json) 记录图片/视频/GIF 的大小、摘要、编码与时长；[隔离环境清理](../evidence/2026-10-25/readme-media-capture.json) 单独记录。

The response file contains actual archive/unarchive request bodies, status codes and responses. Authentication headers, cookies, CSRF and login responses are excluded. Capture/manifest records describe browser cleanup, page errors, file hashes, encoding and duration. Isolated-environment cleanup is recorded separately.

在 `app/`、已准备好的原生开发环境中运行。录制 Python 需要 `playwright==1.58.0` 与本机 Microsoft Edge；可放在独立环境，不必改变服务 Python。`--browser-python` 指向该解释器。脚本仅允许项目所属回环 PostgreSQL，创建并清理唯一演示库，凭证留在忽略目录并在完成后删除。

Run from `app/` in the prepared native environment. The recorder requires `playwright==1.58.0` and local Microsoft Edge. Install it in a separate Python environment if preferred, then pass that interpreter with `--browser-python`. The script restricts itself to the owned loopback PostgreSQL instance, creates/removes a unique demo database and removes private capture credentials.

```powershell
# 示例路径须替换为自己安装了 Playwright 的原生 Python。
./.venv/Scripts/python.exe scripts/capture-readme-media.py --browser-python C:/path/to/media-python/python.exe
# 上一命令输出 private_run；将其作为 --run。
./.venv/Scripts/python.exe scripts/render-readme-media.py --run <private_run>
```

原始 WebM 与日志位于 `app/.local/readme-media/`，不提交。公开 MP4 为无声 H.264、yuv420p、faststart；GIF 使用调色板压缩并循环播放。浏览器三个 context 在 finally 中关闭，不操作用户已有标签页。转换使用锁定的原生 FFmpeg/ffprobe，验证工具摘要并检查输出。

Raw WebM/logs remain in ignored `app/.local/readme-media/`. Public MP4 is silent H.264/yuv420p with faststart; GIF uses a palette and loops. Three owned contexts are closed in `finally`; existing user tabs are untouched. Encoding uses hash-verified native FFmpeg/ffprobe.

## 网关目录增量 / Gateway catalog capture

另增四张截图：[映射编辑](gateway-catalog/catalog-editor.png)、[期望/确认](gateway-catalog/catalog-confirmed.png)、[历史](gateway-catalog/catalog-history.png) 和 [375px 移动端](gateway-catalog/catalog-mobile.png)。[响应](gateway-catalog/api-responses.json) 包含实际 Admin 发布及明确标记的模拟 Provider 回执，不包含身份秘密；[浏览器记录](gateway-catalog/capture.json)、[媒体摘要](gateway-catalog/manifest.json) 和 [临时资源清理](../evidence/2026-10-25/gateway-catalog-browser.json) 分别留证。该增量完成时图库为 15 张图片、3 个 MP4 和 3 个 GIF。

Four additional screenshots show the editor, desired/confirmed versions, history and the 375px layout. Responses contain an actual Admin publication and explicitly simulated Provider receipts, with authentication secrets omitted. The capture, media manifest and isolated-resource cleanup are separate records. At that increment, the gallery contained 15 images, three MP4s and three GIFs. Actual Provider automatic application is validated separately in topic 80; these recordings do not establish GPU or production acceptance.

```powershell
./.venv/Scripts/python.exe scripts/test-gateway-catalog-browser.py --browser-python C:/path/to/media-python/python.exe
./.venv/Scripts/python.exe scripts/render-readme-media.py --catalog --run <private_run>
```

目录录制关闭四个自有 context 及浏览器，原始文件留在忽略的 app/.local/gateway-catalog-browser；登录通过录制前的独立请求完成。回执夹具不连接实际 Provider；[原生跨进程报告](../evidence/2026-10-25/provider-catalog-native-protocol.json) 才记录实际消费者。新增媒体同样核对锁定工具摘要、产物摘要与完整解码。

The catalog recorder closes four owned contexts and its browser. Raw files stay in the ignored app/.local/gateway-catalog-browser directory, and login happens before recording. Receipt fixtures do not invoke the Provider; its separate native cross-process report records actual consumption. New media follows the same pinned-tool, artifact-hash and full-decode checks.

## 确认失败重试增量 / Confirmed failure retry capture

新增 [桌面详情](confirmed-retry/retry-desktop.png)、[375px 手机详情](confirmed-retry/retry-mobile.png)、[取消结果](confirmed-retry/retry-mobile-cancelled.png)、[GIF](confirmed-retry/confirmed-retry.gif) 和 [MP4](confirmed-retry/confirmed-retry.mp4)。页面显示 OOM 原因、1/2 共享预算、最早可分配时间与关闭的来源尝试；验证优先级变更、配额/候选/GPU 口径和取消不触发新生成。长 ID 在手机详情内换行。故障由实际领域/事务生成，数据与 OOM 观察为合成，没有真实 GPU 推理。

Three screenshots and a continuous recording show the OOM cause, shared budget, earliest dispatch time, closed source attempt, priority change and cancellation. Mobile identifiers wrap inside the table. The actual domain/database create the retry state using explicit synthetic data and OOM observations; no physical GPU generation is recorded.

[实际脱敏响应](confirmed-retry/api-responses.json)、[浏览器清理](confirmed-retry/capture.json)、[媒体摘要](confirmed-retry/manifest.json) 和 [隔离资源报告](../evidence/2026-10-25/confirmed-retry-browser-fixture.json) 分别记录；不公开认证头、Cookie、CSRF 或登录过程。两个自有 context/browser、临时 API、数据库和私密配置均已清理。该增量完成时图库共 18 张图片、4 个 MP4、4 个 GIF；第 87 增量已全部重新拍摄，并新增登录页与移动队列截图，共 20 张图片。

Sanitized responses, capture cleanup, media hashes and isolated-resource cleanup are recorded separately. Authentication material and login are excluded. Both owned contexts, the browser, temporary API/database and private configuration are removed. At that increment, the gallery contained 18 screenshots, four MP4 files and four GIF files. Increment 87 recaptures them all and adds login/mobile-queue screenshots, bringing the current total to 20 images.

```powershell
./.venv/Scripts/python.exe scripts/test-confirmed-retry-browser.py --browser-python C:/path/to/media-python/python.exe
./.venv/Scripts/python.exe scripts/render-readme-media.py --confirmed-retry --run <run_id>
```

原始录屏在忽略的 app/.local/confirmed-retry-browser/<run_id>/recordings；转换只接受对应运行目录，核对锁定工具摘要，并完整解码公开 MP4/GIF。Raw recordings remain under the ignored owned capture run; conversion validates ownership and pinned tools, then fully decodes both outputs.

run_id 从 docs/evidence/<日期>/confirmed-retry-browser-fixture.json 读取。Read run_id from the dated browser-fixture evidence file.

## 第 94 增量等待估算 / Increment 94 queue estimates

六张新截图和原速录屏展示任务详情的等待区间、样本不足、过期和关闭面板。20 条运行样本与占用均为合成夹具，页面、管理会话和 HTTPS API 请求实际执行；不构成真实 GPU 生成或容量验收。三种尺寸共 10 项检查，测试浏览器、临时 API 与数据库清理通过。

Six new screenshots and an original-speed recording demonstrate conditional waits, insufficient samples, expiry and drawer closure. Twenty runtime samples and occupancy are synthetic fixtures; the UI, Admin sessions and HTTPS requests actually run. Ten checks cover three widths and verified resource cleanup. This is component evidence, not physical inference or capacity acceptance.

| 宽度 / Width | 区间 / Interval | 未知 / Unknown |
| --- | --- | --- |
| 1440px | [Desktop](wait-estimates/wait-estimate-1440.png) | [Unknown](wait-estimates/wait-unknown-1440.png) |
| 768px | [Tablet](wait-estimates/wait-estimate-768.png) | [Unknown](wait-estimates/wait-unknown-768.png) |
| 390px | [Mobile](wait-estimates/wait-estimate-390.png) | [Unknown](wait-estimates/wait-unknown-390.png) |

[MP4](wait-estimates/wait-estimates.mp4) · [GIF](wait-estimates/wait-estimates.gif) · [Actual responses and capture](../evidence/2026-10-26/wait-estimate-94-bounded/summary.json) · [Media hashes](wait-estimates/manifest.json)

![Conditional wait and unknown state](wait-estimates/wait-estimates.gif)
