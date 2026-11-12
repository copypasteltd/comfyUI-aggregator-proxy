# 131 · 单文件 Skill 与固定在线地址

2026-11-13 [CORS 更新](145-公网入口统一开放CORS.md) 已同步 Skill 静态响应与发布器验证：反映任意 Origin、允许凭证、自定义请求头和私网预检；Skill 1.3.0 内容及不可变资产保持相同。早期报告中的通配来源断言按当时实现保留。

按用户要求，提供可独立安装和调用的 `skill.md`，并部署到现有服务的固定公开地址。说明、原生 API 用法和 Python 标准库客户端全部嵌入同一文件；不把依赖外部 scripts/references 的入口文件直接冒充单文件。

## 1. 交付与边界

| 编号 | 交付 | 约束 |
| --- | --- | --- |
| SF01 | 最新入口 `https://gen.sub2share.xyz/skill.md` | 公开 UTF-8 Markdown；无需业务 Key 读取说明；生成和内容 API 仍保持原鉴权 |
| SF02 | 版本入口 `/skills/media-gen/1.3.0/skill.md` | 版本目录不可覆盖；latest/current 原子切换；保留历史以便回退 |
| SF03 | 自包含文件 | 现有 Skill、API 参考、客户端由同一来源编译，不依赖额外下载；内嵌客户端与被测脚本一致；只使用实时模型发现决定能力和参数 |
| SF04 | 更新与发布 | 沿用版本标签发布流程；Release 同时包含 ZIP、skill.md、来源清单与摘要；固定地址由原生部署工具更新，验证失败恢复原指针/配置 |
| SF05 | 服务隔离 | 使用现有 Nginx 静态入口和独立只读发布目录，不改现有生成服务、Worker、数据库或用户反代；无本地 Docker/WSL/浏览器 |

单文件读取不授予服务器管理或业务权限。使用者自行设置业务 Key，按已有模型/任务/内容权限和额度调用；同源下载、摘要验证、提交前保存幂等状态、未知结果恢复以及八小时内容保留规则继续沿用。3D 仍限已发布的静态 GLB 流程。

## 2. 验证与维护

实现前核对线上当前生成服务实际为 `linux-20261030.137.1`，本次保留该运行版本及原进程，不拿本地旧部署包覆盖。专用 Nginx 监听 HTTP 39443，后端为回环 32880；只增加公开文档路由。

单文件和 ZIP 的来源、版本、摘要需一致。验证涵盖单文件独立执行、现有客户端回归、同平台及跨平台打包元数据、公开文档的 GET/HEAD、缓存/条件请求、任意来源读取、版本目录和不存在路径、生成服务健康及进程保持。文档与来源证据分别记录，不把本次静态发布当作新 GPU 或完整产品验收。

本地 [12 项客户端与单文件验证](evidence/2026-11-02/media-gen-single-file-131-local.json) 已通过：把生成文件独立保存为 SKILL.md，按文内命令提取客户端，使用隔离 Python 进程访问回环 HTTP、识别 GLB 扩展名并核对实际下载字节；不依赖原 Skill 的 scripts/references。生成文件与目录式 Skill 均通过官方 quick_validate，相关源文件 Ruff 通过。没有启动数据库、GPU、浏览器或虚拟化环境。

## 3. 使用 / Installation

