# 双 GPU 实际接入 / Actual dual-GPU onboarding

[中文入口](../../../README.md) · [English](../../../README.en.md) · [接入与调用说明](../../118-双GPU真实ComfyUI接入.md)

这些画面来自已部署的实际 Admin：两台原生 Windows GPU、两条启用渠道、241 个候选草稿、51 个模型文件登记和三个实际生成后发布的模型。未注入 fixture。私网地址仅在公开画面遮挡；Workflow 目录按 YuE2 筛选，避免展示私人项目名称。自建浏览器会话已退出并关闭。

These captures show the deployed Admin after connecting two physical Windows GPUs. No fixture data is injected. Private addresses are visually masked and the Workflow screen is filtered to YuE2. All owned browser sessions, contexts and processes are closed. The UI consistently uses the original 04 Lime system.

![实际接入后台 / Actual onboarded Admin](admin-onboarding.gif)

[原速后台录屏 / Original-speed Admin MP4](admin-onboarding.mp4)

| 页面 / Page | 截图 / Screenshot |
| --- | --- |
| 运行总览 / Overview | [Desktop](overview.png) · [Mobile](overview-mobile.png) |
| Windows 渠道 / Channels | [Channels](channels.png) |
| Windows 节点 / Nodes | [Nodes](nodes.png) |
| Workflow 管理 / Workflow directory | [Filtered actual directory](workflows.png) |
| 模型与路由 / Published models | [Models](models.png) |
| 任务管理 / Actual jobs | [Jobs](jobs.png) |
| 模型文件目录 / Registered model files | [Model files](model-files.png) |

真实样本使用本轮自建青柠/白杯提示，由服务派发到两台 ComfyUI，再经私有 S3 校验及 Admin 鉴权下载；不是手工绘制的图片或旧客户输出。公开样本已移除内嵌生成元数据；证据分别记录原始下载及公开副本摘要。单条样本不代表所有规格、内容质量或完整产品验收。

The samples below were generated through the service, verified in private S3 and downloaded with authorization. They use new test prompts and are not historical customer outputs. Embedded generation metadata is removed from public copies; exact downloaded and public-copy hashes are recorded separately. These samples do not certify all presets, creative quality or complete-product acceptance.

![真实生图样本 / Actual image generation](verification-image.png)

![真实视频 GIF / Actual video sample](verification-video.gif)

[生成视频 MP4 / Generated video](verification-video.mp4) · [ComfyUI 音乐 FLAC / Generated music](verification-audio.flac)

音乐样本为 48 kHz 双声道、约 29.952 秒，prompt 绑定曲风并保留 Workflow 自带歌词。视频为 864×480、约 5.166 秒；图片为 1024×1024。

证据 / Evidence: [生成与交付](../../evidence/2026-10-27/comfy-real-delivery-118.json) · [实际浏览器](../../evidence/2026-10-27/comfy-admin-browser-118-final.json) · [最终核对](../../evidence/2026-10-27/comfy-onboarding-closeout-118.json) · [媒体清单](manifest.json)。第三台仍超时，图生图/多参考和其他候选仍待实测。
