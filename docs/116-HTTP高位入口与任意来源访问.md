# 116 · HTTP 高位入口与任意来源访问

用户明确要求：服务器只开放高位 HTTP 端口，域名和 HTTPS 由用户自行反代；关闭 CORS 来源约束，允许任意源访问。这是对原生产 HTTPS / 同源配置的明确调整，适用于此次部署。

## 配置与行为

新增 `GEN_ALLOW_HTTP` 和 `GEN_ALLOW_ANY_ORIGIN`，默认均为 false，保留既有部署行为。本服务器显式设置两项为 true，`GEN_PUBLIC_ORIGIN=http://<服务器>:39443`。39443 不再配置 TLS，临时 IP 证书不用于入口。继续使用 production 模式、真实 PostgreSQL 18 和私有版本化 S3。

任意来源模式在完整 ASGI 错误栈之外响应 CORS：反映请求 Origin、允许凭证、允许所有标准 API 方法、反映全部预检请求头，并暴露下载范围、ETag、请求编号等响应头。使用 `Vary: Origin` 避免缓存混用；不能对携带 Cookie 的跨域请求只返回无效的 `*`。Admin 不再因调用 Origin 不同而拒绝登录、写入或交互续期，但写操作仍验证 CSRF token、会话、角色、版本和幂等键。业务 API Key 仍须通过鉴权、资源范围、额度及其配置的 IP 限制；开放 CORS 不会绕过这些访问控制。

显式 HTTP 部署的管理 Cookie 使用 HttpOnly / SameSite=Lax，以支持同源 HTTP 管理页登录。公开源改为 HTTPS 后 Cookie 自动使用 Secure；同时开启任意来源时使用 SameSite=None，支持浏览器的跨站凭证请求。Cookie 的浏览器规则与 Bearer Key 的任意源访问分别验证。

现有独立入口覆盖上游 Host 和真实客户端地址，API 只信任回环代理。用户将域名反代至普通 HTTP 39443 即可；界面同源 API 使用相对路径。需要按新公开域名生成绝对链接或使用跨站管理 Cookie 时，再更新 `GEN_PUBLIC_ORIGIN` 并重启本项目服务。内部路径不因 CORS 开放而变成公网管理通道。

## 兼容与验证

不增加数据库迁移，不修改 Worker 协议、任务状态或生成计量。涉及 DEV-05、14、17、18、24、27、31、32。验证默认配置仍要求生产 HTTPS、显式 HTTP 管理登录、任意来源预检/401/500、不同来源的登录与 CSRF 写入、来源伪造不能绕过 Key IP 限制；再对服务器最终 HTTP 入口实测。前期 HTTPS 报告不替代最终 HTTP / CORS 证据。

实际浏览器复验发现普通公网 HTTP 不提供 `crypto.randomUUID()`，使原前端的登录请求在发出前失败。Admin 启动时现在仅在缺少该方法时安装兼容实现，使用 HTTP 下可用的 `crypto.getRandomValues()` 生成标准 v4 UUID；原生实现存在时保持原方法。登录、幂等键以及各管理组件共享该入口，避免只修登录而遗留 Workflow / Key 写入。复制凭据仍受浏览器剪贴板能力约束，可手动选中保存。

| 编号 | 验证 | 结果与证据 |
| --- | --- | --- |
| 01 | HTTP/CORS、相关鉴权、权限与监控 | 309 项 Python 专项通过，零失败/错误/跳过；[当前来源报告](evidence/2026-10-27/http-cors-component-116.json)。此前 59 / 89 项是较窄检查，不与这 309 项相加计数。 |
| 02 | 普通 HTTP 请求 UUID | 两项专项检查原生方法保留、安全随机数、v4 格式、128 次无重复和重复初始化；前端类型检查通过。[报告](evidence/2026-10-27/http-admin-uuid-116.json)。 |
| 03 | 公网 HTTP 与 CORS | 最终 116.2 的 55 项通过，覆盖任意 HTTPS/http/null Origin、预检方法/头、401、跨源 Admin 与 CSRF、Key 范围与 IP 防伪、输入上传和 Range/HEAD 下载；探针 Key/文件删除、会话退出。[最终报告](evidence/2026-10-27/linux-public-http-cors-116-2.json)。116.1 的 55 项仅为更早 API 检查，浏览器兼容修复后另复验。 |
| 04 | 实际浏览器 | 普通公网 HTTP 登录、总览、渠道、Workflow、模型、API Key 页面及移动总览；[截图与原速录屏](media/linux-deployment-116/README.md)、[浏览器记录](evidence/2026-10-27/linux-admin-browser-116.json)。使用实际空业务数据库，不注入 fixture，也不使用浏览器安全例外。 |
| 05 | 平台运行 | [Linux 运行、服务、数据库、对象和备份记录](evidence/2026-10-27/linux-runtime-116.json)；单独保留真实生成未签收状态。 |

用户后续配置反代时，只需把上游指向普通 HTTP `39443`，保留 `/admin/` 与 `/v1/` 路径、请求体和 Range 请求。公开域名改变时再修改 `GEN_PUBLIC_ORIGIN`，通过服务重启生效。若反代增加另一跳，应按实际代理拓扑核对可信地址与真实客户端 IP；不能为兼容新反代而直接相信公网任意 `X-Forwarded-For`。
