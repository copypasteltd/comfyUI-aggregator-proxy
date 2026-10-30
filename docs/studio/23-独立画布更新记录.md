# 独立画布更新记录 / Independent canvas release notes

<!-- independent149:start -->
## canvas-20261031.149.1 · Web原件下载与播放失败处理 / pending-test

- [修复] 普通WebM及批次JPEG/WebP下载使用本机原件MIME/字节；缺少存储原件明确拒绝，不回退残留URL。
- [新增] 原生video/audio失败提示和原件下载，真实替换后按新来源判断；图层操作栏同步当前节点，下载不打开编辑面板。
- [验证] 三引擎本机/公网36+36组、78次下载、18截图；203单元/类型/构建、18协议、11双源、12守卫及ZIP/清理回归。
- [交付] 独立静态149.1与兼容149.0，全部新旧资源/路由核对；双语、文档34、两MP4两GIF和失败留证更新。原API/全部服务/配置不变。
- [待验] Windows WebKit解码/Cookie、实际Safari、完整播放/声音/后台、键盘触控、配额/故障及全部46项；完整Web签收HOLD。

Web149.1 preserves original media formats, refuses missing keyed bytes, and handles native playback errors without failing a completed task. Real replacement and layer action selection are verified. Three engines passed 72 local/public groups with 78 actual downloads. Compatible recovery is prepared; only independent static files changed. Windows WebKit decoding/Cookie isolation, actual Safari and full Web acceptance remain HOLD.
<!-- independent149:end -->


## 证据148 · 混合媒体跨浏览器/跨源站备份恢复，产品仍146.1 / pending-test

- [验证] Firefox→Chromium→Windows WebKit→Firefox的实际ZIP下载/文件输入、刷新、再次导出共34组通过；九节点/五连线/八原件、重复参考、新键和GLB下载字节一致，任务不重提。
- [待验] Windows WebKit四音视频均code=4，解码未通过；原件经最终Firefox再次载入正常。播放器失败说明、普通WebM另存命名及更多格式继续完善，不宣称Safari支持。
- [文档] 文档33、双语README、全部46项、12通过截图与完整所选页两MP4/两GIF同步，三次测试夹具/定位/水合失败保留并关闭全部自有浏览器。
- [边界] 仅Web、合成小文件、无业务Key或GPU请求；复用146.1冻结203单元回执，不冒充重新构建。原API/静态配置、指针及服务PID不变，无部署或重启。

Evidence148 validates actual mixed-media ZIP hand-offs across three native engines and fresh public/LAN origins,34 checks in four stages. Eight original lengths/MIME/SHA, nine nodes, five edges, references, reload and GLB downloads match; tasks are not replayed. Windows WebKit cannot decode the four audio/video fixtures, while final Firefox reloads them. Playback, general WebM save naming and actual Safari remain pending. Bilingual document33, screenshots and two complete selected page MP4/GIF recordings preserve the limitations and early fixture/script failures. No product/API change or deployment; full Web acceptance stays HOLD.

## 证据147 · Web浏览器验收补充，产品仍146.1 / pending-test

- [验证] Firefox新增38组、Windows WebKit本机功能27组通过；包括真实文件输入/下载、ZIP原件/引用/刷新、双页保护及390px视口。
- [修正] 测试样片IDAT CRC损坏已替换为校验通过的RGB PNG；原生SDK/revision匹配、持久测试profile和无用启动页生命周期整理，产品源码不改。
- [待验] Windows WebKit携带Cookie的问题由不加载产品代码的最小fetch复现；Chromium/Firefox omit与include正对照正常。WebKit目录总回执失败并保留，不宣称实际Safari支持。
- [文档] 文档32、双语README、全部46项、截图及完整所选页两MP4/两GIF同步。原API与静态服务/指针/配置不变，无部署、重启、业务Key或生成请求。

Evidence147 keeps the frozen146.1 application. Firefox adds38 passed groups, Windows WebKit27 local-original/project groups; WebKit Cookie isolation remains unaccepted and reproduces without product code. Matching native browsers, valid PNG fixtures, screenshots and two complete selected page MP4/GIF recordings are documented. No new product release, rebuild, deployment, API/config/service change or GPU work. Full Web acceptance remains HOLD.

## canvas-20261030.146.1 · Web端独立交付 / pending-test

