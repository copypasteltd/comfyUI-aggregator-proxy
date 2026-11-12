# 145 · 公网入口统一开放 CORS

2026-11-13，按用户要求关闭全部请求的 CORS 约束。本次覆盖项目的公网 HTTP 39443：API、Admin、Studio、Skill、错误响应和预检；域名反代继续由用户管理。业务身份、管理会话及 CSRF、实际路由方法、内部网络入口的访问控制继续按既有协议执行。

初始实测：普通任意来源 API 请求和预检已允许，但非标准方法预检返回 `Disallowed CORS method`，带 `Access-Control-Request-Private-Network` 的预检返回 `Disallowed CORS private-network`；Skill 地址只声明少数允许头，Studio 静态地址及入口直接错误缺少统一跨域声明。

在专用公网入口统一处理预检、反映 Origin/请求方法/请求头、允许凭证和 Private Network Access 声明。避免携带凭证时使用无效的通配来源；不从跨域请求直接推导任何业务权限。隐藏上游重复 CORS 头，保留其他上游响应头与缓存维度。现有 location 自带响应头时补齐同一策略，保留 Studio CSP、Skill 资产和内部路径屏蔽。

原生配置工具使用当前摘要核对、受限备份、语法校验、平滑 reload 和失败回退。只更新专用入口配置，不重启 API、Worker、数据库或 Windows 设备；不修改任务状态、Key 或 Workflow。Skill 内容不变，发布工具后续复用新跨域策略，不递增 Skill 版本。

状态：**2026-11-13 00:18（Asia/Taipei）已线上生效**。专用 Nginx 入口完成语法校验及平滑 reload；API、Runner、GC、HTTP Worker 和入口主进程 PID 均保持不变，应用仍为 `linux-20261104.152.1`。

## 当前响应规则

| 编号 | 请求情况 | 响应逻辑 |
| --- | --- | --- |
| CORS-01 | 任意 HTTPS/HTTP Origin 或 `null` | 精确反映 Origin，允许凭证；响应不会叠加两份 Allow-Origin |
| CORS-02 | 带 Origin 和 Access-Control-Request-Method 的 OPTIONS | 公网入口统一返回 204，反映任意请求方法及请求头，包括 Authorization、自定义头、Idempotency-Key 和 Range |
| CORS-03 | Private Network Access 预检 | 返回 Access-Control-Allow-Private-Network: true；浏览器本身的私网授权规则仍由客户端处理 |
| CORS-04 | 普通 API、Admin、Studio、Skill 请求 | 统一跨域响应头；关键下载、范围、缓存和请求编号响应头可供前端读取 |
| CORS-05 | 401、404 等错误 | 使用 always 输出跨域头，使调用方读取实际业务错误；底层代理错误采用同一规则，本次未在生产人为制造 500/502 |
| CORS-06 | 实际路由不支持的方法或未授权访问 | 仍返回原路由/鉴权结果；预检许可不新增接口、不使未发布模型可见，也不开放内部 Worker 数据 |
| CORS-07 | 没有 Origin 的请求 | 保持普通 HTTP 行为；不凭空生成客户端来源 |

预检响应携带 600 秒 Max-Age，以及 Origin、请求方法、请求头和私网预检的 Vary。保留上游已有的其他 Vary 维度。此配置解决服务端 CORS；没有修改 HTTP Cookie 的 SameSite/Secure 属性，也没有规避浏览器自身的 Cookie、混合内容或私网权限策略。本次没有运行浏览器，不将 HTTP 探针当作浏览器联合验收。

## 安装与后续维护

既有安装使用 [原生配置工具](../app/deploy/native-linux/open_cors.py)，先读取两个当前配置摘要，再在已授权 Linux 主机运行：

```sh
sudo python3 open_cors.py \
  --expected-config-sha256 INSPECTED_NGINX_SHA256 \
  --expected-skills-sha256 INSPECTED_SKILLS_SHA256
```

固定目标为 `/etc/comfyui-aggregator/nginx.conf` 及 `public-skills.conf`，不接受客户指定路径或命令。摘要变化、找不到唯一公网监听、重复应用、解析失败或服务未运行均拒绝；修改前保留受限备份。语法、重载及验证失败恢复原配置。只定位专用公网 server，私网与回环监听不改；Studio CSP、静态资源根、Skill 版本和内部路径屏蔽保留。

[新装模板](../app/deploy/native-linux/nginx.conf.example)、[Skill 静态模板](../app/deploy/native-linux/public-skills.nginx.conf) 与 [Skill 发布器校验](../app/deploy/native-linux/publish-public-skill.py) 已同步。后续新增静态 location 如自带 `add_header`，必须复用同一跨域声明，防止 Nginx 的响应头继承规则使其遗漏。模板中的开放策略作用于公网入口；应用内部的 `GEN_ALLOW_ANY_ORIGIN=true` 保持现有值，内部源站并非本次新增的直接公网入口。

Skill 当前与固定版本仍为 1.3.0，内容 SHA-256 为 `ddce149dfc6ad7f713ad4a49c9ee75a57e251055d8471289d34ad115a4e59ee9`。本次只改变提供它的跨域响应，不重新生成 Skill、覆盖不可变版本或升级 API。

## 实际验证

| 编号 | 验证层 | 结果与范围 |
| --- | --- | --- |
| CHECK-01 | 初始现象 | [原始预检记录](evidence/2026-11-13/cors-edge-before.json) 保留方法/私网 400 和静态请求头限制，不发布完整服务器配置 |
| CHECK-02 | 本地 | [9 项配置检查](evidence/2026-11-13/cors-edge-local.json) 通过，覆盖公网定位、私网原样保留、已有 CSP、静态资产、重复/歧义/缺失/不完整配置拒绝、新装与 Skill 模板；Ruff、Linux 目标 mypy 通过 |
| CHECK-03 | 实际 Linux 部署 | [部署记录](evidence/2026-11-13/cors-edge-deployment.json)：当前摘要核对、原生 Nginx 语法通过、平滑 reload、14 项本机 HTTP 检查通过；五个服务 PID 与应用指针不变 |
| CHECK-04 | 公网独立请求 | [76 项公网检查](evidence/2026-11-13/cors-public-http.json) 全部通过：63 项由三来源、三方法、七路径组合的自定义头/私网预检，加 13 项真实响应、业务发现与完整/HEAD/Range 读取 |
| CHECK-05 | 业务与资产 | 普通业务 Key 查询 45 个模型为 200；缺身份为 401；内部实际请求为 404；Studio CSP 保留；Skill 完整摘要、HEAD 和 32 字节 Range 正常 |

没有提交生成、修改 Key/Workflow/任务、迁移数据库、重启 Windows 或干预用户反代。没有使用本地 Docker/WSL/VM，没有创建浏览器标签页，也没有保留本次新增的后台服务。完整工作流接入与项目验收状态继续按 [144](144-API更新进度与运行复核.md) 和既有证据独立记录，本次 CORS 通过不关闭这些待办。
