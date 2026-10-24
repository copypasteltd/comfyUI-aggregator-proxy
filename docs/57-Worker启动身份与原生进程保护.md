# 57 · Worker 启动身份与原生进程保护

日期：2026-10-24。状态：本地实现与分层验证通过；真实设备与生产出口未验收。对应 WP-00/05/13/14，DEV-02/03/05/09/10/14/19/25/26/29/30/31。此增量不替代六类真实 Workflow、私有对象存储或 10/20 台设备验收。

## 1. 问题与要求

已有 journal 文件锁阻止同一目录的多个 writer，但复制配置、使用不同 state_root 仍可同时启动同一节点。每台 Windows 设备上的同一控制面源与 node_id 必须由单个 Worker 进程持有；轮换 token 不产生新身份。使用 Windows Global 命名 mutex，启动即非阻塞取得；获取失败、权限不足或平台不支持时拒绝启动，不退回仅进程内锁。名称只含规范化源与节点 ID 的摘要，不含 token。仍保留 journal 文件锁，二者分别保护节点与持久目录。

节点锁不等于云端 GPU 空闲证明。进程崩溃后操作系统允许下一进程接管，但下一进程仍必须依据 journal 与控制面核对旧执行，不能重提未知任务或释放未知 GPU。不同设备间复制凭证不能由本机 mutex 防止，仍受服务端 boot/fencing 约束；设备凭证不得共享。

退出时先停止后台协程，并等待已经开始的文件复制、哈希、探测或固定管理指令线程真正结束，再关闭 HTTP/SQLite 和释放两类锁。取消协程并不代表操作系统线程已经结束；退出期间不接受新的本地线程操作。若进程被强制结束，保留原有 journal/未知提交，不推断生成已经停止。

## 2. 持久身份与兼容

新 journal 在 SQLite 中记录规范化 control_origin 与 node_id，已有绑定不允许被其他节点或控制面覆盖。仅 HTTPS 默认端口、主机大小写、IDNA 和 IP 文本规范化，不合并不同域名别名。token 更换不更改绑定。所有 attempt、事件、回传包、保留期限、claim 和管理指令原样保留。

没有身份且没有运行事实的旧 journal 可自动绑定。含 attempt、claim 或管理指令的旧 journal 必须使用明确的 `--adopt-legacy-journal`；该选项仅允许首次绑定，不能覆盖现有身份。使用前操作人员需核对原节点与控制面、排空及停止旧进程。接管先用 SQLite backup 生成本目录专用备份并执行完整性检查，成功后才记录身份；失败保留原事实并拒绝运行。备份有大小/时间预算，不覆盖旧备份。不将该快照称为云对象/密钥或生产灾难恢复验收。

## 3. 启动和预检

凭证为 UTF-8（接受 BOM）的 JSON，恰好包含 `node_id` 和 `token`，上限 4 KiB；拒绝重复键、未知字段、错误身份前缀、错误 ID/secret 语法和非普通文件。读取错误只输出固定错误码，不打印文件内容、路径、原异常或 Pydantic 输入。配置 JSON 上限 1 MiB，同样拒绝重复键及非有限数。

`--validate-config` 检查配置、凭证结构及 TLS 信任配置，但不创建 state/journal、取得运行锁、注册节点、访问 ComfyUI 或修改云端。它不证明凭证已获服务端接受、GPU 真实存在或 Workflow 已批准。真实启动先完成无写入校验，再取得节点锁、journal 锁及身份绑定；构造失败也必须关闭已创建的连接和锁。正常退出与异常退出均清理 HTTP/SQLite/本机锁，持久 journal 保留。

源码 Worker 与候选 `release-entrypoint.py worker` 共享校验和接管参数；候选依旧要求显式 state-dir、外部凭证与配置，不改变 PostgreSQL schema。不会注册系统服务、启动 ComfyUI、修改防火墙、安装模型或运行远程 shell。

