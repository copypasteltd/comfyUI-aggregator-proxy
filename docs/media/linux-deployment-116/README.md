# Linux HTTP Admin · 实际部署截图与录屏

本组素材来自已部署 Linux 服务的普通公网 HTTP 管理页，沿用原始 **04 · 青柠** 组件和样式。实际数据库尚无 Windows 节点、渠道、Workflow 或生成任务；API Key 页可能保留已删除的协议探针记录。没有注入演示 fixture、没有启动 GPU 或执行真实 ComfyUI，不代表生成能力或集群容量验收。

These captures show the actual Linux deployment over plain HTTP, using the original **04 Lime** system. No Windows nodes, channels, Workflows or generation jobs have been registered. Deleted protocol-probe records may appear on the API Key page. There is no injected UI fixture or real GPU generation.

已验证普通 HTTP 登录、五个桌面页面及 390×844 移动总览；无 JavaScript 错误、5xx 或页面横向溢出。测试会话已退出，所有本次创建的浏览器上下文和浏览器已关闭。首轮浏览器检查发现的 `randomUUID()` HTTP 兼容问题已修复；最终截图来自修复后版本。

![实际 Linux 后台：总览、渠道、Workflow、模型与 API Key](admin-http.gif)

| 编号 | 页面 / 内容 | 文件 |
| --- | --- | --- |
| 01 | 实际空业务总览 / live empty overview | [桌面 PNG](overview.png) · [手机 PNG](overview-mobile.png) |
| 02 | Windows 渠道 / Windows channels | [PNG](channels.png) |
| 03 | Workflow 管理 / Workflow management | [PNG](workflows.png) |
| 04 | 模型与路由 / Models and routing | [PNG](models.png) |
| 05 | API Key 管理 / API Key management | [PNG](keys.png) |
| 06 | 原速录屏 / original-speed recording | [MP4 · 13.84s · 1440×1000](admin-http.mp4) |
| 07 | 文件摘要与浏览器结果 / hashes and browser results | [manifest](manifest.json) · [验证记录](../../evidence/2026-10-27/linux-admin-browser-116.json) |

MP4 与 GIF 从同一浏览器录屏编码，保持原时长；GIF 为 960px、5fps。文件大小和 SHA-256 全部记录在 manifest，实际 PNG、MP4 与 GIF 已解码检查。登录口令和 Cookie 不出现在录屏、文件清单或公开报告中。
