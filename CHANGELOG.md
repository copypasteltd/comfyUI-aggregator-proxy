# 更新日志 / Changelog

本文件记录面向使用者的变化、验证范围和迁移事项。日期使用 Asia/Taipei；源码推送不等于发布生产版本。

This file records user-facing changes, validation scope and migration notes. Dates use Asia/Taipei. Pushing source is not a production release.

## 2026-10-25 — 仓库首发 / Initial repository publication

### 新增 / Added

- 初始化 `copypasteltd/comfyUI-aggregator-proxy` 的源码仓库，保留 `app/` 代码与 `docs/` 设计/契约/证据分工。加入忽略规则、精确字节策略和编辑约定，排除运行时、数据库、凭证、模型、安装包及依赖缓存。
- 提供完整 [中文 README](README.md)、[English README](README.en.md)、英文文档导航和中英协作约定，说明架构、六类 Workflow、设备接入、启动、接口、Key/内容/额度、恢复与尚未验收范围。
- 新增 11 张真实 Admin 截图、2 段 MP4、2 张 GIF，以及经过范围限制的实际管理 API 响应。全部来自隔离 PostgreSQL 中的合成演示数据，不包含实际 GPU 生成或客户内容。
- 新增可复现录屏/媒体转换脚本与源码发布审查工具。约定后续代码、文档、演示及更新说明同步提交和推送。
- Establish the source repository while retaining code under `app/` and design/contracts/evidence under `docs/`. Exclude private runtimes, databases, credentials, weights, archives and dependency caches.
- Add complete Chinese/English READMEs, English documentation navigation and bilingual contribution guidance, covering architecture, six Workflow types, onboarding, startup, APIs, authorization, quota and recovery.
- Add 11 real Admin screenshots, 2 MP4 recordings, 2 GIFs and selected actual management API responses. All use isolated synthetic fixtures rather than GPU generation or customer content.
- Add reproducible capture/encoding and publication-audit tools; persist the requirement to synchronize future code, documentation, media and change notes.

### 已有功能基线 / Existing implementation baseline

- 第 76 增量：六类 ComfyUI 任务、持久调度、Windows Worker journal、图形 Admin、多业务 Key、内容管理、Sub2API Provider 集成及 Workflow 历史/归档。完整验收记录见 [76](docs/76-Workflow归档与恢复管理.md)。
- Increment 76 includes six ComfyUI task types, durable coordination, Windows Worker journals, visual Admin, multiple business Keys, content management, Sub2API Provider integration and Workflow history/lifecycle. See [recorded verification](docs/76-Workflow归档与恢复管理.md).

### 验证与迁移 / Verification and migration

- 本次为仓库与文档/演示工具整理，无数据库迁移、无运行接口或任务协议变化。执行文档/链接/请求示例、静态检查、合成录屏、媒体完整解码、发布内容审查及 Git 远端核对；不冒充重跑已有 3087 项完整 Python 回归。
- This delivery changes repository/documentation/demo tooling, with no database migration or runtime API/task protocol change. It checks documentation/links/request examples, static analysis, synthetic capture, full media decoding, publication candidates and the Git remote. It does not claim a fresh run of the historical 3087-check Python suite.
- 真实 Windows/Workflow/模型质量、S3/Redis、生产服务/联合恢复、10～20 台实机容量仍待验收。Actual devices/Workflows/model quality, S3/Redis, production services/joint recovery and 10–20-machine capacity remain unaccepted.

后续每次修改在本文件顶部增加对应日期的 Changes / Validation / Migration / Pending 说明。详见 [CONTRIBUTING](CONTRIBUTING.md)。
