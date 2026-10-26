# 115 · Linux 服务器部署与磁盘清理

用户已明确授权在指定 Ubuntu 服务器原生部署、开放高位端口并清理垃圾缓存。本增量涉及 DEV-02、05、07、14、17、18、24、25、27、31、32；不改变生成接口、任务状态机、数据库迁移或 Windows Worker 协议。

## 部署边界与方案

服务器是 Ubuntu 24.04 / amd64，已有多个业务、PostgreSQL 16、私有 MinIO、Nginx 和 Sub2API，内存约 2 GiB。为本项目建立独立 Linux 运行目录、低权限服务账号、PostgreSQL 18 集群和数据库、私有版本化对象桶及限权存储身份；不覆盖既有业务配置或升级既有数据库。控制 API、维护 Runner 和对象 GC 使用 systemd 原生进程。Windows 发行候选不作为 Linux 安装包。

源码、平台无关 Python wheels 和 Admin 静态资源从核验过的候选提取；第三方 Linux 依赖由当前 uv.lock 的精确版本和哈希安装。记录原候选、提交、每个安装输入、Linux 工具及服务配置的摘要。Linux 使用单独的平台证据，不能继承 Windows 可执行程序的安装结论。

用户随后明确要求只开放高位 HTTP 端口、自行处理域名反代，并关闭 CORS 来源限制。最终入口按 [116](116-HTTP高位入口与任意来源访问.md) 执行，39443 使用普通 HTTP，允许任意来源的标准 API 方法和请求头。前期临时 HTTPS 检查仅保留为历史过程，不作为最终入口。数据库、对象存储和内部 API 留在回环，拒绝公网 internal 路径。

既有 Sub2API 保持原实例。本项目的定制 Provider 需独立版本与联合验证，不能把服务器上存在 Sub2API 当成已接入。真实 Windows 节点和六类 Workflow 尚未提供，部署管理面不等于生成能力或 10～20 台容量已验收；不发布虚构模型或替身渠道。

## 清理范围

先记录磁盘与服务，再清理 systemd 已归档旧日志、APT 下载缓存和 root 的 pip HTTP 下载缓存。日志先轮转，再按最多 7 天 / 512 MiB 两个限制回收归档；大小限制可能先于时间限制生效。不清理业务数据库、上传产物、MinIO 数据、应用目录、活动业务日志、其他服务的依赖或虚拟化数据。不执行未审查的 autoremove。

## 执行与验证

安装前审查原生依赖变更；迁移前备份并验证 pg_restore 归档目录。服务启动不自动建表。配置秘密只写受限文件，不进 Git、命令回显或公开证据。先回环验证，再开放专属高位端口；逐项验证 TLS、健康检查、真实 PostgreSQL 与 S3、Admin 登录/退出、CSRF 和未认证拒绝、静态资源、服务启停恢复及既有服务存活。

实际结果在执行后补充。完整恢复演练、域名证书、真实生成和全量容量验收按各自证据签收。

官方依据：[PostgreSQL Ubuntu 安装源](https://www.postgresql.org/download/linux/ubuntu/)、[uv 固定版本发布](https://github.com/astral-sh/uv/releases/tag/0.13.5)、[MinIO 限权 access key](https://min.io/docs/minio/linux/reference/minio-mc-admin/mc-admin-accesskey-create.html)。
