# 模型说明、Skill 与自动回收 · 实际交付

[English](README.en.md) · [完整交付记录](../../124-模型使用说明与公开Skill交付.md) · [Skill Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.0.0)

来自 `https://gen.sub2share.xyz` 的实际青柠 Admin，包含已发布模型说明编辑、参考槽位和 600 秒空闲回收配置。录屏原速 13.56 秒，登录过程单独保存在私有目录，没有发布密码或会话。所有本次浏览器 context/browser 已关闭。

![实际模型说明与回收设置](model-usage-and-idle-policy.gif)

[原速 MP4](model-usage-and-idle-policy.mp4) · [模型说明](screenshots/model-usage.png) · [说明编辑](screenshots/model-documentation-editor.png) · [参考槽位](screenshots/model-references.png) · [移动端说明](screenshots/model-usage-mobile.png) · [回收完成](screenshots/memory-completed.png) · [回收执行证据](screenshots/memory-evidence.png) · [移动端回收](screenshots/memory-mobile.png)

通过正式 Release 解压后的 Skill 完成实际图片、视频、参考图视频和音乐生成。输入为本项目的青柠/陶杯示例和自制参考图；所有 API 下载均先校验大小与 SHA-256。公开图片去除 PNG 元数据，视频/音频进行保留编码内容的去元数据封装，避免在样本中附带 ComfyUI 图和设备路径。公开样本摘要因此与精确 API 下载不同，两组分别在 manifest 和运行证据记录。

![真实 720×1080 生成](generated-720x1080.png)

| 样本 | 实际规格 |
| --- | --- |
| [4090 文生视频](generated-4090-video.mp4) | 864×480、24 fps、约 5.167 秒 |
| [5070 首帧视频](generated-5070-first-frame.mp4) | 512×288、24 fps、1.625 秒 |
| [3080 音乐](generated-15s-music.flac) | 15 秒、48 kHz、双声道 FLAC |

[实际原生请求/响应](api-responses.json) 不含鉴权头、Cookie 或幂等凭证；临时测试 Key 已停用。原响应中的任务与素材 ID 是当次运行记录，素材成功八小时后到期，不能作为长期下载链接。当前音乐 prompt 控制风格，歌词仍由冻结流程提供。

[manifest](manifest.json) 包含媒体和来源报告摘要，[完整媒体校验](../../evidence/2026-10-28/media-gen-public-media-124.json) 记录图片解析和原生 FFmpeg 完整解码。截图可以从原始全页截图裁成可读视窗，原图仍在证据目录。它们验证本次功能和素材链路，不代表生成质量、所有 Workflow、严格多机均衡或 10～20 台容量验收。
