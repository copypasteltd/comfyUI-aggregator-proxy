# 独立画布更新记录 / Independent canvas release notes

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
