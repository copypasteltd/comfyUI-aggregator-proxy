# 33 · Web混合媒体跨浏览器备份恢复

证据148继续使用线上 **canvas-20261030.146.1**，产品代码为`db56cae3634da0361074e84024ccd5b428d189fa`。仅建设和验证Web端；本轮没有改产品源码、重新构建或部署，也没有修改原API、Worker、调度或服务配置。完整签收仍为 **HOLD/pending-test**。

本轮通过实际网页下载和文件选择完成 **Firefox → Chromium → Windows WebKit → Firefox** 四步链路，共34组检查通过。Windows WebKit四份音视频的原生解码失败，明确不计播放通过；备份中的原件仍完整，最终回到Firefox后四份均正常载入。Windows WebKit不等同于实际Safari，前轮Cookie隔离问题也仍未签收。

## 用户如何迁移

1. 在原浏览器、原访问地址打开画布库，使用作品卡片上的“导出”，保存完整ZIP。重要任务另存任务JSON；两者用途不同。
2. 在目标浏览器或新域名/端口打开画布库，点击“导入画布”，选择原ZIP。不同源站的本机数据库不会自动共享。
3. 打开导入作品，核对图片、音视频、GLB和连线，再刷新网页复查。原任务只保留离线说明，不会因为导入重新生成。
4. 需要生成或查询原任务时，重新连接原API并填写自己的业务Key。ZIP不带连接凭证，也不授予原任务权限。
5. 确认目标作品及独立备份可用后，再决定是否清理旧浏览器数据。当前导入不会替用户删除旧数据；本机保存和ZIP不能替代云同步。

浏览器可能不支持某种编码。不能播放不等于原件损坏；完整ZIP仍保留原件。当前Windows WebKit的解码错误、播放器失败说明和原件下载交互仍需完善/验收，不宣称所有格式可在所有浏览器播放。

## 实际验证范围

| 步骤 | 浏览器与源站 | 组数 | 实际操作与结果 |
| --- | --- | --- | --- |
| 0 | Firefox，公网39445，独立测试profile | 7 | 原生数据库建立合成作品，实际文件输入新增图片；实际网页导出八份原件ZIP |
| 1 | Chromium，原生LAN新HTTP端口，独立profile | 9 | 选择步骤0实际下载的ZIP，导入、刷新、GLB下载、重新导出 |
| 2 | Windows WebKit，另一LAN HTTP端口，独立profile | 9 | 选择步骤1实际下载的ZIP，原件完整保存并再次导出；音视频解码失败单独记录 |
| 3 | Firefox，公网39445，全新独立profile | 9 | 选择步骤2实际下载的ZIP，重新导入与刷新；图片及四份音视频重新正常载入，GLB原件下载摘要一致 |

LAN源站仅为自有原生HTTP静态夹具，逐文件核对与公网146.1冻结构建一致；未修改DNS、代理、系统hosts或线上服务。四个profile均为空间独立的持久会话，未复制浏览器数据库；跨浏览器传递的内容只有实际下载的ZIP。

作品含九节点、五连线、八原件：三份PNG（其中一份只存在于参考列表）、WAV PCM、FLAC、MP4 H.264、WebM VP8及双动画GLB。源作品先用原生数据库建立，第三张PNG来自实际画布文件输入。所有媒体均为小型合成夹具，经Pillow或FFmpeg校验；不是生产用户素材或GPU生成结果。

每一步都核对全部原件长度、MIME和SHA-256；导入生成新项目ID及八个新文件键，保留节点ID、五条连线、尺寸/时长元数据，以及参考顺序和重复项。参考键在提示词中的相同字面量保持原文。GLB仍为内置3D卡片，实际“保存GLB原件”下载字节一致；本轮未新增跨引擎三维渲染/动画验收。

每个接收端的任务、上传、结果和频控记录均为空；历史任务转为说明文字，脚本形状文本没有执行。实际导出剔除结构化Cookie、Authorization及URL查询/片段中的合成凭证。提示词仍为用户原文，不应把作品ZIP视为自动内容脱敏工具。

## 音视频观察与未完成项

| 引擎 | MP4 / WebM / WAV / FLAC载入 | 可据此确认的范围 |
| --- | --- | --- |
| Chromium | 四份readyState=4，无媒体错误 | 当前Windows原生引擎可载入这四份短样片；未据此签收完整播放、声音、后台或其他编码 |
| Firefox（源与最终恢复） | 四份readyState=4，无媒体错误 | 从WebKit再导出的原件保持可用，排除本次迁移改坏文件；不能外推到所有媒体 |
| Windows WebKit | 四份readyState=0，媒体错误code=4 | 原件迁移完整，播放器解码不通过；具体运行时/编码支持原因及失败提示另验 |

