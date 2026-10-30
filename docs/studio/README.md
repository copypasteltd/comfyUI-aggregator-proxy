# 无限画布文档

[独立画布部署与使用：39445、跨源迁移、原生构建与回退](21-独立画布部署与使用.md)。当前139.2独立上线，107当前/170旧资源与14路由通过；原API137.1及其服务保持原样。业务直达原39443，无画布转发后端，完整签收HOLD。

[24 · 真实GPU响应丢失、下载损坏与离线素材恢复](24-真实故障恢复与离线素材验收.md)：一项720×1080真实编辑完成，139.2离线原件/错Key/联网恢复7项通过；截图、MP4/GIF和失败回执完整保留，验收Key已禁用。

[20 · 独立画布与既有 API：最终边界、23项分层评估、性能与可用性](20-独立画布与既有API融合最终边界.md)：按用户最新要求，前端独立部署，只请求原API；不修改 API 转发/Worker/调度，不再镜像进中转项目。后端候选已撤回。

[137 · 原API历史：持久设备池、Admin图形均衡及可用性边界](19-持久设备池与融合可用性评估.md)：保留历史实现证据；画布不再实施或部署中转层扩展，真实多卡/容量仍未签收。

[最新实现与全部 46 项状态](10-开发进度与交付记录.md) · [中文使用说明](../../README.md) · [English](../../README.en.md)

[公网独立画布](http://43.165.167.179:39445/studio/) · [历史线上运维（非当前独立发布指令）](11-线上使用与原生发布运维.md) · [历史八种媒体样片与调用回执](media/studio-131/README.md)。本轮一项实际编辑与恢复已验证，未全重跑八模式；完整故障、真机和容量待验，下方调研保留历史范围。

项目目录：`E:\dev\sandbox\无限画布\`。

- [02 已下载：本地源码与中转服务融合方案](04-本地源码与中转服务融合方案.md)
- [接口映射、24 项改造清单与 18 项验收](05-中转服务接口映射与开发验收清单.md)
- [完整融合评估：12 项新发现、46 项差距和 6 个工作包](06-融合可行性评估与上线约束.md)
- [实现契约：模型参数、可靠提交、恢复、鉴权和文件交付](07-融合实现契约与故障恢复.md)
- [可用性验证、原生结果与 27 项发布门禁](08-可用性验证与发布验收.md)
- [11 个模型适配样本、完整用户流程与调用结构](09-模型适配样本与用户流程.md)
- [本机引用事务、旧数据保留升级与保存失败恢复](12-本机引用事务与保存失败恢复.md)
- [任务清单备份、原子导入与服务端首次确认](13-任务清单备份与只读恢复.md)
- [原提交只读核对与无ID恢复](14-原提交只读核对与恢复.md)
- [批量创作、部分失败及只继续未发送项](15-批量创作与部分失败恢复.md)
- [GLB 交互预览、原件保存与三维资源释放](16-GLB交互预览与资源释放.md)
- [本机空间与参考缓存清理](17-本机空间与参考缓存清理.md)
- [Key轮换、已知任务接管与原身份备份](18-Key轮换与已知任务接管.md)
- [46 项开发差距 CSV](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/fusion-assessment-2026-10-30/development-gaps.csv)
- [评估汇总、来源摘要和证据索引 JSON](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/fusion-assessment-2026-10-30/assessment-summary.json)
- [本地下载与静态接口核对记录](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/aggregator-integration-audit-2026-10-30.json)
- [01 / 02 深度对比：48 项，覆盖移动端、接口、存储及运维](03-Toonflow与无限画布全面横向对比.md)
- [页面对比图集：桌面原图、手机视口、筛选与放大](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/03-%E9%A1%B5%E9%9D%A2%E5%AF%B9%E6%AF%94.html)
- [01 / 02 对比 CSV](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/toonflow-vs-infinite-canvas-2026-10-29.csv)
- [当前选型：12 个 AI 创作画布，比较易用性与轻量部署](02-AI创作画布精选与轻量部署对比.md)
- [当前选型 CSV](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/ai-creative-canvas-2026-10-29.csv)
- [当前选型 JSON 与证据](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/ai-creative-canvas-2026-10-29.json)
- [第一版：60 项泛无限画布 / SDK / 桌面方案调研](01-开源无限画布方案调研.md)

调研快照日期：2026-10-29。12 项选型以静态调研为基础；01 / 02 另补源码核对、Toonflow 官方截图与无限画布公开演示的桌面／手机视口检查。

2026-10-30 已将 02 原版源码克隆至 `../infinite-canvas/`，并完成系统融合评估。原版 npm 安装失败；隔离修复版已通过原生安装/类型/生产构建和 HTTP 新建刷新，手机工具栏检查失败并保存截图。11 个历史模型、七类任务的 101 项离线契约，以及四份文档示例均通过。未做移动真机、融合生成联调或生产部署，发布状态 HOLD。公开演示、隔离本机基线与未来融合页面分别标注，不能互相冒充。


以上 2026-10-30 评估段落为当时基线；正式分支已经实现适配器、日志与媒体节点，最新构建、浏览器、部署和真实验证以文档 10 为准。

- [原提交只读核对、无ID恢复和首次发送保护](14-原提交只读核对与恢复.md) · [132.2当前证据](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/submission-lookup-validation-notes-1321.json)

- [本机空间、站点配额与参考缓存手动清理](17-本机空间与参考缓存清理.md) · [135.1历史证据](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/storage-validation-notes-1351.json)

- [Key轮换及已知任务接管](18-Key轮换与已知任务接管.md) · [136.1当前证据](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/task-access-validation-notes-1361.json)
