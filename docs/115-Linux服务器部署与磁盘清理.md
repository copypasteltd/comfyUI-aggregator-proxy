# 115 · Linux 服务器部署与磁盘清理

用户已明确授权在指定 Ubuntu 服务器原生部署、开放高位端口并清理垃圾缓存。本增量涉及 DEV-02、05、07、14、17、18、24、25、27、31、32；不改变生成接口、任务状态机、数据库迁移或 Windows Worker 协议。

## 部署边界与方案

服务器是 Ubuntu 24.04 / amd64，已有多个业务、PostgreSQL 16、私有 MinIO、Nginx 和 Sub2API，内存约 2 GiB。为本项目建立独立 Linux 运行目录、低权限服务账号、PostgreSQL 18 集群和数据库、私有版本化对象桶及限权存储身份；不覆盖既有业务配置或升级既有数据库。控制 API、维护 Runner 和对象 GC 使用 systemd 原生进程。Windows 发行候选不作为 Linux 安装包。

以核验过的 Windows 候选作为冻结迁移、契约和未变输入的种子；七个项目的 `py3-none-any` wheels 及 Admin 静态资源从当前已提交源码重新构建。第三方 Linux 依赖由当前 uv.lock 的精确版本和哈希安装。记录种子、当前提交、每个安装输入、Linux 工具及服务配置的摘要。Linux 使用单独的平台证据，不能继承 Windows 可执行程序的安装结论。

用户随后明确要求只开放高位 HTTP 端口、自行处理域名反代，并关闭 CORS 来源限制。最终入口按 [116](116-HTTP高位入口与任意来源访问.md) 执行，39443 使用普通 HTTP，允许任意来源的标准 API 方法和请求头。前期临时 HTTPS 检查仅保留为历史过程，不作为最终入口。数据库、对象存储和内部 API 留在回环，拒绝公网 internal 路径。

既有 Sub2API 保持原实例。本项目的定制 Provider 需独立版本与联合验证，不能把服务器上存在 Sub2API 当成已接入。真实 Windows 节点和六类 Workflow 尚未提供，部署管理面不等于生成能力或 10～20 台容量已验收；不发布虚构模型或替身渠道。

## 清理范围

先记录磁盘与服务，再清理 systemd 已归档旧日志、APT 下载缓存和 root 的 pip HTTP 下载缓存。日志先轮转，再按最多 7 天 / 512 MiB 两个限制回收归档；大小限制可能先于时间限制生效。不清理业务数据库、上传产物、MinIO 数据、应用目录、活动业务日志、其他服务的依赖或虚拟化数据。不执行未审查的 autoremove。

## 执行与验证

安装前审查原生依赖变更；迁移前备份并验证 pg_restore 归档目录。服务启动不自动建表。配置秘密只写受限文件，不进 Git、命令回显或公开证据。最终验证使用普通 HTTP，覆盖健康检查、真实 PostgreSQL 与 S3、Admin 登录/退出、CSRF 和未认证拒绝、静态资源、本项目服务重启及既有服务存活。域名反代由用户自行配置。

## 实际安装与运维入口

| 编号 | 对象 | 已部署配置 / 操作逻辑 |
| --- | --- | --- |
| 01 | 公网入口 | 普通 HTTP `39443`，根路径跳转 `/admin/`；`/v1/` 为对外业务 API，Admin API 位于 `/admin/api/v1/`。不在本服务配置域名证书。 |
| 02 | 原生 API | `127.0.0.1:32880`，单 Uvicorn 进程，生产模式；独立低权限账号 `comfy-control`。 |
| 03 | 数据库 | PostgreSQL **18.6**，独立 `127.0.0.1:55439` 和数据库 `comfy_aggregator`，迁移头 `3da02e7b5941`，45 张业务表。既有 PG16 服务保留。 |
| 04 | 对象存储 | 复用回环 MinIO，但新建私有版本化桶 `comfy-aggregator-prod-v1`、仅限该桶的独立凭据及 **4 GiB** 桶限额。上传、历史版本读取、Range、匿名拒绝与版本删除已实测。 |
| 05 | 发布目录 | `/opt/comfyui-aggregator/releases/linux-20261027.116.2`；`/opt/comfyui-aggregator/current` 为当前版本软链接；Python 3.13.1、uv 0.13.5。 |
| 06 | 配置与秘密 | `/etc/comfyui-aggregator/cloud.env` 为受限环境配置；`initial-admin.json` 为 root-only 初始账号交接。管理账号为 `admin`，密码独立交付，不写公开文档。 |
| 07 | 可写状态 | `/var/lib/comfyui-aggregator/` 下分离 PostgreSQL、API、Runner、GC、代理、spool 和 backups。密钥、数据库与业务对象必须与发布目录分别备份。 |
| 08 | 常驻服务 | `comfy-aggregator-{postgres,api,runner,gc,edge}.service` 已启用开机启动。Runner 负责控制面维护；它不是 GPU 执行 Worker。 |
| 09 | 数据库备份 | `comfy-aggregator-backup.timer` 每日 18:30 UTC，另有最多十分钟随机延迟、错过后补跑；已实际执行一次。归档写入 `backups/`，使用 PG18 `pg_dump` custom 格式，并检查 `pg_restore --list` 与 SHA-256。尚未完成异地备份与完整恢复演练。 |
| 10 | 专属 HTTP 代理 | 独立 Nginx master 和配置 `/etc/comfyui-aggregator/nginx.conf`；复用服务器 Nginx 二进制，不改现有站点。该二进制含 Lua 模块，专属配置增加 `/www/server/nginx/lib/lua/?.lua` 搜索路径。 |
| 11 | 资源约束 | API memory high/max 为 256/384 MiB，Runner 与 GC 各 96/160 MiB，专属代理 max 64 MiB；PG shared buffers 32 MiB、最多 48 连接。2 GiB 共享服务器的正式容量仍需实测。 |
| 12 | 系统依赖变更 | 安装独立 PG18 和 Ubuntu FFmpeg 6.1.1；PG 客户端/common、libpq 等共享客户端包随依赖更新，既有 PG16 服务端未升级。安装时临时阻止包管理器自动重启服务，操作后恢复。未运行本地或服务器 Docker / WSL。 |

