# 独立画布更新记录 / Independent canvas release notes

## canvas-20261030.139.2 · 候选 / pending-test

- [修复] 原API暂不可用时，使用已记住或当前输入的原Key读取已校验本机原件；离线读取不创建远端客户端，错误Key/源站、退出及身份切换继续隔离。
- [修复] 画布、素材库与GLB预览使用同一本机身份；离线可预览/另存，远端生成和云端操作仍需原API鉴权成功。
- [验证] 10组原生浏览器身份/IndexedDB专项通过。139.1真实图像已完成，损坏/截短下载拒绝、原件SHA和刷新恢复通过；发现的API离线刷新问题保留失败记录，本候选继续验证修复。

Verified local media can now be read with its original Key while the API is unavailable. This local identity never authorizes remote calls. Ten native browser cases cover Key/origin isolation, disconnect, races and memory-only behavior. The real image finished and its original/checksum and download rejection checks passed; the recorded offline-refresh failure is being repaired and remains visible in the evidence.

## canvas-20261030.139.1 · 已独立部署 / pending-test

- [修复] 画布缺少发布校验或只读核对能力时，提示保留原记录并使用原API已支持的能力，不再引导为画布升级中转服务。
- [验证] 真实上传/生成响应丢失只创建一项任务；139.1独立静态107文件与85项旧哈希资源已公网核验，原API和静态服务均未重启；离线刷新问题在139.2修复。

Missing-capability messages now preserve the original request and explain supported API usage without directing a backend upgrade. A real138.1 browser test recovered the same uploaded asset and exactly one original GPU job after lost responses; output and candidate deployment validation remain pending.

## canvas-20261030.138.1 · pending-test

- [修复] 将画布源站和原API地址分开；未配置默认API时不再猜测画布源站，清空/新建连接也沿用显式API默认值。
- [新增] 独立39445静态入口、发布目录、systemd单元与逐文件摘要；直接调用原39443API，不增加转发服务。
- [调整] 原API两个工作区冻结为只读，未部署的138后端候选已撤回，不改原API/Worker/协议/调度/数据库/Admin。
- [新增] 179单元/类型/构建、11组合成双源和2组实际目录浏览器证据、107文件公网校验、桌面/390px截图及MP4/GIF。
- [新增] 双语使用文档、跨源本机数据迁移说明、23项架构边界和完整F-01～F-46跟踪；只读验收Key已禁用，自有临时资源已清理。

The independent canvas now separates its own origin from the original API, is served on39445, and sends business requests directly to the unchanged API on39443. An undeployed backend candidate was withdrawn. Validation covers static delivery and actual catalog access, with synthetic media tours clearly labeled; no new GPU generation or physical-phone acceptance is claimed. Full acceptance remains HOLD. The original upstream version and changelog remain separate from this deployment identifier.