五秒媒体就绪观察只属于测试脚本，不是产品超时；没有给产品增加重试、大小、并发或保留限制。当前一般视频节点另存后缀仍按MP4选择，WebM原件的独立另存命名需完善；本轮完整ZIP已按MIME保留WebM文件。该发现列入F-26，不能把ZIP通过写成所有下载路径已完成。

F-34的“实际跨引擎、跨源站、混合媒体作品ZIP互导”已完成本次样本验收；仍待大文件、真实生成多媒体、不同产品版本灾备、长离线和故障矩阵。云同步只可使用原API已有能力，否则保留外部依赖。[全部F-01～F-46](10-开发进度与交付记录.md)。

## 页面、录像及回执

![Firefox最终恢复的混合媒体作品](media/studio-148/migration-3-firefox-2026-10-30T15-48-56-958Z/mixed-canvas-desktop.png)

[Chromium接收页面](media/studio-148/migration-1-chromium-2026-10-30T15-48-01-089Z/mixed-canvas-desktop.png) · [Windows WebKit解码失败但原件保留](media/studio-148/migration-2-webkit-2026-10-30T15-48-22-009Z/mixed-canvas-desktop.png) · [Firefox 390px网页](media/studio-148/migration-3-firefox-2026-10-30T15-48-56-958Z/mixed-canvas-390.png) · [最后实际导出ZIP](media/studio-148/migration-3-firefox-2026-10-30T15-48-56-958Z/actual-Web-export.zip)。390px仅响应式视口模拟。

[WebKit完整所选页MP4](media/studio-148/WebKit-mixed-originals-migration.mp4) / [GIF](media/studio-148/WebKit-mixed-originals-migration.gif) · [Firefox最终恢复完整所选页MP4](media/studio-148/Firefox-mixed-originals-recovery.mp4) / [GIF](media/studio-148/Firefox-mixed-originals-recovery.gif)。录像覆盖各自完整所选页面，不冒充全部四步的连续录像，且不代表WebKit播放通过。

| 回执 | 内容 |
| --- | --- |
| [夹具](research/studio-implementation/Web-migration-fixtures-148.json) | 八份原件的编码、尺寸、长度及摘要，原生CPU生成/解码 |
| [步骤0](research/studio-implementation/Web-migration-0-firefox-2026-10-30T15-45-40-445Z.json) | Firefox源作品实际导出、原件与引用核对 |
| [步骤1](research/studio-implementation/Web-migration-1-chromium-2026-10-30T15-48-01-089Z.json) | Chromium新源站实际互导 |
| [步骤2](research/studio-implementation/Web-migration-2-webkit-2026-10-30T15-48-22-009Z.json) | WebKit互导通过、原生解码错误明确保留 |
| [步骤3](research/studio-implementation/Web-migration-3-firefox-2026-10-30T15-48-56-958Z.json) | Firefox公网全新会话接收及刷新 |
| [媒体](research/studio-implementation/Web-migration-media-148.json) | 两MP4两GIF完整解码、来源摘要 |
| [资源关闭](research/studio-implementation/Web-migration-process-cleanup-148.json) | 四步及早期失败的自有浏览器、页面与夹具关闭，未操作用户标签 |
| [线上只读](research/studio-implementation/independent-host-unchanged-148.json) | 原API与静态服务指针/配置/PID不变，无重部署或重启 |
| [源码只读边界](research/studio-implementation/independent-api-boundary-4c40e18fc5554e74a8fdde792859ead0.json) | 两个原API工作区均未写入 |
| [综合复核](research/studio-implementation/independent-review-148.json) | 冻结代码、逐步ZIP链、全部46项、文档链接及已知业务Key扫描 |

早期失败保留：[夹具键包含非法点号](research/studio-implementation/Web-migration-0-firefox-2026-10-30T15-45-06-498Z.json)、[页面定位不符](research/studio-implementation/Web-migration-1-chromium-2026-10-30T15-46-01-970Z.json)、[未等画布完成水合](research/studio-implementation/Web-migration-1-chromium-2026-10-30T15-46-41-247Z.json)。它们属于测试脚本/夹具问题，修正后重跑完整接收步骤，失败不计通过；未通过改产品、延长等待或放宽摘要断言掩盖。

复现工具：[生成夹具](tools/prepare_studio_web_migration.py)、[实际四步互导](tools/verify_studio_web_migration.mjs)、[原生浏览器生命周期](tools/studio_web_browser.mjs)、[完整所选页录制转换](tools/record_studio_web_migration_media.py)、[综合复核](tools/review_studio_web_migration.py)。工具以外层开发根目录为执行环境，`docs/studio/tools`为审查镜像，不能直接假设独立克隆后路径不变。未调用本地Docker、WSL或虚拟机。
