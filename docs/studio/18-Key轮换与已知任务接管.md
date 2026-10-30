> 历史融合文档：前端契约可继续参考；涉及原API的改造/发布不再执行。当前架构以文档20为准，独立部署只使用文档21。

# 18 · Key轮换与已知任务接管

本增量已发布静态136.1，179单元/171原生浏览器组及独立真实轮换验证通过；新增功能仍pending-test等待用户手工验收，完整目标HOLD。对应F-32和文档07的身份契约：连接新Key不会改写旧提交，只有原服务用新Key返回匹配的已知任务后，才建立明确的本机访问关联。

## 使用与权限

连接页填写新Key；凭证变化创建新身份，保留旧连接名称和指纹用于离线备份，移除旧连接保存的明文Key。用户进入“接管已知任务”，逐条确认查询；仅向当前固定源站发送GET，检查任务ID、模型和类型。401/403/404、网络失败、连接切换或记录变化均不建立关联。服务器权限继续决定查询、取消和下载能力，关联不赋予服务端权限。

Admin应对原业务Key使用“轮换”，这样新的凭证仍属于原逻辑Key。另建一个业务Key，即使复制相同模型和配额，也不会自动取得旧任务权限。取消还需要对应服务端scope；读取权限通过不代表取消权限通过。没有旧Key也可恢复清单，但不能用本机文件绕过原服务授权。

无任务ID的prepared/unknown记录禁止接管，不换新Key执行lookup、上传或生成。原身份、正文、幂等键、发布摘要、项目及节点保持不变。多次轮换可为同一已知任务保存多个显式关联；解除当前关联不取消任务、不删除原记录或本机文件。

## 持久化与备份

原Intent新增独立readAccess元数据，逐条绑定目标连接、已知job ID和原提交快照SHA。写入前在原任务事务中重查当前连接、原提交及job ID，且与成功GET的任务状态一起提交；配额或事务失败不留下半个关联。原任务更新继续保持原提交不可变。旧客户端不了解新身份的结果引用，因此本机日志库升级到v4，保留原v1/v2/v3各仓库，旧v3客户端必须重载，不能用静态回退降级。

当前身份只显示其原生任务及已明确关联的已知任务。新Key下载按新指纹保存本机副本；不会搬移旧Key的缓存。已有画布节点继续对应原intent，保存引用和可重建任务共同保护新副本。连接切换及晚到响应不能写入错误身份。

任务清单继续按原身份导出，readAccess权限证明不导出。接管页可另行导出各原身份清单；换浏览器后可明确导入同源旧身份清单，保留原身份和恢复保护，不自动查询/生成，逐条重新用新Key核对已知ID。备份SHA仅用于完整性，不能当作新Key授权证明；未知提交继续留在原身份，不能补发。

清单不包含画布和原件。跨浏览器恢复作品还需要单独保存的画布ZIP；同一浏览器轮换时，现有画布和原intent节点关联保留。服务端GET只能证明当前Key可访问该ID及匹配的模型/类型，不能证明导入清单中的提示词是服务器原文。原件下载仍独立检查ID归属、MIME、长度和SHA。

## 验收

| 编号 | 必须证明 |
| --- | --- |
| K-01 | 新Key连接保留原身份元数据并移除旧明文，不暗改旧提交 |
| K-02 | 未确认时零业务请求，确认后仅GET；ID/模型/类型必须匹配 |
| K-03 | 错Key、异源、未知提交和不匹配响应均无关联、零生成/上传 |
| K-04 | 连接或原记录在GET/写入期间变化、事务中止保留原记录 |
| K-05 | 两页并发关联合并、重复确认幂等、解除仅移除当前关联 |
| K-06 | 查询/取消/下载仍由新Key鉴权，画布和素材身份及引用一致 |
| K-07 | 本机v1/v2/v3→v4保留，旧v3客户端不能执行不懂新引用的删除 |
| K-08 | 原身份清单导出、同源旧清单显式恢复、证明不随备份传播 |
| K-09 | 桌面/390px、键盘、错误、青柠主题和跨连接晚到UI |
| K-10 | 最终冻结源码、隔离构建、公网发布/保留和真实权限验证分别留证 |

不更改服务端Key轮换协议，不轮换用户现有Key、不重启旧验收Key。部署使用原高位HTTP与任意源CORS；验证浏览器只创建自有上下文并在finally关闭。


K-01～K-09由[31组原生浏览器报告](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/task-access-2026-10-30T04-05-17-889Z.json)覆盖，实际日志库版本4及v1/v2/v3保留独立读取验证；部分旧套件的“v3”案例名称保留其历史场景名，不当作当前版本证明。K-10绑定[22份当前回执](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/task-access-validation-notes-1361.json)：同锁隔离106产物一致、公网两个页面6项、实际v3→v4保留/重启3项、健康/CORS和555后端运行源码同冻结Git。原112/1,486后端结果沿用，未重跑。

[实际权限五组](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/live-key-rotation-1361.json)证明旧凭证401、新凭证原任务/原件200、异逻辑Key404及撤销取消权限403。只提交一次720×1080图片，747,994字节原件完整解码/SHA一致；[具体调用与文件](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/research/studio-implementation/key-rotation-delivery-1361.json)记录端到端1,483秒（24分43秒），不代替性能签收。五把专用逻辑验收Key已停用，六份凭证均401，用户原Key未改。

原速[MP4](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/media/studio-131/studio-task-access.mp4)/[GIF](https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/media/studio-131/studio-task-access.gif)和桌面/390px截图来自生产构建、原生数据库与合成API。真实GPU图片、服务器权限及公网空凭证页面分别记录，互不冒充；所有自建客户端/浏览器/临时服务关闭。更多离线/授权/物理手机、峰值和10～20台容量仍待验收；不新增产品超时、重试、大小、并发或保留限制。
