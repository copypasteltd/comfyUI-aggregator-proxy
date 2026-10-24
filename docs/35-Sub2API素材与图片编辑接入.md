# 35 · Sub2API 素材与图片编辑接入

2026-10-23，接续 [34](./34-Sub2API生成兼容与产物访问.md)，涉及 DEV-01～03、07、11～19、23～27、29～32。本增量实现素材上传/原属查询/删除/下载，以及同步/异步图片编辑；完整服务运行和真实设备仍须独立验收。本文先冻结变更约束，再同步代码及证据。

## 上传与归属

POST /v1/assets 使用单文件 multipart；model 查询参数用于选择受信模型对应的网关，客户不能选择任意地址。只有当前主体可用的已配置模型参与路由；存在多个目标时必须明确 model。现有资产的查询/下载/删除按持久化原目标执行，不按当前模型路由遍历网关。

每张素材最大 20 MiB，静态 PNG/JPEG/WebP、最多 2500 万像素，控制面受限解码为权威验证。网关仍逐请求检查真实服务凭证、显式客户映射、当前 Key/IP、assets:upload/read 及敏感下载/删除权限。客户端头和文件名不能指定归属、对象路径或网关凭证。

素材元数据和下载回执的大小、类型、SHA-256 ETag/范围总长必须与已持久化上传规格一致，矛盾时拒绝交付；流式范围请求只核对元数据与范围，不宣称完成了整文件摘要验证。

控制面公开上传支持可选 Idempotency-Key；网关上传按既有端点契约强制要求该头，Provider 总是提供稳定键。按逻辑 Key、上传操作、token 摘要持久去重，并核对文件 SHA-256 和规格。相同键/不同文件冲突，相同文件重放返回原素材，不重置保留期；原素材到期/删除时拒绝复活。公开入口未传键的既有调用保留新建行为。对象 HTTP/存储不跨数据库锁；并发重复写出的未发布对象精确清理，提交结果不明确时先核对原对象，不能误删可能已提交的版本。

新增 252 前向迁移：素材上传意图在调用控制面前保存实例、用户/Key、原网关、模型与文件摘要/规格；收到并验证回执后登记不可变的 asset 归属。回执丢失后用同一意图、原网关和远端幂等键恢复，不新建目标。关联不含素材正文、秘密或货币额度。247～251 保持不变；只在备份核对后的随机测试库演练，不自动迁移应用开发库。

## 图片编辑与规范化

POST /v1/images/edits 及 /async 接受：multipart 的 image 或有序 image[]（不混用）、可选 mask、model/prompt/n/size/quality/response_format/user，以及 JSON 对象形式的 workflow_options 字符串。JSON 入口接受已有 source_image/mask/references 的 asset_id 外形，或 images 数组中的 image_url；两类输入不能混用。URL 限受限 Base64 data URL 或已配置 public_origin 的原属 /v1/assets/{id}/content 地址；禁止任意外链、重定向、Files ID 和客户自报对象路径。

单图归一化 image_to_image，mask 只用于单图；两至四图归一化 references_to_image，保持图序。workflow_options.image_roles 必须明确每个 index 的 role，或由以后单独发布的默认槽位契约提供；当前不猜测角色。workflow_options.strength 仅用于单图且继续由实际模型发布清单校验；多参考不接受强度或 mask。原生请求字段和五类能力不缩减。

所有可预检字段、模型/分组、同步交付权限在上传和资金预留前检查。内联图片使用文件摘要派生稳定上传键，已上传 ID 使用原归属；最终归一化到同一 JobRequest、规范 operation、持久预留及恢复协调器。规范请求相同即可跨兼容/原生入口恢复同一任务；改变文件、顺序、角色或强度须产生冲突。同步仍默认 Base64、等待 60 秒并保留超时后的原任务；异步为 202。

## 宿主与资源预算

图片编辑的 JSON/multipart 总正文最多 128 MiB，最多四张输入与一张 mask，普通字段数量/字节、JSON 深度和节点均有上限。JSON 在解析前按固定缓冲校验 UTF-8，跨块最多保留一个未完整码点，不把非法字节替换后接收。入站临时文件只写受信 upload_directory；省略时使用托管配置旁的 render-control-spool。只创建随机文件名，在 finally 中关闭和删除本次文件，不扫描或删除用户原文件。

