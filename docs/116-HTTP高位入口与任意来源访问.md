# 116 · HTTP 高位入口与任意来源访问

用户明确要求：服务器只开放高位 HTTP 端口，域名和 HTTPS 由用户自行反代；关闭 CORS 来源约束，允许任意源访问。这是对原生产 HTTPS / 同源配置的明确调整，适用于此次部署。

## 配置与行为

新增 `GEN_ALLOW_HTTP` 和 `GEN_ALLOW_ANY_ORIGIN`，默认均为 false，保留既有部署行为。本服务器显式设置两项为 true，`GEN_PUBLIC_ORIGIN=http://<服务器>:39443`。39443 不再配置 TLS，临时 IP 证书不用于入口。继续使用 production 模式、真实 PostgreSQL 18 和私有版本化 S3。

任意来源模式在完整 ASGI 错误栈之外响应 CORS：反映请求 Origin、允许凭证、允许所有标准 API 方法、反映全部预检请求头，并暴露下载范围、ETag、请求编号等响应头。使用 `Vary: Origin` 避免缓存混用；不能对携带 Cookie 的跨域请求只返回无效的 `*`。Admin 不再因调用 Origin 不同而拒绝登录、写入或交互续期，但写操作仍验证 CSRF token、会话、角色、版本和幂等键。业务 API Key 仍须通过鉴权、资源范围、额度及其配置的 IP 限制；开放 CORS 不会绕过这些访问控制。

显式 HTTP 部署的管理 Cookie 使用 HttpOnly / SameSite=Lax，以支持同源 HTTP 管理页登录。公开源改为 HTTPS 后 Cookie 自动使用 Secure；同时开启任意来源时使用 SameSite=None，支持浏览器的跨站凭证请求。Cookie 的浏览器规则与 Bearer Key 的任意源访问分别验证。

现有独立入口覆盖上游 Host 和真实客户端地址，API 只信任回环代理。用户将域名反代至普通 HTTP 39443 即可；界面同源 API 使用相对路径。需要按新公开域名生成绝对链接或使用跨站管理 Cookie 时，再更新 `GEN_PUBLIC_ORIGIN` 并重启本项目服务。内部路径不因 CORS 开放而变成公网管理通道。

## 兼容与验证

不增加数据库迁移，不修改 Worker 协议、任务状态或生成计量。涉及 DEV-05、14、17、18、24、27、31、32。验证默认配置仍要求生产 HTTPS、显式 HTTP 管理登录、任意来源预检/401/500、不同来源的登录与 CSRF 写入、来源伪造不能绕过 Key IP 限制；再对服务器最终 HTTP 入口实测。前期 HTTPS 报告不替代最终 HTTP / CORS 证据。
