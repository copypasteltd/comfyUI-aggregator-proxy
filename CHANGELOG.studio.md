# 独立画布更新记录 / Independent canvas release notes

## canvas-20261030.143.1 · 已独立部署 / pending-test

- [修复] 本机原件删除与保存统一识别历史references，覆盖画布、素材及可恢复回收站；普通提示词不猜作文件。
- [修复] 精确旧引用索引由完整正文补齐，原生事务递增修订；未知索引拒绝覆盖，正文、原件和任务保留。
- [验证] 保存/删除双向竞争、升级中止、实际前版函数与双页一次升级通过；有引用删除拒绝，无引用明确删除可用。
- [交付] 188单元/类型/构建、11双源、14ZIP、15本机+15公网保护、107当前/425旧资源与14路由；文档28、双语README、截图及完整解码MP4/GIF。原API和所有服务/配置不变，无实际Key/GPU任务，完整签收HOLD。

143.1 protects historical reference-only originals during local deletion and document saves, including saved assets and recoverable deleted projects. Only an exact recognized old index is rebuilt from unchanged document bytes, with an atomic revision increment. Save/delete races, transaction abort, actual previous-release functions and two-page initialization are verified.188 units, typecheck/build,11 dual-origin groups,14 ZIP groups and15 local plus15 public reference-protection groups passed. All107 current/425 retained resources and14 routes were verified. Screenshots and a fully decoded MP4/GIF are included. The original API, services and configuration remain unchanged; no actual Key/GPU task was created. Full acceptance remains HOLD.

## canvas-20261030.142.1 · 已独立部署 / pending-test

- [修复] ZIP统一收集节点原件及references中的明确本机键；仅历史引用的原件不再遗漏，清单去重但引用顺序/重复项保留。
- [修复] 导入同步重映射本机参考键，提示词原文不变；参考未声明/缺失/摘要不符时整包拒绝，原件不可读则拒绝导出残缺ZIP。
- [验证] 既有双页版本冲突不覆盖先保存内容、不盲目重试；未保存分支实际导出后原件/参考/SHA完整。DBv4/ZIPv3不变，不改普通媒体清理。
- [验证] 184单元/类型/构建、11双源、14本机+14公网原生组及12类坏档通过；107当前/340旧资源、14路由和原API只读边界逐项核对。
- [交付] 独立静态142.1、文档27、双语README、截图及两MP4两GIF完整解码；无Key/GPU任务，发布仅只读健康/CORS。原API和全部服务/配置不变，无重启，完整签收HOLD。

142.1 includes originals referenced only by explicit local reference lists, validates their completeness, and remaps their keys while preserving reference order, duplicates and prompt literals. Missing originals prevent a partial ZIP download. Actual stale-page import cannot overwrite the winning document; its unsaved project remains exportable with complete originals and references.184 units/typecheck/build,11 dual-origin groups and14 local+14 public native groups passed, including12 malformed archives. All107 current/340 retained files and14 routes were checked. Two MP4s and two GIFs fully decode. No business Key or GPU job was created; original API/services/config remain unchanged. Full acceptance remains HOLD.

## canvas-20261030.141.1 · 已独立部署 / pending-test

- [修复] 作品ZIP先整包预检并写完全部新原件，再一次加入完整项目列表；第二原件配额失败不留下部分项目。
- [修复] 文档事务失败保留旧已提交文档并显示整批未保存；明确重试后完整保存，不重新导入/生成；暂存孤儿文件保留待安全回收。
- [修复] 清除旧blob地址后，从新本机键恢复顶层图片和仅有嵌套变体的主图，显示和刷新通过。
- [修复] 拒绝畸形元数据、重复标识/文件路径与原型字段，核对原件长度/SHA；导出过滤Cookie/鉴权头/会话及URL片段凭证，不改提示词原文。
- [验证] 182单元/类型/冻结构建、11双源、10本机与10公网原生组通过；107当前/255旧资源、14路由和原API只读边界逐项核对。先前失败回执保留。
- [交付] 只发布独立画布静态文件，不改API/Worker/调度/数据库/Admin；全部服务与配置不变。双语说明、文档26、截图及完整解码MP4/GIF同步。未创建业务Key或GPU任务；发布仅读健康/CORS，390px非真机，完整签收HOLD。

141.1 validates the entire archive and stages all originals under new local keys before adding the whole project batch. File-write failure adds no project; document abort keeps the whole unsaved batch visible for explicit retry. Imported top-level and nested-primary images now hydrate from local originals after refresh. Malformed archives are rejected and structured cookies, authorization headers, session tokens and URL-fragment credentials are removed from exports without editing prompt text. All182 unit tests, typecheck/build,11 dual-origin groups and10 local plus10 public native-IDB groups passed. The independent static release verifies107 current/255 retained files and14 routes. No API, service configuration, service PID, business Key or GPU request changed. Earlier failures remain visible; physical-phone/full-fault/fleet acceptance remains HOLD.

## evidence-20261030.140 · 静态版本仍139.2 / pending-test