新增可选 HTTP 头 `X-Worker-Node-Id`，新 Worker 在内部请求中固定发送凭证 JSON 的节点 ID。服务端先验证 Bearer，再要求该头与真实认证节点一致；错配在注册/boot、GPU 清单或其他管理事务之前拒绝，不能用此头选择另一节点。缺省头仍接受旧 Worker，保持 0.4.0 协议兼容；该兼容不赋予旧 Worker 新的本机锁保护。OpenAPI/客户端类型与 HTTP 正反例同步。新 Worker 还要求注册回执的 node_id 和 boot_id 与本次身份一致，缺失/错配立即停止，不进入心跳、领取或管理循环。部署顺序为先升级控制面，再停止旧 Worker 并升级；旧控制面忽略该头时，回执检查只能阻止后续执行，不能保证其注册事务没有先发生。

## 操作步骤

首次登记使用文档 56 的凭证 JSON，并将所有配置路径填写为绝对路径。先运行 `python -m windows_worker.main --config <worker.json绝对路径> --validate-config`。正式启动去掉预检参数；已安装候选则使用文档 49 的独立入口，额外提供对应 `--state-dir`。

升级前停止旧 Worker 并核对原节点/源、journal 与在途执行。旧版本不认识新增节点 mutex，不能与新版本同时运行。旧 journal 若提示 `journal_identity_unbound`，在完成核对后使用同一配置加 `--adopt-legacy-journal`；快照写入 state_root 下的 identity-backups，先校验备份才写绑定，原有未知执行继续恢复核对。后续正常启动不再需要此参数；不能靠修改/删除身份表或换 state_root 绕过冲突。已有身份不匹配应恢复正确配置，不自动重绑。

Worker journal 的新增元数据为本地 SQLite 的加法变更，与 PostgreSQL 迁移独立。回退到不认识身份保护的旧 Worker 不在此轮已验证范围，必须先排空并按完整升级/回退出口另行演练。备份及 journal 含任务运行信息，应限制运行账号读取；不上传到公开文档或发给客户。

## 4. 必须取得的证据

- Windows 原生独立进程：同节点不同目录拒绝；规范化源/轮换 token 仍互斥；不同节点独立；同目录旧锁保护；争抢只有一个成功；强制结束本次进程后可重新接管且未知提交原样保留。
- 绑定与升级：其他节点/源拒绝；旧事实默认拒绝、显式接管有可恢复快照；损坏/超预算备份拒绝；已绑定身份不被接管参数改写。
- 预检和失败：重复/超限/错误凭证、非法配置、坏 CA 与 journal 初始化失败均脱敏；失败后另一合法启动能取得全部资源；预检不创建 state 或发 HTTP。
- 配对与回执：有效 token 配错误节点头不能修改实际节点 boot/清单/审计；匹配及旧客户端缺省头兼容；错误语法拒绝；注册回执缺少或错配 node_id/boot_id 时不再发送其他内部请求，全部本机资源释放。
- 现有 Worker 提交不确定性、取消、音频产物、保留及完整 Python 回归；实际 Sub2API/控制面/Windows Worker 六类协议回归；精确候选隔离安装与源码摘要核对。

