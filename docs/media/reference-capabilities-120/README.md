# 120 · 参考生成实际媒体 / Actual reference-generation media

来自实际部署平台，使用本项目自制的两张参考图。没有注入 fixture；没有使用客户图片或原私有画布提示词。画面沿用原 04 · 青柠样式。 / Captured from the deployed platform with two project-owned reference images, without fixtures or customer media. The original 04 Lime style is retained.

| 内容 / Item | 文件 / File |
| --- | --- |
| 已发布模型 / Published models | [models.png](models.png) |
| 渠道 / Channels | [channels.png](channels.png) |
| 任务 / Jobs | [jobs.png](jobs.png) |
| 总览 / Overview | [overview.png](overview.png) · [mobile](overview-mobile.png) |
| 原速界面录屏 / Original-speed UI recording | [MP4](admin-reference.mp4) · [GIF](admin-reference.gif) |
| 自制参考图 / Original reference inputs | [0](reference-0.png) · [1](reference-1.png) |
| 单图生图 / Single-image editing | [PNG](sample-image-edit.png) |
| 双图生图 / Two-image generation | [PNG](sample-image-refs.png) |
| 单首帧视频 / First-frame video | [MP4](sample-video-i2v.mp4) |
| 双图参考视频 / Two-image video | [MP4](sample-video-refs.mp4) · [GIF](sample-video-refs.gif) |

![双图参考视频 / Two-image video](sample-video-refs.gif)

图像与原视频交付均为 720×1080，视频约 5.167 秒。公开 PNG 移除生成元数据，MP4 为移除元数据后的重新编码副本，GIF 为 360×540 的预览；原始交付与公开副本摘要分别记录于 [samples.json](samples.json)。未进行帧内身份一致性、模型质量或吞吐基准验收。 / Original outputs are 720×1080, videos about 5.167 seconds. Public PNGs omit generation metadata; MP4s are re-encoded visual copies, and the GIF is a 360×540 preview. Original and public-copy hashes are distinct. These controlled samples do not establish identity fidelity, quality benchmarks or fleet throughput.

[调用与范围 / Usage and scope](../../120-参考能力发布与精确尺寸验证.md) · [真实生成证据 / Delivery evidence](../../evidence/2026-10-28/reference-capabilities-120.json) · [实际浏览器 / Browser evidence](../../evidence/2026-10-28/reference-admin-browser-120.json) · [界面媒体摘要 / UI media hashes](manifest.json)