- [调整] 需求、开发约束和全部46项仅按Web应用执行；原生客户端与应用商店不在范围，原API仍只读。
- [修复] 本机清理页只接受最新读取结果，两个页面释放后的旧快照不再覆盖新状态；无新增计时器、重试或限制。
- [新增] 完整存储契约、摘要绑定与兼容恢复146.0，原子切换前拒绝不兼容目标；恢复包精确复用145.1应用并保留新旧资源。
- [交付] 实际公网Web回退/恢复8项、203单元/类型/构建、18原生、12发布守卫、清理/ZIP回归及114当前/688旧资源、15路由通过。截图、完整所选页面MP4/GIF、双语README和文档31同步；旧失败/跨引擎中止保留，完整Web验收HOLD。

Web-only146.1 keeps the latest cleanup snapshot authoritative and binds the complete storage contract to verified static releases. Compatible recovery146.0 reuses145.1 application bytes and retains new/old lazy assets. Eight actual public rollback/restore checks preserve saved projects, originals and frozen tasks; v4-only targets are refused. Current evidence covers Chromium; interrupted Firefox/WebKit checks are not counted.114 current/688 retained files and15 routes passed, with no original API/config/service changes or new GPU jobs. Bilingual docs31, screenshots and complete selected page MP4/GIF recordings accompany this release. Full Web manual/fault acceptance remains HOLD/pending-test.

## canvas-20261030.145.1 · 已独立部署 / pending-test

- [新增] 新上传/ZIP导入普通原件与文档同库保存，独立不可覆盖的新键；旧原件保留并可读取。
- [新增] 独立本机安全清理入口，文档先保存、原生连接占用保护、原件备份与显式逐项/整批清理；实际删除同事务复核全部引用，未知状态保留。
- [调整] 日志升级v5，保留原文档/原件/上传/原任务正文及幂等键/预算；旧v4调用被阻止，不降级或回退仅支持v4的144.1。
- [交付] 203单元/类型/构建、18原生协议、10本机+10公网清理、14本机+14公网ZIP、11双源及14媒体回归；114当前/598旧资源与15路由、双语文档30/README、截图和完整解码MP4/GIF。原API及服务/配置不改；无业务Key/GPU任务，完整签收HOLD。

145.1 adds explicit cleanup for newly controlled local originals in the document database. Native connection liveness protects active editors; every delete rechecks all references and registrations in one transaction. Old raw originals and previews stay retained. Downloads preserve bytes. Journal v5 retains existing data and fences v4 callers; do not roll back an upgraded client to v4-only144.1. 203 units/type/build,18 protocol groups,10 local+10 public cleanup groups,14 local+14 public ZIP groups,11 dual-origin and14 media regression groups passed. Original API/services/config remain unchanged; no business Key/GPU task. Full acceptance remains HOLD.

## canvas-20261030.144.1 · 已独立部署 / pending-test

- [新增] 存储页只读普通原件引用清单、缺件/未知记录提示及JSON下载；不读取原件正文、不写库、不发业务请求，无引用不等于可删除。
- [新增] GLB内嵌动画片段选择、播放/暂停与起点复位，默认暂停；隐藏停止/返回恢复，关闭/离开/上下文丢失释放帧和三维资源。
- [设计] 普通跨库安全回收的受控同库原件/占用、旧版屏障和迁移验收契约，未升级DB或启用普通文件清理。
- [交付] 197单元/类型/构建、11双源、14ZIP、14本机+14公网媒体专项，109当前/510旧资源及14路由；文档29、双语README、截图和完整解码MP4/GIF。原API与服务/配置不改，无实际Key/GPU任务；完整签收HOLD。

144.1 adds a read-only local-original inventory with reference categories, missing-original diagnosis and JSON download. It reads no Blob bodies, writes no database records and makes no business requests. Unconfirmed files remain retained. Embedded GLB clips now have explicit selection, play/pause and reset; hidden pages stop frame updates and disposal releases animation/WebGL resources. Safe ordinary-media cleanup remains a documented future contract.197 units/typecheck/build,11 dual-origin groups,14 ZIP groups,14 native plus14 public media-tool groups and109 current/510 retained files with14 routes passed. Screenshots and the full MP4/GIF decode. Original API/services/config remain unchanged; no actual Key/GPU job was created. Full acceptance remains HOLD.

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