宿主为选择模型而读取共享编辑路径时使用有界文件暂存，回退原 Handler 时重放原字节和 Content-Type；不能把非本 Provider 的请求重新编码。最多两个并发编辑/上传，请求体读取最长 60 秒；完整异步编辑/上传预算 180 秒、同步编辑 240 秒，包含宿主选模的入站暂存阶段，控制面上传单次 90 秒。原上游模型的回退不附加 Provider 上下文截止时间。普通生成/元数据/媒体仍沿用文档 34 的预算。业务 Key 认证失败、未知字段、压缩正文、超限、失败及取消均须清理本次临时文件，不留下后台上传 goroutine。

## 验证与尚待完成

使用真实上游认证/仓储与原生 PG、HTTPS 协议服务验证：上传回执丢失、同键并发、异文件冲突、当前权限、跨 Key/实例、原网关保持、数据库中途失败、不可变约束、编辑跨入口幂等、图序/角色/强度/掩码、平台 URL 归属、外链拒绝、multipart 原文回退、临时文件和容量释放。控制面另以真实 PG/本地开发对象存储验证幂等和过期/删除不复活；不冒充真实 S3。

只有与当前源码匹配的组件报告、实际 cmd/server 编译、应用回归和清理证据通过，才更新组件标志。完整 Sub2API/控制面跨进程运行、模型发现/列表/显式共享、实际副作用消费者、套餐/价格倍率、真实 ComfyUI/五类 Workflow/S3 和 10/20 台设备仍在原首版范围。

OpenAI 官方当前文档区分 multipart 与 JSON 的图片编辑输入，本项目按上述受控子集接入，不承诺所有模型参数。依据：[图片编辑参考](https://developers.openai.com/api/reference/resources/images/methods/edit)；核对日期 2026-10-23。

## 当前实现与实测

[Go 客户端报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-discovery.json) **31 项顶层测试、102 个子用例通过**，4.92 秒。[原生组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-discovery.json) **106 项顶层测试、209 个子用例通过**，另 1 项子进程 helper 在主进程跳过；总耗时 31.56 秒，含原生测试程序编译 8.22 秒。相较生成兼容版新增 16 项顶层和 27 个子用例，不与历史重复累计。完整上游迁移链为 303 项、108 张测试表，并通过重放；备份可读已核对、随机测试库已删除，247～251 摘要未变。

[实际 cmd/server 编译](./evidence/2026-10-23/sub2api-provider-native-build-before-discovery.json) **通过，14.09 秒**，不启动完整上游服务。[应用检查记录](./evidence/2026-10-23/sub2api-assets-application-checks.json) 登记 Python **241 项通过，172.79 秒**，锁文件、Ruff 和 mypy（79 项源文件）通过；OpenAPI、TypeScript 客户端生成、vue-tsc 与 Vite 构建通过，Vite 为 13.05 秒。104 个 OpenAPI 路径、134 个方法路径注册，127 项设计端点全部匹配。保留现有 TestClient 弃用和 Vite 大块警告；本增量未运行浏览器测试。

[源码与运行收尾](./evidence/2026-10-23/sub2api-assets-closeout.json) 核对 46 项维护/生成文件、两处原版/补丁、验证入口与测试/服务器二进制摘要匹配；另核对 184 项应用源码和生成产物。开发库保持原版本及 40 张业务表，9 项注册开发进程身份一致；临时测试/恢复库、测试目录、上传暂存文件和编译/测试进程均为 0。只重启已登记的 HTTPS API 以加载控制面上传变更，Runner/GC 保留；项目证书核验 live/ready/Admin 为 200，匿名策略/素材请求为 401，运行中 OpenAPI 与导出一致。

保留 [首轮 Go 失败](./evidence/2026-10-23/sub2api-assets-first-run.json) 与 [首轮 Python 类型检查失败](./evidence/2026-10-23/sub2api-assets-python-first-run.json)；具体原因与修复见文档 17。组件验证标志已按当前报告更新；provider_implemented、runtime_integration_verified 仍为 false。未使用虚拟化或打开浏览器，主动保留原生 PG、HTTPS API、Runner 和对象 GC 供继续开发。
