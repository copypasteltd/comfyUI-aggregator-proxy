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

验证与正式发布结果完成后在此追加；历史证据不改写。维护流程见 [131](131-单文件Skill与固定在线地址.md)。
