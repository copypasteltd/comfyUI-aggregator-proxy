# 147 · Skill 原生 ComfyUI 操作与发布

**后续 1.4.1 域名统一**见 [148](148-Skill业务域名统一与发布.md)；当前安装与调用请使用 **https://gen.sub2share.xyz**。本文保留 1.4.0 的实际高端口验证记录，不改写其来源或资产。

2026-11-16，Asia/Taipei。按用户要求升级 Media Gen Skill 至 **1.4.0**，明确允许 Skill 使用者/Agent 通过已上线的 `/origin/` 直接操控获准 ComfyUI。沿用 [146 的原生会话、独占和数据边界](146-原生ComfyUI面板访问与独占操作.md)，不通过静态 Skill 发布升级应用服务或 Worker。

## 能力与授权

| 编号 | 内容 | 实际行为及开发约束 |
| --- | --- | --- |
| SK140-01 | 原生模式发现 | 主说明与发现描述明确可直接操作 ComfyUI；读取详细原生参考。标准业务调用继续使用实时 `/v1/models` 及冻结参数，自定义原生图使用源 `object_info`/模型枚举。 |
| SK140-02 | 授权 | Admin 对 Key 独立启用原生面板并选择设备，与既有渠道权限取交集。任务已授权且设备获准后，必要读取、上传、提交无需逐步重复确认。无限额业务 Key 本身不代表原生授权。 |
| SK140-03 | 独立客户端 | 新增 Python 3.10+ 标准库 `comfy_origin.py`，原 `media_gen.py` 保持业务接口行为。通过 Cookie/CSRF 使用原生 API；只在登录时传 Key，不向设备转发。 |
| SK140-04 | 图编辑和提交 | 查询实际节点/模型，修改独立 API 格式图及节点参数；支持现有图片、视频、音频、3D 等节点。UI nodes/links 导出不能被猜测成 API 图。最多 3,000 节点、8 MiB；不安装节点、下载权重或运行主机命令。 |
| SK140-05 | 上传 | 127 MiB 有界流式 multipart，原生 image 字段用于各种开放文件扩展。使用实际回执的随机 input 文件描述符；遮罩调用真正 upload/mask 且 original_ref 必须归本 Key。 |
| SK140-06 | 持久意图 | 请求前排他落盘原图、摘要、幂等标识、源与凭证指纹。未知回执只恢复相同意图，服务器不重发已存在的未知图；已知 prompt_id 只读。禁止换源、换标识盲目重跑。 |
| SK140-07 | 等待与结果 | 有界轮询 owned history，并每分钟刷新目录维持占用。递归收集文件描述符，支持图像/视频/音频/3D 多文件；缺描述符的自定义节点不承诺。浏览器有 WS，本客户端不伪称提供 WS 实现。 |
| SK140-08 | 下载 | 同源、拒绝跳转及路径穿越，保留已有文件，2 GiB/30 分钟上限。报告本地 SHA-256；只有另行提供独立摘要时才标记 source_checksum_verified，不能把本地哈希当独立源验证。 |
| SK140-09 | 个人数据 | 保存/读取/删除 Key/设备自己的 Workflow，默认不覆盖。取消仅限明确要求的本 Key 待执行任务，中断仅请求停止本 Key 当前执行；不把请求中断当停止证明。 |
| SK140-10 | 归还与退出 | 显式 release，closing 继续核对、closed 才完成。掉线、关闭页面、超时和 logout 不证明 GPU 空闲；不清除维护或未知占用。 |
| SK140-11 | 隐私与生命周期 | 会话文件含派生凭据，放私有目录、不得提交；请求状态含图及提示词同样保密。原生结果留在设备，不计入业务用量、云端内容或八小时删除。 |
| SK140-12 | 单文件与发行 | 两个客户端和两份参考从提交来源编译到一个 skill.md，ZIP/单文件/清单一致；两次原生构建、独立提取、真实调用、不可变 GitHub Release 与线上 current 原子切换分别记录。禁止本地 Docker/WSL/虚拟机。 |

## 调用逻辑

设置 `MEDIA_GEN_BASE_URL=http://43.165.167.179:39443` 和私下提供的 `MEDIA_GEN_API_KEY`，从 Skill 源目录运行；单文件安装按文内命令提取后去掉 `scripts/`。

```sh
python scripts/comfy_origin.py login
python scripts/comfy_origin.py devices
python scripts/comfy_origin.py acquire DEVICE_SLUG
python scripts/comfy_origin.py get DEVICE_SLUG object_info/EmptyImage
python scripts/comfy_origin.py get DEVICE_SLUG models
python scripts/comfy_origin.py submit DEVICE_SLUG api-workflow.json --state-file private/native-request.json
python scripts/comfy_origin.py wait private/native-request.json --timeout 900 --output-dir native-output
python scripts/comfy_origin.py release DEVICE_SLUG
python scripts/comfy_origin.py logout
```

`DEVICE_SLUG` 取目录真实值；不要把 GPU 名或私网 IP 当代号。详细图准备、720×1080 节点参数、多模态文件绑定、恢复、个人 Workflow、取消及原生 HTTP 表见 [Skill 原生参考](../app/skills/media-gen-skill/references/comfy-origin.md)。默认 `.comfy-origin-session.json` 含访问凭据；使用 `--session-file private/session.json` 时所有命令保持同一路径。

