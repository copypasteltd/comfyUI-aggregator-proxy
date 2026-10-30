# ComfyUI 创作画布

[English](README.en.md) · [文档索引](docs/studio/README.md) · [部署与使用](docs/studio/21-独立画布部署与使用.md) · [全部46项状态](docs/studio/10-开发进度与交付记录.md)

**独立静态画布，只调用现有 ComfyUI API。** 前端基于 [basketikun/infinite-canvas](https://github.com/basketikun/infinite-canvas)，全面采用已拉取的 04·青柠样式。画布独立构建、发布与回滚；模型、Workflow、业务 Key、队列、GPU 和内容生命周期由原 API 系统管理。

| 入口 | 地址 |
| --- | --- |
| 独立画布138.1 | [打开画布](http://43.165.167.179:39445/studio/) |
| 原业务API | `http://43.165.167.179:39443` |
| 原管理后台 | [Admin](http://43.165.167.179:39443/admin/) |

![实际公网桌面模型目录](docs/studio/media/studio-138/public-independent-models-desktop.png)

[390px页面](docs/studio/media/studio-138/public-independent-models-390.png) · [导览MP4](docs/studio/media/studio-138/independent-desktop.mp4)。实际截图使用已禁用的只读验收 Key，生成权限提示为预期；导览使用合成 API 协议，不代表本轮 GPU 生成或真机手机验收。

![独立画布导览：合成API协议](docs/studio/media/studio-138/independent-desktop.gif)

## 方案与功能

```mermaid
flowchart LR
  U[用户浏览器] --> S[独立静态画布39445]
  S --> L[本机画布/素材/原提交日志]
  S -->|业务Key·既有v1接口·不带Cookie| A[原API39443]
  A --> Q[原队列/Worker/ComfyUI]
  Q --> A
  A -->|任务状态与鉴权内容| S
  S -->|外链| M[原Admin]
```

- 动态读取当前 Key 的模型目录、注释、Schema、完整 preset 与参考槽位；按原 API 已发布能力创作图片、视频、Workflow 音频及图片生3D。
- 画布组织参考素材和结果分支，支持本机媒体库、任务历史、批量任务、图片/视频/音频节点与按需 GLB 静态交互预览。
- 先持久化原请求再提交；已知任务查询原ID，未知提交先核对。支持离开恢复、显式远程取消、已知任务的逐项Key接管；不通过换Key或改参数重发未知任务。
- 下载核对类型/长度/SHA-256，再保存本机。作品ZIP备份画布和原件，任务JSON备份原请求与编号；均不替代云同步。

画布不增加生成后端或第二套队列，不改原转发、协议、Worker、调度、数据库或Admin，不直接调用ComfyUI。业务请求只有既有`/v1/`；画布39445上的API/管理/Worker路径返回404。原API137.1的源码基线、配置、指针及服务PID在部署前后相同。[23项架构边界与可用性评估](docs/studio/20-独立画布与既有API融合最终边界.md)。

## 快速使用

1. 打开独立画布，在连接页填写**原API地址39443**和管理员发放的业务Key，按需选择是否在本机记住Key。地址不要填写画布39445、`/admin/`或ComfyUI8188。
2. 连接后查看模型用法，按授权选择完整规格、尺寸/时长及参考槽位。目录列出模型不代表设备当前空闲；首帧视频与多参考视频是不同能力，音频是Workflow产物。
3. 新建画布并新建创作，提交后保留原任务。响应丢失使用核对原提交；停止本页等待不等于取消GPU任务。
4. 完成后校验并载入素材，可继续创作、交互预览GLB或另存原件。云端沿用完成后8小时到期，本机副本受浏览器配额和清理影响，建议另存或导出ZIP。
5. 需要管理模型、Workflow、渠道、Key或排队时，打开原Admin。原系统缺少的功能属于外部依赖，画布不会绕过API实现。

**换端口/域名不会自动迁移本机数据。** 先在旧39443画布导出作品ZIP、必要原件和任务JSON，到新39445入口导入并重新填写业务Key；确认可用前保留旧数据。导入作品不会重新生成；任务清单需主动查询原ID。数据库v4不得被静态回退降级。

## 原生开发与部署

已验证Node22.14.0/npm10.9.2。进入本仓库`web/`执行：

```powershell
npm ci
$env:VITE_BASE='/studio/'
$env:VITE_STUDIO_MODE='managed'
$env:VITE_AGGREGATOR_ORIGIN='http://43.165.167.179:39443'
npm run typecheck
npm run build
```

仅发布`web/dist/`，由独立静态服务提供`/studio/`和SPA回退。`managed`是功能约束模式，与是否独立部署无关；不要换成启用上游脚本/Agent的`standalone`模式。默认API是公开地址，Key不进入构建。没有默认API时连接页留空。

当前独立服务为`infinite-canvas-static.service`，目录`/opt/infinite-canvas/releases/canvas-20261030.138.1/`，指针`/opt/infinite-canvas/current`，监听39445。只切换画布自己的发布目录；原API不随画布部署或回退。HTTP高位端口供用户自行反代，不设HTTPS门槛。本地开发/验证禁止Docker、WSL和虚拟机。[部署详情与迁移](docs/studio/21-独立画布部署与使用.md)。

## 验证与限制

本轮179前端单元、类型/生产构建、11组合成双源浏览器、107静态文件公网摘要及2组真实API目录浏览器检查通过。依赖复用先前原生空目录的锁定安装；不是本轮重新安装。临时只读Key已禁用，自建浏览器/夹具和测试PG均已关闭。图片、视频、音频和GLB八种真实模式为历史证据，本轮未重发GPU任务。

完整首期仍为**HOLD/pending-test**：故障矩阵、实际Android/iOS、大文件峰值/泄漏及10～20台容量待验。WebDAV/云同步、可选文本助手、动画/更多3D格式未完成；ComfyUI原件移交即删除、异构设备合并及GPU优化是原API外部依赖，不能通过修改转发来补足。[已实现编号与全部待办](docs/studio/10-开发进度与交付记录.md)。

## 文档与许可

源代码在`web/`；独立文档、公开证据和页面资料从[文档索引](docs/studio/README.md)进入。更新说明见[独立画布更新记录](docs/studio/23-独立画布更新记录.md)，旧融合/中转报告保留历史标记；文档20/21为当前架构和部署依据。

保留上游[MIT许可](LICENSE)与作者声明，04·青柠源样式/字体说明随项目和静态包保留。功能分支独立维护，不向basketikun上游推送，也不更新原API分支。