打开 [最新单文件](https://gen.sub2share.xyz/skill.md)，下载后在 Agent 的 skills 目录保存为 `media-gen-skill/SKILL.md`，重新打开会话。Codex 可用 `$CODEX_HOME/skills/media-gen-skill/SKILL.md`；文件名按 Agent 规范使用大写，公开地址保留小写。文件内完整说明业务 Key 设置、模型发现、参数检查、参考图、提交/等待/恢复/下载和八小时期限。只有使用 CLI 时才按文内命令在工作目录提取 `media_gen.py`；该命令不覆盖已有脚本。

Download [the current file](https://gen.sub2share.xyz/skill.md), save it as `media-gen-skill/SKILL.md` in your agent's skills directory, and open a new session. Configure your business API Key yourself. The instructions, native API reference and complete standard-library client are embedded. Native HTTP tools can use the API directly; extract the client only for CLI use. After an update, deliberately replace an old extracted client or extract into a new working directory.

固定版本：[1.3.0/skill.md](https://gen.sub2share.xyz/skills/media-gen/1.3.0/skill.md)。同目录提供 [release.json](https://gen.sub2share.xyz/skills/media-gen/1.3.0/release.json)、[SHA256SUMS.txt](https://gen.sub2share.xyz/skills/media-gen/1.3.0/SHA256SUMS.txt) 和 [ZIP](https://gen.sub2share.xyz/skills/media-gen/1.3.0/media-gen-skill-1.3.0.zip)；用于来源核对、固定版本和回退。源站固定 `/skill.md` 使用 no-cache，版本入口使用一年 immutable；用户现有域名代理可覆盖缓存行为，实际观察见下文。任意来源可读取公开文档，生成/内容接口继续使用业务 Key。

## 4. 后续发布 / Maintainer workflow

1. 更新 `app/skills/media-gen-skill/` 的真实来源，递增 VERSION 和客户端 User-Agent，更新双语入口、CHANGELOG、专题及 `docs/releases/media-gen-skill/VERSION.md`，执行匹配测试与文档/证据检查；提交来源和构建器。
2. 执行 `python app/scripts/build-media-gen-skill.py --output app/.local/NEW_RELEASE_DIR`，在另一新目录重建并比较四项产物，校验摘要和单文件独立运行。不要直接手改生成的单文件，否则会与清单失配。
3. 推送源码和 `media-gen-skill-vVERSION` 标签到本仓库；既有 GitHub Actions 构建两次、校验并发行 ZIP、skill.md、SHA256SUMS.txt、release.json。匿名重新下载并核对后，再部署相同资产。**GitHub 标签发行不自动连接生产服务器**。
4. 用原生 SSH/SFTP 把四项资产放入云端暂存目录，同时上传 `app/deploy/native-linux/publish-public-skill.py` 和同目录的 `public-skills.nginx.conf`。先检查实际配置并取得 `/etc/comfyui-aggregator/nginx.conf` 当前 SHA-256，再运行下方固定部署器。无须重启 API、Runner、GC 或 Worker，无须改用户反代或证书。
5. 部署器校验完整产物、来源清单与不可变版本，备份 Nginx 配置、检查语法、原子切换 `current` 并平滑加载；回环验证失败恢复原配置和指针。再从外部核对固定/版本入口、完整摘要及健康状态，提交证据并推送。回退可用同一部署器，以已保留的历史版本目录为 `--release-dir`，仍须提供当时配置摘要。

```sh
# Run only on the authorized production Linux host; use the inspected config SHA.
sudo python3 /home/ubuntu/skill-publish/publish-public-skill.py \
  --release-dir /home/ubuntu/skill-publish/assets \
  --expected-config-sha256 OBSERVED_CONFIG_SHA256
```

The tag workflow publishes GitHub assets; the static publisher updates the production URL separately. It verifies checksums/manifest/ZIP members, refuses to overwrite a different immutable version, guards the inspected configuration hash, backs up configuration, tests Nginx and reloads it gracefully. Local HTTP verification checks GET/HEAD, ETag/304, arbitrary origins/OPTIONS, version/missing paths, live health, business authentication and the private-ingress boundary. It also requires every existing service PID and application release pointer to stay unchanged. Keep the previous version directory for rollback; do not delete history while long-lived caches may still reference it.

## 5. 当前 1.3.0 发行 / Current release

2026-11-04 已同步 GitHub 及原固定地址。来源 `4bc242fcf2dff14c30fa83469cee443937408984`，单文件 **46,550 字节**，SHA-256：

```text
ddce149dfc6ad7f713ad4a49c9ee75a57e251055d8471289d34ad115a4e59ee9
```

| 编号 | 核对 | 实际结果 |
| --- | --- | --- |
| SK130-01 | 客户端和单文件 | [17 项本地检查](evidence/2026-11-04/media-gen-skill-130-local.json) 通过，含独立单文件运行、GLB 下载、流式 multipart、命名视频/音频、转写与父任务输入 |
| SK130-02 | 真实业务 | [线上报告](evidence/2026-11-04/media-gen-skill-130-live.json)：音频、视频、GLB 上传，实时发现、持久化幂等提交、生图及字节/SHA-256 下载核对通过；合成输入，不代表全部候选已通过 |
| SK130-03 | 构建与源码 | [两次原生构建](evidence/2026-11-04/media-gen-skill-130-build.json) 的四项资产逐字节相同；内嵌客户端等于被测来源，Python 3.10 语法和两种形式的官方 Skill 验证通过 |
| SK130-04 | GitHub 发行 | [1.3.0 Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.3.0)；[匿名独立下载](evidence/2026-11-04/media-gen-skill-130-public-release.json) 的四项 Linux 资产与两次 Windows 构建一致 |
| SK130-05 | 原生静态发布 | [部署记录](evidence/2026-11-04/media-gen-skill-130-deployment.json) 通过，保留应用 `linux-20261104.133.4` 和五个原服务 PID，current 切到 1.3.0，1.2.0 保留 |
| SK130-06 | 对外固定/版本入口 | [高位入口和现有域名](evidence/2026-11-04/media-gen-skill-130-external.json) 的完整文件、GET/HEAD、缓存、任意源读取/预检、版本资产及历史、live/ready、业务鉴权与内部接口边界均通过 |

Version 1.3.0 adds typed streaming uploads, named media/text fields and compatible parent jobs. Required fields are explicit; example placeholders are never submitted. All four GitHub assets match both native Windows builds. The fixed URL serves the same verified file and preserves historical 1.2.0. Publication leaves the generation application, database and service PIDs unchanged. Local installed source uses a junction from the Codex skills directory to this project's canonical `app/skills/media-gen-skill/`; no unrelated skill repository was modified.

第一次本地匿名 GitHub 资产连接被重置，改由已授权原生云服务器匿名下载后经固定主机密钥 SSH 取回并核对，未改 TLS 校验或资产。外部 HEAD 核对显式请求 identity 编码，避免已有代理对压缩响应省略 Content-Length。域名条件请求返回完整新鲜 200，源站返回 304；文件字节、ETag 和摘要仍严格匹配。没有修改用户反代。

## 6. 历史 1.2.0 发行 / Previous release


2026-11-02 已上线，当前及版本入口均可匿名读取。文件 **35,951 字节**，SHA-256：

```text
ff96161dbce701bc1c6bcb08e817641e0ac38c2766034d75d2b339634bca1a59
```

| 核对 | 结果与证据 |
| --- | --- |
| 本地行为 | [12 项回归](evidence/2026-11-02/media-gen-single-file-131-local.json)；单文件独立提取/执行、真实回环 HTTP 和校验下载通过；两种 Skill 均通过 quick_validate |
| 来源与两次构建 | [构建报告](evidence/2026-11-02/media-gen-single-file-131-build.json)；来源 `ce982ff43a633e86c4faa6e3e99a7927510b0767`，四项资产两次一致，单文件等于被测候选，内嵌客户端具有 Python 3.10 语法 |
| GitHub 正式发行 | [1.2.0 Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.2.0)；[匿名独立下载报告](evidence/2026-11-02/media-gen-single-file-131-public-release.json)，四项 Linux 发行资产与两次 Windows 原生构建逐字节相同；校验摘要、标签、清单及全部 ZIP 成员通过 |
| 原生线上部署 | [部署报告](evidence/2026-11-02/media-gen-single-file-131-deployment.json) 和 [最终发布器/安装核对](evidence/2026-11-02/media-gen-single-file-131-publisher-checks.json)；实际安装与当前源码摘要一致，Ruff/Python 3.10 语法通过；保留 `linux-20261030.137.1`，五个服务 PID 和应用指针未变，Nginx 语法、GET/HEAD、条件请求、CORS、版本/不存在路径、健康及鉴权边界通过 |
| 高位入口及域名 | [38 项外部检查](evidence/2026-11-02/media-gen-single-file-131-external.json)；固定/版本四项文件完整匹配、UTF-8 Markdown、GET/HEAD、任意来源 GET/OPTIONS、写入拒绝、缺失路径、live/ready、原 Admin/Studio 和业务鉴权均通过 |
| 失败自动恢复 | 首次缺少 Markdown charset，第二次 OPTIONS 被方法过滤提前拒绝；两次均实际恢复原配置/指针并核对五个原 PID。保留 [首次回退](evidence/2026-11-02/media-gen-single-file-131-first-deploy-rollback.json)、[第二次原因](evidence/2026-11-02/media-gen-single-file-131-second-deploy-preflight.json) 及 [第二次回退](evidence/2026-11-02/media-gen-single-file-131-second-deploy-rollback.json)，修正 charset_types 和允许 OPTIONS 后按原断言通过 |

域名已有代理会追加重复 no-cache；本次观察到 CF-Cache-Status 为 DYNAMIC，条件请求返回相同 ETag/完整摘要的 200，而直接高位入口返回 304。初始外部探针把缓存头原文及域名 304 写死而停止，最终按实际代理语义核对：源站仍严格要求 304，域名允许正确 304 或带相同 ETag/摘要、DYNAMIC/no-cache 的完整 200。无需改变用户代理，说明读取仍为最新发行内容；公开文件字节和业务鉴权断言没有放宽。

Version 1.2.0 is published at both URLs and on GitHub. All four public release assets match both native Windows builds byte for byte. All 12 local client cases and 38 external HTTP checks pass. The existing application release and five service PIDs are preserved. Two initial Nginx integration failures were rolled back and recorded before correcting Markdown charset and OPTIONS handling; strict origin assertions pass after the fix. The existing domain proxy returns fresh 200 for conditional requests while the origin returns 304, documented separately. No GPU generation, browser, database migration, local virtualization or user reverse-proxy change was required.

历史 ZIP 与 1.1.x Release 保留。没有创建新的常驻服务；线上仅增加静态版本目录、current 指针和文档路由，备份/部署记录留作恢复。本次分发验收不提升整个产品、10～20 台实机或其他未完成工作包的验收状态。
