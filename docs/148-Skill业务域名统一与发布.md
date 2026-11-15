# 148 · Skill 业务域名统一与发布

2026-11-16，Asia/Taipei。按用户要求，Media Gen Skill **1.4.1** 统一使用业务域名 **https://gen.sub2share.xyz**。本次修正 1.4.0 原生 ComfyUI 示例仍推荐服务器 IP 的问题；两个客户端原有默认值已经是该域名，继续沿用。

| 编号 | 用途 | 统一入口与规则 |
| --- | --- | --- |
| DOMAIN-01 | 业务模型 API | `https://gen.sub2share.xyz/v1/models`、`/v1/jobs` 等；`media_gen.py` 默认使用业务域名。 |
| DOMAIN-02 | 原生 ComfyUI | `https://gen.sub2share.xyz/origin/` 登录，`/origin/list` 选设备；`comfy_origin.py` 的会话、上传、执行和下载均保持同域。 |
| DOMAIN-03 | Skill | [最新 skill.md](https://gen.sub2share.xyz/skill.md)、[固定 1.4.1](https://gen.sub2share.xyz/skills/media-gen/1.4.1/skill.md)；目录式和单文件指令、两份参考及两个客户端同步。 |
| DOMAIN-04 | 配置 | 默认无需设置地址；如显式配置，使用 `MEDIA_GEN_BASE_URL=https://gen.sub2share.xyz`，不能带 `/origin`、账号、查询串。不同部署仍可显式覆盖。 |
| DOMAIN-05 | 迁移 | 旧地址的 Cookie/会话文件不能跨源复用；使用新的私有会话文件在域名登录。未决旧请求保留原始地址、图和幂等标识，先在原源核对，不能改地址盲目重提。 |
| DOMAIN-06 | 发布约束 | 版本递增为 1.4.1，保留 1.4.0；两次原生打包、匿名下载核对、同资产静态发布。只切换 Skill，不升级应用/Worker，不改用户域名反代、证书、设备维护占用或不限额策略。 |

```powershell
$env:MEDIA_GEN_BASE_URL = 'https://gen.sub2share.xyz' # 可省略，两个客户端的默认值相同
python scripts/media_gen.py models
python scripts/comfy_origin.py --session-file private/domain-session.json login
python scripts/comfy_origin.py --session-file private/domain-session.json devices
```

Key 通过 `MEDIA_GEN_API_KEY` 私下设置。原生设备仍须由 Admin 独立授权。其余能力、归属、未知提交恢复和设备本地素材保留规则沿用 [147](147-Skill原生ComfyUI操作与发布.md)。本轮不新增生成任务，不以域名连通性代替模型质量或全设备验收。

## 验证与发布结果

| 编号 | 验证 | 实际结果与证据 |
| --- | --- | --- |
| DOMAIN-V01 | 客户端回归 | [35 项通过](evidence/2026-11-16/skill-domain-141-local.json)，来源运行前后摘要一致；Ruff、官方目录式 Skill 校验和 Python 3.10 语法通过。 |
| DOMAIN-V02 | 打包与默认地址 | [两次原生构建一致](evidence/2026-11-16/skill-domain-141-build.json)，独立提取两个客户端运行帮助；AST 核对默认地址和清单均为业务域名，单文件不含旧服务器 IP 或高端口示例。 |
| DOMAIN-V03 | 域名实际调用 | [6 项通过](evidence/2026-11-16/skill-domain-141-live.json)：两个独立提取客户端不覆盖地址，实际业务鉴权读取模型列表、原生域名登录、会话、获准设备目录与退出。该 Key 可见 45 个业务模型和 3 台设备；目录可见不代表设备空闲或全部可生成。测试会话已注销及删除。 |
| DOMAIN-V04 | GitHub | [1.4.1 Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.4.1) 正式发布；[标签流水线及匿名下载](evidence/2026-11-16/skill-domain-141-public-release.json) 确认四项资产与本地两次构建逐字节相同。 |
| DOMAIN-V05 | 线上静态部署 | [current 已切至 1.4.1](evidence/2026-11-16/skill-domain-141-deployment.json)；原应用 `linux-20261115.146.6`、五个服务主进程、主配置与既有 CORS include 不变；1.3.0、1.4.0 文件摘要保持相同。 |
| DOMAIN-V06 | 公网入口 | [48 项检查通过](evidence/2026-11-16/skill-domain-141-external.json)，覆盖业务域名和源站的最新/固定四项资产、HEAD/条件请求、任意来源预检、鉴权、健康、原生入口及旧版本。沿用用户既有反代，不审计或修改 TLS/证书配置。 |
| DOMAIN-V07 | 最终核对 | [来源、门禁与收尾](evidence/2026-11-16/skill-domain-141-final-checks.json) 核对当前源码摘要、本地安装链接、域名发行资产、各层证据及测试会话删除。 |

发行来源：`c1a8eedbceafea3bf8373c21f009e228e2f22f92`。单文件 **91,131 字节**，SHA-256 为 `4e3251018531af8adf8512e527a01e164dd204d00e15488dc0dab59828bbcc73`。本地安装链接指向当前仓库来源，已同步 1.4.1；其他单文件用户重新下载后在新会话加载，旧提取脚本须在新目录提取或明确替换。

本轮没有设备占用、素材上传或生成提交，不改变 API Key 权限、配额和设备维护状态。[发布隐私扫描](evidence/2026-11-16/skill-domain-141-publication-scan.json) 私下匹配真实凭据，不输出值。历史证据不改写；维护流程见 [131](131-单文件Skill与固定在线地址.md)。
