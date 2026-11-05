# ComfyUI 创作画布

[English](README.en.md) · [使用与部署](docs/studio/21-独立画布部署与使用.md) · [当前范围与限制](docs/studio/37-交付范围纠正与核心适配现状.md) · [文档索引](docs/studio/README.md) · [更新记录](docs/studio/23-独立画布更新记录.md)

基于 [basketikun/infinite-canvas](https://github.com/basketikun/infinite-canvas) 二次开发的独立 Web 创作画布。复用原有拖拽、缩放、节点、连线和项目管理，适配现有 ComfyUI 聚合 API 的模型、参考输入、异步任务与产物。浏览器直接调用原 API，无须新增生成后端、队列或 GPU 代理。

当前静态版本 **153.1** 已部署；本次更新纠正开发范围和文档状态，应用源码与部署保持该版本。历史46项是评估与扩展验证矩阵，**不代表46项未开发功能**。核心适配已有实现；近期双参考视频失败及未验证浏览器仍单列说明，不宣称所有模型当前都可成功执行。

| 画布 | 原业务 API | 管理后台 |
| --- | --- | --- |
| [打开创作画布](http://43.165.167.179:39445/studio/) | `http://43.165.167.179:39443` | [原 Admin](http://43.165.167.179:39443/admin/) |

## 方案与能力

```mermaid
flowchart LR
    W[已有无限画布 + 前端适配] -->|业务Key / 既有v1接口| A[原聚合API]
    A --> G[原队列 / Worker / ComfyUI]
    A -->|状态与鉴权内容| W
    W --> L[浏览器本机项目 / 素材 / 任务记录]
```

| 能力 | 使用逻辑 |
| --- | --- |
| 模型发现 | 读取当前Key的模型说明、Schema、完整preset和参考槽位；不按模型名称猜参数 |
| 生图与参考创作 | 文生图、单图编辑、多参考图按已发布模型适配；画布所选图片可填入参考槽位 |
| 视频 | 文生、首帧和多参考按各自Workflow声明调用；不把缺少参考的请求降级为其他模式 |
| 音频与3D | 音频是ComfyUI Workflow产物；支持GLB结果卡、按需预览和原件保存，不把音频默认当TTS |
| 异步任务 | 上传参考 → 显式提交 → 查询原job → 载入/另存；刷新恢复原记录，不盲目新建替代任务 |
| 项目与素材 | 继续使用原画布编辑/项目能力，保留本机保存、ZIP/任务清单和已有引用保护 |
| 青柠与案例 | 全面复用04·青柠；浅色/深色切换持久保存；八个可编辑案例在确认前不会生成 |

所有业务请求使用Bearer Key与`credentials: omit`，不借用Admin会话。画布只调用现有`/v1/`，不修改原转发、调度、Worker、数据库或Admin；管理仍到原后台完成。可选助手、云同步、新格式及容量工程不自动纳入本轮适配。

## 如何使用

1. 打开[连接页](http://43.165.167.179:39445/studio/config)，填写**原API地址**和管理员提供的业务API Key。不要填画布端口、Admin路径或ComfyUI8188。Key默认只在当前页面内存使用，可在可信浏览器选择记住。
2. 查看[模型与用法](http://43.165.167.179:39445/studio/models)，选择当前Key获准的模型和完整规格。尺寸/秒数来自已发布preset，不能任意混搭；模型可见不等于设备空闲或额度充足。
3. 新建/打开画布，或到[案例库](http://43.165.167.179:39445/studio/library)打开可编辑示例。案例可先不带Key浏览，打开不会上传或创建生成任务。
4. 选中图片后点“新建创作”，核对提示词、模型、规格和参考图顺序/角色，明确点击“提交1项生成任务”。其他媒体按其Schema提供输入，当前图片生3D模型不强加提示词。
5. 在节点/任务页查询原任务，可继续编辑或离开后恢复；取消须等服务端确认。成功后点“载入产物”，鉴权下载校验后回填画布，再播放、继续创作或另存原件。
6. 保存项目，重要内容另存原件或导出含原件的作品ZIP；任务JSON用于原任务核对，不代替作品备份。业务Key权限由原Admin配置。

云端产物通常完成后**八小时到期**，以API元数据为准；已下载本机副本单独保留。项目与素材主要保存在当前浏览器，不是云同步。更换域名、端口或浏览器配置文件时先导出/导入，核对新副本后再处理旧数据。保留当前日志v5，不清空或降级数据库。

具体接口映射、保存/恢复和部署说明见[使用文档](docs/studio/21-独立画布部署与使用.md)；[参考创作说明](docs/studio/35-画布参考创作与生成表单.md)展示输入到结果的流程。

## 页面与演示

| 浅色 | 深色 |
| --- | --- |
| ![浅色案例页](docs/studio/media/studio-151/gallery-public-chromium-2026-10-30T20-57-06-617Z/examples-light-desktop.png) | ![深色案例页](docs/studio/media/studio-151/gallery-public-chromium-2026-10-30T20-57-06-617Z/examples-dark-desktop.png) |

![明暗主题与可编辑案例演示](docs/studio/media/studio-151/themes-and-examples-public.gif)

[完整MP4](docs/studio/media/studio-151/themes-and-examples-public.mp4) · [主题/案例细节、3D与390px对比](docs/studio/36-明暗主题与预设创作案例.md) · [历史真实媒体图集](docs/studio/media/studio-131/README.md)。主题/案例演示使用合成API，不代表GPU生成。历史真实图集记录八类API调用与浏览器取件，当前版本没有重新全量生成；近期双参考视频准备失败原样保留。

## 本地开发与静态部署

在独立`infinite-canvas` Git仓库根目录执行，使用原生Node/npm（已验证Node22.14.0/npm10.9.2）：

```powershell
Set-Location web
npm ci
$env:VITE_BASE='/studio/'
$env:VITE_STUDIO_MODE='managed'
$env:VITE_AGGREGATOR_ORIGIN='http://43.165.167.179:39443'
npm run dev
```

打开`http://localhost:3000/studio/`。生产构建保留这些环境变量，执行`npm run typecheck`和`npm run build`，只部署`web/dist/`。API默认值只含公开地址，不含Key；也可以不设置，让用户自行填写。

`managed`表示使用既有聚合API并禁用上游任意脚本/插件/本地Agent，仍是独立静态部署。用户禁止本地Docker、WSL和虚拟机。沿用高位HTTP端口，域名反代由用户处理；不增加HTTPS门槛，不修改原API任意源CORS。静态更新只切画布目录，保留数据与旧页面资源，不重启原API。详细操作及兼容恢复限制见[部署文档](docs/studio/21-独立画布部署与使用.md)。

## 当前验证与限制

- 冻结153.1源码通过218单元、类型与生产构建；主题/案例69组合成页面检查、43截图。本机Chromium/Firefox与公开站点Chromium有对应证据，本次源码摘要一致，未重跑构建或生成。
- 历史真实图片、编辑、双参考图、文生/首帧/双参考视频、Workflow音频及GLB有原件记录；独立画布另有真实图片丢响应恢复。**最近一次双参考视频返回`preparation_transport_failure`，该模型当前成功生成尚未复验**，属于原API/设备准备链路依赖。
- Windows WebKit存在Cookie/解码诊断失败；实际Safari未验，不能宣称已支持。390px为视口模拟。更多浏览器、编码、极端配额/长离线/灾备及10～20节点容量各自标记，不再合并为画布未开发量。
- 本轮仅核对公共HTTP和现有证据；无有效业务Key环境配置，未重新验证当前全模型执行。未改旧失败JSON或将旧整体HOLD改成通过。

[核心清单及具体问题](docs/studio/37-交付范围纠正与核心适配现状.md) · [当前进度](docs/studio/10-开发进度与交付记录.md) · [历史交付明细](docs/studio/history/10-历史交付与46项评估.md)。后续只处理明确需求与实际缺陷，相关说明同步到授权远端`feat/independent-canvas`；不推上游、不修改main或原API分支。

保留原作者与[MIT许可](LICENSE)。04·青柠参考样式/字体及来源声明随静态包保留。
