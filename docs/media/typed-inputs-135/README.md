# 合并线上版本后的多模态 Admin

2026-11-04；全部为明确标记的合成演示资料。使用实际本地 Admin、原生 API 与独立 PostgreSQL 演示库，未展示真实生成或生产发布结果。视觉使用 04 · 青柠。演示凭证、数据库、服务、浏览器和两个上下文均已清理；[验证记录](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/capture.json) 可核对来源与摘要。

![命名素材、文本、声音和续拍管理](workflow-inputs-admin.gif)

[原速 MP4](workflow-inputs-admin.mp4) · [GIF](workflow-inputs-admin.gif) · [文件摘要](manifest.json) · [上轮演示](../typed-inputs-133/README.md)。

| 页面 | 截图 |
| --- | --- |
| 模型输入及功能说明 | [模型 usage](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/typed-model-usage.png) |
| JSON 内的受控文字绑定 | [文本绑定](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/nested-text-binding.png) |
| 图片、源视频、声音与网格输入 | [素材槽](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/typed-input-slots.png) |
| 脚本和转写说明 | [文本槽](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/typed-text-slots.png) |
| 源声音策略 | [声音策略](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/video-audio-policy.png) |
| 受控父任务上下文 | [续拍](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/workflow-continuation.png) |
| Key 参考图片限额 | [Key 设置](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/key-sixteen-references.png) |
| 移动端说明 | [移动端](../../evidence/2026-11-04/typed-inputs-133-ui-merged-second/screenshots/typed-usage-mobile.png) |

复现时先构建当前 Admin，再执行 `app/scripts/capture-workflow-inputs.py`，显式传入已安装 Playwright 的原生 Python 和新的 `docs/evidence/` 输出目录。脚本拒绝覆盖既有证据，并在 finally 清理自身资源。[兼容升级说明](../../135-全量接入与线上版本兼容升级.md) 区分本地检查与真实上线。