- [验证] 14组原生HTTP/IndexedDB专项通过，覆盖4状态×9查询故障、取消晚到、终态冲突、停止本页等待和跨页服务端预算；零生成POST。
- [验证] 6组冻结139.2页面专项通过：429按提示等待、503无提示暂停、双页恢复及模拟隐藏/离线后查询原ID成功，390px无横向溢出；非真机后台验收。
- [验证] 两项真实GPU批次各受理一次，第二项响应丢失后只读核对找回原ID；取消响应丢失后原ID确认cancelled，第一项继续执行。
- [交付] 第一项1,458秒成功，720×1080 PNG、720,638字节、SHA与8小时到期通过；3项实际UI另存/载入/刷新、4项真实任务JSON恢复通过，两MP4两GIF完整解码，样片去元数据，临时Key禁用401。
- [取证] 本机Chromium中文持久profile下载PNG原生崩溃有最小复现；使用已核验为同一目录的既有ASCII联接完成复查，失败保留，不作为产品/API修复。
- [复查] 原Key只允许standard，实际页面禁用draft正确；旧脚本和原记录保留、不补发，修正验证脚本后按授权720×1080 standard另建批次。
- [边界] 产品源码、静态版本和线上服务均未改动/重建/部署/重启；原API源码、配置、发布指针与服务PID只读核对相同。

Evidence140 adds14 native fault groups and6 frozen-UI polling groups without changing or redeploying139.2. Exactly two actual GPU members ended succeeded/cancelled, recovering original IDs after lost responses. The first took1,458 seconds and produced a720,638-byte720×1080 PNG with verified SHA and eight-hour expiry. Four actual backup and three original-delivery UI checks passed; two MP4s/two GIFs fully decode. The temporary Key is disabled with401. Existing policy is preserved. Windows native-browser profile-path download failures and their minimal diagnosis remain visible; the same profile worked through a verified existing ASCII junction. Physical-phone/background/fleet acceptance remains HOLD.

## canvas-20261030.139.2 · 已独立部署 / pending-test

- [修复] 原API暂不可用时，使用已记住或当前输入的原Key读取已校验本机原件；离线读取不创建远端客户端，错误Key/源站、退出及身份切换继续隔离。
- [修复] 画布、素材库与GLB预览使用同一本机身份；离线可预览/另存，远端生成和云端操作仍需原API鉴权成功。
- [修复] 独立静态服务为素材库深链增加两条精确SPA规则，避免与assets构建目录重名返回404；只重载画布自身配置，原API保持原样。
- [验证] 179单元/类型/生产构建、11组双源和10组离线身份专项通过；107当前文件及170旧哈希资源逐项公网验证，14条静态路由通过。
- [验证] 一项真实720×1080图片编辑完成；上传与生成响应丢失恢复同一素材/任务，损坏与截短下载拒绝。139.2真实离线恢复7项通过，原件长度/SHA相同，复查零生成POST。
- [交付] 双语README、真实桌面/390px图集、离线恢复MP4/GIF和完整46项待办同步；公开PNG移除元数据，原件私有核验；验收Key已禁用并确认401，自建浏览器已关闭。

The independently deployed139.2 restores verified local originals using their original Key while the API is unavailable, without granting remote access. Ten native identity cases and seven actual saved-GPU-image UI checks passed. The static-only assets-route collision was fixed. All107 current files and170 retained old assets were verified; only the canvas configuration was reloaded. One actual image-edit job was recovered after lost responses; corrupt/truncated downloads were rejected. The temporary Key is disabled. Earlier failed runs remain visible; full acceptance is still HOLD.

## canvas-20261030.139.1 · 已独立部署 / pending-test

- [修复] 画布缺少发布校验或只读核对能力时，提示保留原记录并使用原API已支持的能力，不再引导为画布升级中转服务。
- [验证] 真实上传/生成响应丢失只创建一项任务；139.1独立静态107文件与85项旧哈希资源已公网核验，原API和静态服务均未重启；离线刷新问题在139.2修复。

Missing-capability messages preserve the original request and explain existing API usage without directing a backend upgrade. A real138.1 browser test recovered the same uploaded asset and exactly one original GPU job after lost responses.139.1 static delivery passed; the image succeeded, and the offline-refresh defect discovered there was subsequently fixed and checked in139.2.

## canvas-20261030.138.1 · pending-test

- [修复] 将画布源站和原API地址分开；未配置默认API时不再猜测画布源站，清空/新建连接也沿用显式API默认值。
- [新增] 独立39445静态入口、发布目录、systemd单元与逐文件摘要；直接调用原39443API，不增加转发服务。
- [调整] 原API两个工作区冻结为只读，未部署的138后端候选已撤回，不改原API/Worker/协议/调度/数据库/Admin。
- [新增] 179单元/类型/构建、11组合成双源和2组实际目录浏览器证据、107文件公网校验、桌面/390px截图及MP4/GIF。
- [新增] 双语使用文档、跨源本机数据迁移说明、23项架构边界和完整F-01～F-46跟踪；只读验收Key已禁用，自有临时资源已清理。

The independent canvas now separates its own origin from the original API, is served on39445, and sends business requests directly to the unchanged API on39443. An undeployed backend candidate was withdrawn. Validation covers static delivery and actual catalog access, with synthetic media tours clearly labeled; no new GPU generation or physical-phone acceptance is claimed. Full acceptance remains HOLD. The original upstream version and changelog remain separate from this deployment identifier.
