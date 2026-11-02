# 131 · 单文件 Skill 与固定在线地址

按用户要求，提供可独立安装和调用的 `skill.md`，并部署到现有服务的固定公开地址。说明、原生 API 用法和 Python 标准库客户端全部嵌入同一文件；不把依赖外部 scripts/references 的入口文件直接冒充单文件。

## 1. 交付与边界

| 编号 | 交付 | 约束 |
| --- | --- | --- |
| SF01 | 最新入口 `https://gen.sub2share.xyz/skill.md` | 公开 UTF-8 Markdown；无需业务 Key 读取说明；生成和内容 API 仍保持原鉴权 |
| SF02 | 版本入口 `/skills/media-gen/1.2.0/skill.md` | 版本目录不可覆盖；latest/current 原子切换；保留历史以便回退 |
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

固定版本：[1.2.0/skill.md](https://gen.sub2share.xyz/skills/media-gen/1.2.0/skill.md)。同目录提供 [release.json](https://gen.sub2share.xyz/skills/media-gen/1.2.0/release.json)、[SHA256SUMS.txt](https://gen.sub2share.xyz/skills/media-gen/1.2.0/SHA256SUMS.txt) 和 ZIP；用于来源核对、固定版本和回退。固定 `/skill.md` 每次重新验证缓存；版本入口可长时缓存。任意来源可读取公开文档，生成/内容接口继续使用业务 Key。

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

实际公开发行、部署和外部读取的版本化结果在完成后追加于此；历史 ZIP 与 1.1.x Release 保留。
