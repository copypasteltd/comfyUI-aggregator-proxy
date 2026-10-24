# 33 · Sub2API 服务托管与生命周期

当前目录应用机制见 [80](./80-Provider目录消费与版本快照.md)：独立迁移至 257 后支持 Admin 发布和自动消费。models 可为空数组用于首次接入；尚未 managed 时保留启动配置，首次确认后该网关以持久目录为准。后续动态账户/分组 ID 是十进制字符串；下文启动文件中的整数示例格式保留兼容。本文各旧迁移及统计描述属于当时增量，不作为当前完整安装基线。

2026-10-23。接续 [31](./31-Sub2API恢复租约与协调器.md) 和 [32](./32-Sub2API公开任务接入.md)，涉及 DEV-02、03、07、11～19、23～27、29～32。本文记录真实上游服务器组合根、后台恢复及结算生命周期；后续生成兼容与原属产物访问见 [34](./34-Sub2API生成兼容与产物访问.md)。完整图片编辑、素材/发现、共享列表与副作用消费者仍按原范围开发。

## 启用与配置

SUB2API_RENDER_CONTROL_CONFIG 指向服务账号可读的绝对 JSON 文件；未设置则保持上游服务器原行为，不创建客户端或后台循环。配置显式声明版本、实例、加密密钥文件、网关 HTTPS 地址与服务凭证文件、模型能力/分组/固定单价/默认参数、可信代理和轮询预算。配置、凭证、CA 及密钥均有读取大小上限，拒绝未知或重复字段、不合法数值、非普通文件、明文远程连接和身份不匹配；不记录文件内容或原始错误。密钥必须保留旧版本以读取在途意图。

启用后要求上游 RunMode=standard（进程运行模式）、既有数据库与真实 APIKeyAuthMiddleware。分组余额/套餐行为按后续 [40](./40-Sub2API倍率与套餐计费.md)。启动只读核对持久意图/恢复/结算表字段可用，不在安装步骤运行 DDL。迁移仍须按备份及停旧写入流程单独管理；不把上游自身的既有初始化机制描述成本项目新增迁移工具。配置错误拒绝启用并清理本轮客户端，不能静默回落到普通供应商路由。

配置格式如下，价格、模型、分组和网关标识仅为示例，部署前必须替换为实际受信配置和已批准能力。secret file 路径可相对该配置文件，顶层环境变量必须为绝对路径；加密文件为 Base64 编码的 32 字节密钥，服务凭证文件为既有 gateway 服务凭证。不要把这些文件或真实配置提交到仓库。

```json
{
  "version": 1,
  "instance": "sub2api-production",
  "public_origin": "https://api.example.com",
  "sync_wait_milliseconds": 60000,
  "active_key": "provider-key-v1",
  "encryption_key_files": {"provider-key-v1": "secrets/provider-key-v1.txt"},
  "gateways": [{
    "id": "gateway_00000000000000000000000000000000",
    "origin": "https://control.internal.example",
    "credential_file": "secrets/gateway.key",
    "ca_file": "control-ca.pem"
  }],
  "models": [{
    "model": "example-image-v1",
    "task_type": "text_to_image",
    "gateway_id": "gateway_00000000000000000000000000000000",
    "price_version": "example-prices-v1",
    "unit_price": "0.01000000",
    "usage_account_id": 1,
    "group_ids": [1],
    "defaults": {"size": "1024x1024", "quality": "standard", "n": 1}
  }],
  "trusted_proxies": [],
  "runtime": {
    "workers": 2,
    "poll_milliseconds": 1000,
    "settlement_milliseconds": 2000,
    "call_milliseconds": 15000,
    "lease_milliseconds": 90000
  }
}
```

顶层文件最多 256 KiB，加密密钥文件 256 字节、服务凭证 512 字节、单个 CA 文件 1 MiB；密钥版本最多 4 个，网关最多 8 个、模型最多 256 个。恢复及结算轮询间隔 10～60,000 毫秒，调用 1～60,000 毫秒；租约必须覆盖两次调用加 20 秒余量且不超过 300,000 毫秒。可省略 runtime 的值以采用组件默认值；不能用负值或极大数触发默认或时长溢出。

public_origin 可省略；显式 URL 交付须配置 HTTPS 纯源，无账号/查询/片段/路径，不能接受客户 Host 或转发头作为来源。sync_wait_milliseconds 省略或 0 采用默认 60,000，其余允许 1～60,000；同步生成总预算 90 秒、元数据 30 秒、媒体 300 秒，宿主写预算另加 5 秒。同步等待与媒体各最多 8 个；详情及 Base64/Range 限额见文档 34。素材/编辑另按文档 35 配置 upload_directory、128 MiB 暂存和 180/240 秒预算，包含共享路由选模的入站读取时间。启用前独立迁移至含 252 素材关联表的版本，安装过程仍只读探测。

## 宿主与生命周期

固定上游的生成副本在 cmd/server/wire_gen.go 增加经摘要核对的组合根调用，传入同一数据库、真实鉴权与 http.Server；清理先停止本 Provider，再运行上游清理。生成文件的补丁须在固定版本更新或重新生成 Wire 后重新审查，不能宣称会自动保留。原归档、原版目录和 go.mod/go.sum 不变。

