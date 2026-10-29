# 130 · MediaGen Skill 兼容同步与发布

本增量按用户要求同步平台调用 Skill，以项目 `app/skills/media-gen-skill/` 为权威来源，发布 1.1.1。沿用 Python 3.10+ 标准库、业务 Key、实时模型发现、异步原生任务与提交前持久幂等恢复；不改公开接口、Worker 协议、数据库或生成流程。云端仍为已验证的 129.3，本次不重新部署后台、不新增 GPU 生成或页面。

## 1. 同步内容

| 编号 | 内容 | 兼容与约束 |
| --- | --- | --- |
| S01 | 3D 最小权限及三级额度 | 明列完整上传/生成/下载 scope；meshes:create 与 images:create 独立；旧 Key 和父级 daily_mesh_count 默认 0，不自动扩权 |
| S02 | 单图生成及几何元数据 | source_image、quality、seed、n=1；不传 prompt/size/seconds；MIME 位于顶层，几何字段位于 metadata；服务端校验几何，客户端只校验下载大小/摘要 |
| S03 | GLB 下载与恢复 | 自动命名用 MIME；显式 output 保留用户路径；服务端完整/HEAD/Range 文件名含 .glb；同 Key/原请求恢复，八小时期限不续期 |
| S04 | 支持边界 | 静态自包含三角网格 GLB 2.0，512 MiB、500 万顶点、1000 万三角面；不承诺其他格式、材质/打印质量、交互预览或 Sub2API 3D 结算 |
| S05 | 版本和展示元数据 | VERSION、客户端 User-Agent 为 1.1.1；展示说明补全 3D；五个发行源文件逐一绑定真实 Git 提交与 SHA-256 |

## 2. 验证与发行约束

先执行 Skill 校验、现有客户端行为回归、静态检查和文档/证据门禁，再提交源文件。两次独立目录构建的 ZIP 必须逐字节相同，来源清单绑定已提交的五个源文件；通过独立公开下载检查 tag、ZIP、清单、摘要及全部成员。已有 1.0.0/1.1.0 Release 不覆盖。版本标签沿用仓库已有的发布流程；不提交凭证、恢复状态、客户素材或权重。

客户端回归覆盖未知 POST 沿原幂等恢复、Key/origin 隔离、终态与下载、不覆盖已有文件、损坏内容拒绝、UTF-8、音频预设、单图 3D 参数与自动 .glb。真实 GPU 几何及最小权限证明沿用 [129](129-三维兼容复查与GLB边界修复.md) 的独立实测；不得把本次客户端回归或公开包下载当成新的 GPU/完整首版验收。

安装与更新方式见 [126](126-MediaGenSkill安装与调用.md)，公开发行入口见 [127](127-MediaGenSkill-Release说明.md)，双语更新说明见 [1.1.1](releases/media-gen-skill/1.1.1.md)。

## 3. 本地验证结果

Skill 校验、Ruff、独立 CLI help 和原生客户端 11 项回归通过，0 失败/错误/跳过；验证前后 11 个明确输入摘要一致。[本地结果](evidence/2026-10-29/media-gen-skill-130-local.json)绑定五个发行源文件、客户端测试、构建脚本及锁文件。首次直接运行校验器受 Windows 默认 GBK 解码影响，随后以 Python `-X utf8` 完整校验通过，没有放宽校验规则。pytest 自身报告 7.50 秒，包含独立进程启动的整体验证为 56.958 秒。

客户端执行代码相对 1.1.0 仅变更 User-Agent 版本号，已对 Git 中的旧版客户端与当前文件逐字节核对这一差异；生成、上传、恢复和文件传输算法未改。已有 129.3 真实 GPU 验证按原范围引用，不在本轮重跑。文档/需求证据门禁均通过，历史报告保留。

## 4. 正式发布与独立下载

[Media Gen Skill 1.1.1](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.1.1) 已于 **2026-10-29 16:30:58（Asia/Taipei）** 正式发布。源标签 `media-gen-skill-v1.1.1` 指向真实提交 `bb518a7bfddaf32f410e5cbf4ea0f0081370d88f`；[仓库标签发布流程](https://github.com/copypasteltd/comfyUI-aggregator-proxy/actions/runs/40842651773)成功，没有覆盖历史 1.0.0/1.1.0。

| 编号 | 验证 | 实际结果 |
| --- | --- | --- |
| P01 | 两次 Windows 原生构建 | ZIP、release.json、SHA256SUMS 三个文件分别逐字节相同；ZIP 14,279 字节；来源五文件与提交一致 |
| P02 | 独立公开下载 | 匿名重新下载全部三个资产；公开 SHA256SUMS、GitHub 资产摘要和 ZIP CRC 通过 |
| P03 | 来源与载荷 | 正式标签提交与来源清单一致；ZIP 精确六成员，无多余路径；五个发行源文件及 release.json 与本地构建逐字节相同 |
| P04 | 可执行客户端 | 公开客户端下载到 app/.local 的独立目录，以隔离 Python 执行 CLI help 通过；Python 3.10 语法解析通过；公开脚本等于本轮 11 项回归对应的脚本。没有宣称执行了独立 Python 3.10 运行环境 |
| P05 | 历史与验收边界 | 旧 Release 保留；未新增 GPU 任务、未改服务/Worker/数据库/界面；完整首版和设备容量验收仍待完成 |

**公开 ZIP SHA-256**：`acc9faf1a3c096339f7ba2450dcee53d16caf77fc36210dcf29e1bdac6f6ffc7`。请使用 Release 内的 SHA256SUMS 核对实际下载；不要以 Windows 本地 ZIP 的摘要代替公开包摘要。

[本地构建证据](evidence/2026-10-29/media-gen-skill-130-build.json)和[正式发行独立核对](evidence/2026-10-29/media-gen-skill-130-public-release.json)分开保留。初次公开核对探针要求跨平台 ZIP 整体摘要相同而中止；字节核对确认只有六个 central-directory 的 creator-platform 字段不同（Windows 0 / Linux 3），其余归档字节和全部文件载荷完全一致。最终按公开包自身摘要、标签/清单及逐成员内容核对通过，未覆盖或重新上传正式包。这是验证器跨平台比较方法的修正，不是客户端生成或下载失败。

用户解压后按 [126](126-MediaGenSkill安装与调用.md) 更新自己已安装的 Skill 并打开新会话；本次项目外没有安装或覆盖任何用户 Skill。Admin 页面未变化，原有截图/录屏/GIF 仍见 [128 的实际媒体](media/model3d-128/README.md)。仅保留发行包、验证记录与用户既有线上服务，没有新增常驻进程或浏览器标签。

本次全程原生，不调用 Docker/WSL/虚拟化，不创建浏览器；无需改动远程设备管理 Skill 或用户其他 Skill 仓库。项目目录之外不安装、覆盖其他 Skill。
