# 原生 ComfyUI 工作台：实际页面与录屏

[English](README.en.md) · [设计、接口与开发约束](../../146-原生ComfyUI面板访问与独占操作.md) · [上线与验证记录](../../evidence/2026-11-15/origin-146-production.json)

以下素材于 2026-11-15 从已上线的 HTTP 39443 服务实际采集。登录、设备目录和 Admin 授权复用已拉取的 **04 · 青柠** 样式与字体；设备编辑器保留 ComfyUI 原界面，并增加青柠返回导航及设备本地文件提示。截图中没有 API Key、密码、内网地址或源站鉴权信息。

![设备目录及实际原生编辑器演示](origin-workspace.gif)

| 素材 | 内容 |
| --- | --- |
| [桌面登录](origin-login-desktop.png) / [移动登录](origin-login-mobile.png) | 实际入口，移动视口为 390×844。 |
| [桌面设备目录](origin-devices-desktop.png) / [移动设备目录](origin-devices-mobile.png) | 当前 Key 获准的三台设备、源队列观测及独占操作。获准不代表在线或可立即占用。 |
| [Admin 授权](origin-admin-grant.png) | 实际授权抽屉；浏览器保存原有三台选择并重新打开确认，未额外变更其他 Key。 |
| [5070 原生编辑器](origin-native-5070.png) | 原生前端已完成初始化，实际运行两节点图并显示结果。 |
| [原速完整录屏](native-recording.mp4) | 80.2 秒、无音轨，保留原生页面加载、编辑器和实际提交过程。 |
| [操作导览](workspace-tour.mp4) / [GIF](origin-workspace.gif) | 取设备目录前 7 秒、原生录屏最后 13 秒，再加入 4 秒实际 Admin 截图；片段保持原速，已剪去中间等待。GIF 为 6 fps、宽 960 像素，不是完整连续录像。 |
| [实际诊断 PNG](diagnostic-64x64.png) | 独立 HTTP/WS 验证返回的 64×64 青柠色图，430 字节；与浏览器录屏的生成任务分别执行。 |

诊断图使用 `EmptyImage → SaveImage`，验证真实 ComfyUI 接收、执行、事件及下载链路，**不调用训练模型，也不证明模型画质**。[真实浏览器请求与回执](../../examples/origin-146/README.md) 提供脱敏样例。原生文件保留在设备，不纳入业务云端八小时删除规则。

本轮 5070 完成原生执行。3080 的 ComfyUI 不可达，4090 的原维护占用保留，未向它们提交测试。已安装扩展的静态模块可加载，未开放的私有扩展接口、主机维护接口和外部云服务仍有边界；不能据此宣称任意第三方插件全部兼容。

[媒体摘要清单](../../evidence/2026-11-15/origin-146-media.json) 记录上述十个文件的字节数与 SHA-256。所有本次自动化浏览器页面、上下文和进程均已在 `finally` 中关闭；没有调用本地 Docker、WSL 或虚拟机。