公开任务命名空间由独立处理器处理，请求方法、路径及主体不重写；其余请求交回原 Handler，不能再次进入普通图片计费链。请求级读写时限只作用于 Provider 请求，不给上游长时间流式响应增加全局写时限。入口采用独立可信代理配置、服务生成的请求 ID、普通 JSON 2 MiB 上限与当前授权检查；素材/编辑的有界暂存例外见文档 35。清理开始后拒绝新的 Provider 请求。

恢复 Worker 总数 1～16，目标网关 1～8；轮询顺序公平轮转，每次即时领取一个持久工作，进程内没有待执行任务队列。每个目标有独立、串行且有界的结算轮询；单轮最多 100 个事件。每次步骤都有调用/数据库/租约时限，空闲或失败后等待配置间隔，不自旋；一个网关错误不停止其他目标。固定计数器只保留步骤/错误/结算数量，不含主体、提示词、凭证或原始错误。

Start 只允许一次；Stop 可并发重放，并取消后台上下文、等待所有本轮循环退出。上下文取消不退款、不删除持久工作，也不伪造完成；已发出的请求继续按原意图与远端持久协议恢复。租约未过期时新进程不得抢占。清理有界等待后关闭本轮客户端的空闲连接，数据库生命周期由上游管理。

## 验证边界

验证须涵盖启用/禁用、错误配置不修改服务器、真实 HTTP 鉴权/原 Handler 保留、从持久预留自动准入及确认、自动结算/ack 重放、并发 Start/Stop、停止期间在途调用取消、独立网关调度与错误退避。使用真实上游 schema/仓储和原生 PG、TLS 协议测试服务；另对补丁后的实际 cmd/server 编译，不能仅靠库测试证明组合根可构建。

实际测试与源码摘要登记文档 17。未完成完整上游服务与控制面/真实 Worker 的跨进程联调前，provider_implemented 和 runtime_integration_verified 保持 false；测试服务不是实际 ComfyUI、对象存储或完整上游运行证据。

应用工作目录下，scripts/check.ps1 -Suite Sub2APILedger 运行组件及宿主协议测试；scripts/check.ps1 -Suite Sub2APIProvider 生成经核对的副本并编译实际 cmd/server，二进制位于 app/.local/bin/sub2api-provider.exe。构建入口不启动该服务或迁移数据库，也不执行上游测试；未设置配置环境变量时，此二进制保持原上游 Provider 禁用状态。启用前仍须准备原生上游依赖、配置、独立备份迁移及完整联调。

## 服务托管增量实测（生成兼容前）

配置、安装、后台恢复/结算和生命周期已实现。[当轮组件测试](./evidence/2026-10-23/sub2api-ledger-tests-before-compatibility.json) 77 项顶层测试、152 个子用例通过，另 1 项子进程 helper 在主进程跳过；耗时 113.09 秒，含重新编译 53.91 秒。真实上游 schema/认证/仓储、原生 PG 与 HTTPS 协议测试服务共同验证上文边界；301 个迁移、105 张测试表重放通过，备份核对、临时库和测试目录清理均通过。

[补丁后的实际上游服务器编译](./evidence/2026-10-23/sub2api-provider-native-build-before-compatibility.json) 通过，18.32 秒；未启动完整上游服务。[源码与运行收尾](./evidence/2026-10-23/sub2api-hosting-closeout-before-compatibility.json) 核对 33 项文件、原版/补丁/入口/测试及服务器二进制摘要一致，247～250 未变，应用库与现有 HTTPS API/Admin 正常。首轮夹具 panic 与并发锁探针修正经过完整复测，失败记录保留于文档 17。

上述报告保留为生成兼容前的历史快照。后续生成兼容的 [当轮组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-assets.json) 为 90 项顶层测试、182 个子用例通过，302 个迁移、106 张表重放通过；[当轮服务器重新编译](./evidence/2026-10-23/sub2api-provider-native-build-before-assets.json) 通过，22.47 秒。文生图同步/异步、视频 JSON 和原属内容访问已加入宿主；该轮 39 项源码/生成文件收尾与限制见文档 34。素材/编辑及最新源码证据转见 [35](./35-Sub2API素材与图片编辑接入.md)。完整上游与控制面跨进程运行、真实媒体/设备、模型发现、共享列表、实际副作用消费者和套餐/价格倍率仍待完成，完整 Provider 与运行联调标志保持 false。

后续用量/缓存消费按 [39](./39-Sub2API用量与缓存持久消费.md) 接入：启用时每模型必填 usage_account_id，指向既有未删除、openai、schedulable=false 的统计账号；示例的 1 必须替换。组合根同时注入实际上游 BillingCache，启用前独立迁移至 254，启动只读探测。30 秒后的第二遍失效由持久 effect 调度；后台停止与其余循环一起等待，不释放资金或清空租约。该处理不证明普通供应商路径的全局强一致缓存。