命名空间与 mutex 语义以微软一手资料为依据：[Kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)、[CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)、[Using Mutex Objects](https://learn.microsoft.com/en-us/windows/win32/sync/using-mutex-objects)。Global 只覆盖本机；不把同会话测试写成已实测跨服务会话或跨账号 ACL。


## 5. 本地验证与交付记录

候选 `rc-20261024.57.2`：280662860 字节，SHA-256 `73b0934b49e39c2d57f71b3caa116b460c66ef281a7f9b7a22029bc09221a2ee`；139 个 payload 文件、57 个 wheel、22 份运行契约、346 个构建源输入均与最终源码核对。源码与已安装 Worker 共用启动校验、互斥、接管参数及退出行为。对外 API、Admin 页面与打包资源未变；控制面唯一行为变化为 Worker 鉴权依赖中的预期节点 ID 检查，14 个内部操作新增可选头，组件与其他 OpenAPI 内容逐项一致。

| 验证层 | 本轮结果 | 范围与边界 |
| --- | --- | --- |
| 完整 Python / PostgreSQL | 1,221 项通过，245.19 秒；定向 193 项通过 | 新增 44 个用例：40 个启动/原生进程/线程关闭场景及 4 个真实 HTTP 身份头场景；身份错配拒绝且 boot/GPU/审计不变，缺省头旧客户端兼容 |
| 原生进程与备份 | 六进程争抢单一成功；崩溃接管及独立备份恢复通过 | 真实 Win32 Global mutex、不同 state_root、轮换 token、不同节点及同目录文件锁；旧未知 POST、管理指令、claim 保留并拒绝盲重提；跨服务会话/账号 ACL 未实测 |
| 静态与前端 | Ruff、124 文件 mypy、75 包锁、OpenAPI/类型生成、vue-tsc/Vite 通过 | Vite 大块提示和既有 TestClient 弃用提示保留；未因本轮启动逻辑扩展图形页面 |
| 原生跨进程 | 18 组 / 11 个任务通过 | 实际 Sub2API、控制面、Worker、Runner；六类任务含 500 毫秒音频，11 个确认包核对一小时保留策略；ComfyUI/Redis/对象存储为测试替身，六个 test_only 绑定确认不作真实批准 |
| 精确候选离线安装 | 15 项通过，4 组浏览器 / 2 个上下文关闭 | 离线非 editable、22 契约、迁移前备份、包内四格式音频解码；预检不改旧 journal，默认拒绝旧事实，显式备份接管后合成凭证被真实安装 API 拒绝 |
| 摘要复用的历史证据 | Admin 21 组；Go 45 顶层/192 子项及账本/Provider 491 项；PG 恢复 42 表/753 行 | 分别复用第 56、55、56 增量；当前 UI/资源、非 Worker OpenAPI、维护/生成 Go 源码、候选二进制及数据模型/迁移未变；均未记作本轮新运行 |

两库仍为 `d62f7b08a9e1` / 42 张业务表，fsync、synchronous_commit、full_page_writes 保持开启；本轮没有 PostgreSQL schema 变化。SQLite 恢复为本轮新运行，不能替代对象/主密钥联合恢复或生产回退。旧候选 57.1 已完成当时的 1,213 项回归和 15 项安装，之后为补齐凭证配对/回执检查而被 57.2 替代；旧报告与失败原件保留。完整测试中的时钟/并发问题按失败记录说明，不修改生产限额、锁或持久性要求来通过测试。

保留 PostgreSQL 与 9 个已登记 API/Runner/GC 进程供本地预览；测试进程、随机数据库、私密配置与临时安装/恢复目录已清理，当前测试自建浏览器在 finally 关闭。未调度 Docker、WSL、虚拟化或子代理。真实 Windows/ComfyUI、六类 Workflow/模型与质量吞吐、S3/真实 Redis、公网、Linux/系统服务、生产升级回退、对象/密钥恢复和 10/20 台容量仍未验收。14 个工作包保持进行中，WP-12 等待用户后续真实资源，完整目标未完成。

证据：[完整回归](./evidence/2026-10-24/worker-startup-full-python-tests.xml)、[定向回归](./evidence/2026-10-24/worker-startup-targeted-tests.xml)、[前端](./evidence/2026-10-24/worker-startup-frontend.json)、[身份头契约比对](./evidence/2026-10-24/worker-startup-worker-header-contract.json)、[原生协议](./evidence/2026-10-24/worker-startup-native-protocol.json)、[journal 恢复](./evidence/2026-10-24/worker-startup-journal-restore.json)、[候选构建](./evidence/2026-10-24/worker-startup-release-build.json)、[隔离安装](./evidence/2026-10-24/worker-startup-release-installation.json)、[安装浏览器](./evidence/2026-10-24/worker-startup-installed-browser.json)、[历史浏览器复用](./evidence/2026-10-24/worker-startup-browser-reuse.json)、[Go 复用](./evidence/2026-10-24/worker-startup-go-reuse.json)、[历史 PG 恢复复用](./evidence/2026-10-24/worker-startup-pg-restore-reuse.json)、[失败与替代版本](./evidence/2026-10-24/worker-startup-failure-notes.json)、[进程](./evidence/2026-10-24/worker-startup-process-check.json)、[最终核对](./evidence/2026-10-24/worker-startup-closeout.json)。
