# 5070 精选工作流：真实部署素材

[English](README.en.md) · [方案与验证](../../122-RTX5070纯HTTP接入与调用验证.md) · [精选包和调用](../../examples/5070/README.md)

2026-10-28 的已部署青柠 Admin，使用实际服务器记录，不注入演示任务。当前三条渠道和十个已发布模型；截图只对私网地址文字做遮盖。录制在登录后开始，局部表单只修改本地草稿并恢复，未写入生产。所有拍摄浏览器和 context 已关闭。

![三个实际业务视频](generated-workflows.gif)

| 文件 | 内容 |
| --- | --- |
| [admin-5070.mp4](admin-5070.mp4) / [GIF](admin-5070.gif) | 模型、渠道、任务、精选 Workflow、API 模型和节点表单的原速录屏 / 低帧率预览 |
| [overview.png](overview.png) / [手机](overview-mobile.png) | 实际总览；390px 手机布局 |
| [models.png](models.png) / [channels.png](channels.png) / [jobs.png](jobs.png) | 十个发布别名、三渠道和实际生成任务 |
| [workflows.png](workflows.png) | 按 RTX 5070 筛选出的三个草稿 |
| [api-model-form.png](api-model-form.png) / [节点表单](api-node-form.png) | API-only 模型选项、ComfyUI API 版本、节点类；未伪造主机文件哈希 |
| [手机模型表单](api-model-form-mobile.png) / [手机节点表单](api-node-form-mobile.png) | 390px 单列布局与键盘焦点，减少动画模式 |
| [generated-t2v.mp4](generated-t2v.mp4) | 独立业务 API 的文生视频原始交付文件 |
| [generated-i2v.mp4](generated-i2v.mp4) | 独立业务 API 的单首帧视频原始交付文件 |
| [generated-refs.mp4](generated-refs.mp4) | 独立业务 API 的双参考视频原始交付文件 |
| [generated-overview.png](generated-overview.png) / [generated-workflows.gif](generated-workflows.gif) | 三个视频的真实抽帧与并排预览 |

业务视频均为 512×288、39 帧/24 fps、1.625 秒，含 H.264 与 AAC。它们使用中性提示及[自制参考图](../../../app/examples/5070-workflows/)，未使用私有源图/提示。MP4 保留原交付字节，抽帧和 GIF 为衍生物；各自大小/摘要见 [manifest.json](manifest.json)。短片用于证明模型与交付链路，高清、长片及视觉质量不由本次签收。源码与完整运行证据分别见 [浏览器报告](../../evidence/2026-10-28/5070-admin-browser-122.json) 和 [实际交付](../../evidence/2026-10-28/5070-real-delivery-122.json)。

仓库中的展示副本不会因生产内容八小时到期自动删除。ComfyUI 原文件、HTTP Worker 暂存、正式云对象、调用方下载采用各自保留规则；[121](../../121-ComfyUI原始产物移交即清理设计.md) 的原文件清理仍待实现。
