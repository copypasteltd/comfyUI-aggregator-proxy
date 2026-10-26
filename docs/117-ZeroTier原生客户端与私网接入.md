# 117 · ZeroTier 原生客户端与私网接入

2026-10-27，按用户明确指令，在既有 Ubuntu 控制面服务器原生安装并运行 ZeroTier，加入用户指定网络；用户在控制器授权新成员后，已取得管理地址并实测跨机器 HTTP / SSH。涉及 DEV-01、02、03、05、25、29、31；应用源码、数据库、API 契约、任务状态与 Worker 协议均未变更。

## 安装与持久化配置

| 编号 | 项目 | 实际结果 |
| --- | --- | --- |
| 01 | 平台与版本 | Ubuntu 24.04 / amd64，`zerotier-one 1.16.2`，通过官方 Noble APT 软件源安装。 |
| 02 | 来源核验 | 官方签名密钥指纹 `74A5E9C458E1A431F1DA57A71657198823E52A61`；核验官方安装器签名，实际采用 APT 安装固定版本。软件源为 `https://download.zerotier.com/debian/noble`，`Signed-By` 限定到专用 keyring。 |
| 03 | 依赖影响 | 只新增 ZeroTier 一个包，未升级或删除其他包。原有防火墙已允许 UDP 9993，无需新增规则。 |
| 04 | 服务 | `zerotier-one.service` 为 active / enabled，节点 ONLINE；加入的网络成员状态为 `OK`，已分配一个 IPv4，接口 UP。 |
| 05 | 网络配置 | `allowManaged=1`，`allowGlobal=0`，`allowDefault=0`，`allowDNS=0`；实测原默认 IPv4 路由和 DNS 内容摘要一致。 |
| 06 | 身份与成员持久化 | 工作目录 `/var/lib/zerotier-one/`，网络配置保存在 `networks.d/`。节点 identity 与授权状态长期保留；不要在缓存清理中删除该目录，重建身份将需要重新授权。 |
| 07 | 原服务 | 安装前记录的 64 个运行服务，在安装后检查均 active。既有 HTTP 39443、任意来源 CORS 和业务 Key 鉴权继续有效。 |

网络 ID、节点 ID、私网地址通过会话及服务器受限交接文件提供；公开报告仅保留网络 ID 的 SHA-256，不将完整网络拓扑、成员列表、`identity.secret` 或 `authtoken.secret` 写入仓库。

## 加入与授权逻辑

服务器执行 `join` 后，客户端会向指定网络控制器请求配置。`200 join OK` 只表示本机已接受加入操作；必须继续检查 `listnetworks`。本次首次返回 `ACCESS_DENIED`，用户完成控制器授权后变为 `OK` 并获得地址。只有后者及实际通路检查通过，才记录连接已完成；不能把节点 ONLINE 与网络已授权混为一谈。

日常检查命令：

```sh
sudo systemctl status zerotier-one.service
sudo zerotier-cli info
sudo zerotier-cli listnetworks
sudo zerotier-cli listpeers
```

需要在其他已获授权的服务器重复接入时，先配置上述官方签名软件源，再安装固定版本；将下例 `NETWORK_ID` 替换为实际的 16 位网络 ID。控制器若为私有网络，仍须分别授权新节点。

```sh
sudo apt-get install --no-install-recommends zerotier-one=1.16.2
sudo systemctl enable --now zerotier-one.service
sudo zerotier-cli join NETWORK_ID
sudo zerotier-cli set NETWORK_ID allowManaged=1
sudo zerotier-cli set NETWORK_ID allowGlobal=0
sudo zerotier-cli set NETWORK_ID allowDefault=0
sudo zerotier-cli set NETWORK_ID allowDNS=0
sudo zerotier-cli listnetworks
```

## 实际验证与边界

| 编号 | 验证 | 结论与证据 |
| --- | --- | --- |
| V01 | 原生安装与授权成员 | 服务 active / enabled、节点 ONLINE、指定成员 OK、地址分配、接口 UP、成员文件持久化均通过。[服务器记录](evidence/2026-10-27/zerotier-117.json)。 |
| V02 | 路由、DNS 与原服务 | 原默认路由及 DNS 摘要一致，64 个安装前运行服务均 active；通过分配的私网地址请求 `/health/live`、`/health/ready`、`/admin/` 均 200。属于服务器对本机接口检查。 |
| V03 | 真正跨机器访问 | 从本次工作区已有的原生 Windows ZeroTier 接口，访问服务器私网地址的上述三个路径均 200；`/v1/models` 无 Key 为 401，四个响应均正确反映任意 Origin。[跨机器记录](evidence/2026-10-27/zerotier-peer-117.json)。 |
| V04 | 私网 SSH 身份 | 通过私网地址完成 SSH 登录，远端主机密钥与此前公网连接固定的 Ed25519 密钥一致，并查询服务为 active；检查结束关闭连接。 |

ZeroTier 常驻服务按用户要求保留运行。本轮未启动浏览器、Docker、WSL 或其他虚拟化环境。应用与 UI 没有变更，保留 [116 的实际后台截图和录屏](media/linux-deployment-116/README.md)；未重新执行完整 Python / Windows / GPU 测试。私网连通不等于 Windows Worker 注册、ComfyUI 渠道或真实六类 Workflow 已接入；内部 API 的既有监听与鉴权策略仍按实际部署配置执行。

官方依据：[Linux 下载与软件源](https://www.zerotier.com/download/)、[CLI 与网络状态](https://docs.zerotier.com/cli/)、[客户端管理路由与 DNS 配置](https://docs.zerotier.com/config/)。