用户可用浏览器进入 `/origin/` 登录，在 `/origin/list` 申请设备后打开返回的原始面板；已有[真实截图、录屏与 GIF](media/origin-146/README.md) 记录平台页面。本次仅更新 Skill，未更改页面样式，既有页面证据与本轮客户端证据分开。本轮使用原生 HTTP，未启动浏览器、本地 Docker、WSL 或虚拟机。

## 验证及发布记录

| 编号 | 验证 | 结果与证据 |
| --- | --- | --- |
| SK140-V01 | 客户端回归 | [35 项通过](evidence/2026-11-16/skill-native-140-local.json)，含原业务 17 项与原生 18 项；来源在运行前后摘要相同，Ruff、官方 Skill 验证与 Python 3.10 语法检查通过。 |
| SK140-V02 | 可复现打包 | [两次原生构建完全一致](evidence/2026-11-16/skill-native-140-build.json)，四项资产逐字节比较；独立提取两个客户端并运行帮助命令。提取代码按通用换行比较，发行资产按原始字节比较。 |
| SK140-V03 | 真实原生调用 | [5070 的 15 项检查通过](evidence/2026-11-16/skill-native-140-live.json)：实际登录、设备/节点/模型目录、占用、上传及已知摘要下载、个人 Workflow 增读删、自定义图提交、历史和队列、下载、恢复、归还及退出。测试占用已 closed、会话文件已删除。 |
| SK140-V04 | GitHub 不可变发行 | [1.4.0 Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.4.0) 已发布；[标签流水线与匿名下载](evidence/2026-11-16/skill-native-140-public-release.json) 确认四项资产与本地两次构建完全相同。当地 GitHub API 共享出口限额及资产重定向连接受限时，改从已授权 Linux 主机匿名下载，经固定主机密钥 SSH 回传后逐字节比较，无 GitHub token。 |
| SK140-V05 | 线上静态更新 | [部署记录](evidence/2026-11-16/skill-native-140-deployment.json)：`current` 从 1.3.0 切至 1.4.0，应用仍为 `linux-20261115.146.6`；5 个服务主进程、主配置、既有静态 include 及历史 1.3.0 字节保持相同。 |
| SK140-V06 | 外部 HTTP | [23 项检查通过](evidence/2026-11-16/skill-native-140-external.json)：固定/版本四项资产、HEAD、304、任意来源/方法/请求头/私网预检、旧版本、鉴权、健康、Admin、Studio 及原生入口。仅检查 HTTP 39443，不验证 HTTPS 或修改用户反代。 |
| SK140-V07 | 收尾与隐私 | [最终核对](evidence/2026-11-16/skill-native-140-final-checks.json) 确认来源摘要、目录式本地安装、各阶段报告与文档门禁；[范围化发布扫描](evidence/2026-11-16/skill-native-140-publication-scan.json) 检查本轮来源、文档和全部发行资产，私下匹配 10 项真实凭据，无发现。 |

发行来源固定为 `8d993c84905cbca369674e83164a8a79b5cbaf3d`。单文件 **89,876 字节**，SHA-256 为 `a68d893b4126e5b69e85f68044a020831ac5865024b5012b4da8249c4630c22c`。本地已安装的 `media-gen-skill` 指向仓库来源，随源码同步；单文件安装用户需重新下载并在新会话加载，旧的已提取客户端需明确替换或提取到新目录。

已核对的在线入口：[最新 skill.md](http://43.165.167.179:39443/skill.md) · [固定 1.4.0](http://43.165.167.179:39443/skills/media-gen/1.4.0/skill.md) · [来源清单](http://43.165.167.179:39443/skills/media-gen/1.4.0/release.json)。原有域名路径继续由用户反代提供，本轮不验证其 HTTPS。

静态 include 保留了 145 的受管 CORS 块及更广的暴露响应头，与仓库安装模板的内联写法不同。初次摘要相等保护在暂存或发布前停止；只读复查差异后，发布器显式使用 `--snippet /etc/comfyui-aggregator/public-skills.conf` 保留已运行配置。主配置和 include 的前后摘要均一致。后续维护也应先检查实际文件，不能用安装模板覆盖现有配置。

真实执行为小型 `EmptyImage → SaveImage` 64×64 CPU 诊断，只验证实际协议链路，不证明训练模型质量、其他模态或设备全部验收。3080/4090 没有本轮提交、重启或强制解锁。原生结果下载报告本地哈希，未冒充独立源摘要。已有设备维护、普通业务绑定就绪、历史候选发布、Sub2API 与 10～20 台容量缺口不因 Skill 更新而消除。

源码入口：[Skill](../app/skills/media-gen-skill/SKILL.md)、[客户端](../app/skills/media-gen-skill/scripts/comfy_origin.py)、[构建器](../app/scripts/build-media-gen-skill.py)。发行维护沿用 [131](131-单文件Skill与固定在线地址.md)。后续更新必须递增版本、更新中英说明和更新日志、验证相应能力并同步 GitHub 及线上单文件地址；不能只改本地安装副本。
