# 81 · Admin 网关目录映射与发布核对

日期：2026-10-25（Asia/Taipei）。关联待办 01～04、47、50、72、76；沿用 [79](./79-网关模型目录持久发布协议.md) 与 [80](./80-Provider目录消费与版本快照.md)，不新增另一套运行配置或客户账本。本文先记录界面约束，验证后追加实际出口。

网关接入页增加“模型目录”管理。models:read 可读取期望/确认状态、差异和分页历史；models:publish 才能编辑发布、重试或回滚。首次填写逻辑 instance，发布后固定；Sub2API 用量账户与分组 ID 始终以十进制字符串处理，不转换为 JavaScript Number。价格保持八位定点字符串。模型从已发布目录按项目/名称分页选择，携带选中时的 model revision；不同项目的同名模型只有规格一致才能合并，服务端仍进行权威校验。

模型页同时提供目录入口，供没有 keys:manage 的模型维护者和观察者使用。新增 GET /admin/api/v1/gateway-catalog-options，要求 models:read，按名称字面搜索、每页 50、page=1～1000、q 最多 128 字；只返回 id/name/enabled，不返回地址、网段、凭证、issuer 或客户绑定。网关服务 CRUD 的 keys:manage 权限保持独立。目录历史仍每页 20；分页和详情均不得一次拉取全部冻结正文。

每次编辑捕获目录 revision；刷新状态不悄悄升级编辑基线或改写未提交内容。确认发布说明新版本仍待 Provider 回执；空目录需显式确认下架。旧有效配置、旧任务和旧价格保留。差异展示新增、修改、移除及未变的别名，并允许查看原始冻结 JSON。历史重试/回滚总是创建新版本，不能修改原版本。

写操作保存原正文、CAS 和幂等键；超时、响应丢失或隐藏停止等待后保留未知状态，只允许使用原请求核对。明确拒绝可结束首次请求；未知请求后再遇到拒绝仍需人工核对。刷新、重开、换版本不能自动另发同义操作。读请求十秒限时；只在可见且会话有效时轮询，关闭/隐藏/离页/退出清理本次读取、确认框和等待。恢复可见先核对会话；观察者没有写操作入口。

验证使用隔离原生 PostgreSQL、真实 HTTPS Admin/API、合成目录与明确标记的 Provider 回执；浏览器 finally 关闭本次 page/context/browser，不操作用户窗口。桌面、小屏、丢响应原请求、并发 CAS、拒绝/漂移、空目录、权限和清理分别留证。截图/录屏/GIF 是管理操作演示，不是 GPU 推理或真实设备验收。

## 使用顺序

在网关接入页登记服务身份及客户绑定；在模型页完成 Workflow/规格/渠道的发布门禁。打开任一“模型目录”入口，首次填写与 Provider 启动文件相同的 instance，再选模型、设置既有账户/分组和价格。选择模型时冻结其当前 revision；模型后来发生变化需显式重新选择，不自动接受新绑定。检查差异后确认发布，待 Provider 确认卡显示目标版本再认定本次同步完成。

拒绝或漂移时核对固定错误、当前模型状态和 Sub2API 账户/分组；修复后选择历史版本创建重试，或将某个历史配置回滚为新版本。回滚仍需符合当前发布条件，不能使已失效的依赖重新获得批准。清空全部映射并确认发布，可下架当前网关的新请求；旧任务和内容仍按原属关系与当前权限核对。页面历史每页 20 条；详情仅按需读取一个冻结配置。

## 验证与演示

[后端 19 项](./evidence/2026-10-25/gateway-catalog-ui-backend.xml) 包含名称字面搜索、50 条分页、禁用网关可见、响应仅三字段、预算、观察者/注销和写权限负测。前端 40 项单元通过，其中目录新增 7 项保护十进制 ID、价格、精确 JSON、差异及输入不可变；Vue 类型检查和构建通过。

[浏览器与隔离资源记录](./evidence/2026-10-25/gateway-catalog-browser.json) 通过 16 组场景：实际六类映射发布、64 位 ID/定点价格、期望/确认分离、冻结正文、提交成功但响应丢失时原正文/CAS/幂等键一致、未知时阻止关闭、隐藏/恢复先查会话、拒绝保留旧确认、回滚新版本、并发版本不改写草稿、空目录下架、375px/横屏/放大字体、固定 instance、56 版本分页与模型页观察者只读。四个 context 和自有浏览器、临时 API、数据库、凭证均清理；无真实设备或生产验收提升。

新增 [映射编辑](./media/gateway-catalog/catalog-editor.png)、[确认状态](./media/gateway-catalog/catalog-confirmed.png)、[历史](./media/gateway-catalog/catalog-history.png)、[移动端](./media/gateway-catalog/catalog-mobile.png)、[GIF](./media/gateway-catalog/gateway-catalog.gif)、[MP4](./media/gateway-catalog/gateway-catalog.mp4) 及 [安全请求/响应](./media/gateway-catalog/api-responses.json)。录制只使用合成公开配置；用量账户/分组最大整数用于精度验证，并非可用于真实生成的账号。回执明确标为协议夹具，实际 Provider 消费另见 80 的原生跨进程证据。

保留 [初轮回滚读取竞争](./evidence/2026-10-25/gateway-catalog-browser-initial-failure.json)、[移动布局失败](./evidence/2026-10-25/gateway-catalog-browser-mobile-failure.json) 和 [布局诊断](./evidence/2026-10-25/gateway-catalog-browser-layout-failure.json)。脚本改为等待实际写响应，网关页头在小屏换行；协议种子补显式 nullable 额度值以满足 Admin 序列化，没有放宽运行契约。最终完整浏览器检查通过。

## 原生候选与开发预览

候选 rc-20261025.81.1 的 [构建](./evidence/2026-10-25/provider-catalog-release-build.json) 与 [清单](./evidence/2026-10-25/provider-catalog-release-manifest.json) 记录 456 源输入、154 载荷和 57 wheels，输入与当前源码一致；SHA-256 为 ce4aa073da4d41bd00a0222ec2ca4e9d3cf73ec5d92886eeaad86a6eda38871d。[隔离离线安装 24 项](./evidence/2026-10-25/provider-catalog-release-installation.json) 与 [安装后 8 组浏览器检查](./evidence/2026-10-25/provider-catalog-installed-browser.json) 通过，临时安装目录、数据库、自有进程/浏览器及凭证清理完成。这是同机隔离安装，不能替代新宿主、真实设备或生产门禁；validated_releases 仍为空，所有生产验收标志为 false。

项目 API/Runner/对象 GC 按登记的 PID、可执行文件和创建时间核对后重启，连同本地 PostgreSQL 保留作开发预览；[证书验证的 HTTPS 检查](./evidence/2026-10-25/provider-catalog-development-readiness.json) 通过，新 options 无认证返回 401。预览地址 https://127.0.0.1:8443/admin/；这些常驻服务不是遗留的测试浏览器或临时服务。