查看状态和本项目日志：

```sh
sudo systemctl status comfy-aggregator-api.service comfy-aggregator-runner.service comfy-aggregator-gc.service comfy-aggregator-edge.service comfy-aggregator-postgres.service
sudo journalctl -u comfy-aggregator-api.service -n 100 --no-pager
sudo systemctl list-timers comfy-aggregator-backup.timer
sudo systemctl start comfy-aggregator-backup.service
```

修改公开域名等配置时使用 `sudoedit /etc/comfyui-aggregator/cloud.env`，随后重启本项目 API、Runner、GC。不要把完整环境文件、账号 JSON、备份归档或日志中的业务内容复制到公开问题单。Nginx 配置只重载 `comfy-aggregator-edge.service`，不能因此重载服务器其他业务的主 Nginx。

升级应先构建独立版本目录、核验 manifest 的每个摘要、安装锁定 Linux 依赖并检查，再切换 `current` 并重启本项目进程。发生问题时先停止新进程，再切回已核验且与当前数据库兼容的版本；不能把数据库自动降级当成代码回滚，也不能退回不支持本次 HTTP 要求的旧前端。初始账号、pepper、Fernet 和 S3 凭据保存在受限配置及部署恢复文件中，代码更新不能重新生成并覆盖它们。

## 清理实绩与验收边界

| 指标 | 清理前 | 首轮清理后 |
| --- | ---: | ---: |
| 根分区已用字节 | 31,627,575,296 | 28,662,788,096 |
| 根分区可用字节 | 8,686,821,376 | 11,651,608,576 |
| 根分区显示占用 | 79% | 72% |
| 日志占用 | 约 3.1 GiB | 约 504.5 MiB |

首轮回收 **2,964,787,200 字节，约 2.76 GiB**。收尾清理本次部署 uv 下载缓存约 161 MiB，并移除本轮创建、已经淘汰的 115.1 / 116.1 发布目录；当前 116.2 载荷完整保留。这一阶段根盘已用量从 30,027,132,928 降至 29,314,240,512 字节，另回收约 0.66 GiB，两轮累计约 **3.43 GiB**。不对其他业务执行依赖清理或历史数据删除。

安装与服务运行后，最终记录根盘可用 **10,991,493,120 字节，约 10.24 GiB**；这与“清理操作回收量”分别计量。[最终服务器记录](evidence/2026-10-27/linux-runtime-116.json) 核对原有 49 个运行服务均 active，既有 MinIO、Sub2API、PG16 PID 保持；原 Nginx 的 SysV 单元 MainPID 为 0，另核对原四个 worker PID 及其 10 月 24 日启动时间，不误把 worker PID 当成 MainPID。三份本项目日常归档可列举并核对摘要，完整恢复演练仍未完成。

[最终 116.2 HTTP/CORS 公网检查](evidence/2026-10-27/linux-public-http-cors-116-2.json) 通过 55 项；[真实数据库、对象存储和原生媒体检查](evidence/2026-10-27/linux-data-verification-115.json) 通过。音视频探针仅为 FFmpeg 合成，不能冒充 ComfyUI 生成。实际 Admin [截图、原速录屏和 GIF](media/linux-deployment-116/README.md) 单独标记为已部署空业务后台。完整真实生成、六类质量、10～20 台设备容量、定制 Sub2API 运行接入、异地备份及完整灾备仍需后续资源与验收。

最终 Linux 包：`linux-20261027.116.2.tar.gz`，SHA-256 `6f93aef22eda844bcc3171f410dd472baca7abebd06559d760f2ec5b69f997ba`，应用来源提交 `13e00b8`。94 个载荷逐项校验；七个项目 wheel 和 Admin 均重新构建。Windows 候选 `rc-20261026.113.1` 的 4280 Python / 29 安装 / 10 浏览器结论仍保留其原始来源范围，不能当作当前 Linux 全量验证。

官方依据：[PostgreSQL Ubuntu 安装源](https://www.postgresql.org/download/linux/ubuntu/)、[uv 固定版本发布](https://github.com/astral-sh/uv/releases/tag/0.13.5)、[MinIO 限权 access key](https://min.io/docs/minio/linux/reference/minio-mc-admin/mc-admin-accesskey-create.html)。
