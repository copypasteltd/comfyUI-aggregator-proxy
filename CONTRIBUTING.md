# 开发、文档与推送 / Development, documentation and synchronization

[中文 README](README.md) · [English README](README.en.md) · [项目约束 / Project rules](AGENTS.md)

## 中文

原生 CI 使用 `app/scripts/ci.ps1 -Action Verify`（在 app 执行）。GitHub 必需的根 `.github/workflows` 仅保存平台入口；业务脚本仍在 app。主分支受信代码在专用原生 Windows Runner 执行，启用与准备步骤见 [83](docs/83-原生CI门禁与失败证据.md)。CI 报告不可自动包含原始日志、JUnit、凭证或数据库备份；生成文件漂移应修复后新开一轮。

1. 从 `AGENTS.md`、`docs/09-开发总纲与强制约束.md` 和对应专题开始。应用代码、配置、脚本与构建位于 `app/`；设计、机器契约、演示素材和证据位于 `docs/`。根目录保留仓库入口文件。
2. 使用原生进程，禁止 Docker/WSL/VM。浏览器自动化须在 `finally` 关闭自己创建的 page/context/browser，不关闭用户已有标签页。
3. 冻结契约变化先修改文档/机器契约，再同步实现、OpenAPI/前端类型、迁移及相应验证。维护 PostgreSQL 权威、物理 GPU 栅栏、提交前 journal、租户/Key 隔离和幂等不变量。
4. 用户行为、配置、接口、部署或验证范围变化时，同时维护 `README.md` 与 `README.en.md` 的对应章节，避免两种语言作出不同承诺。开发细节补充对应专题及 `docs/README.md`；新增专题同步英文导航。
5. 每次完成修改都在 `CHANGELOG.md` 顶部记录日期、具体变化、验证结果、迁移/兼容性及待办。不能把静态、模拟、实际设备或复用证据混为一谈。
6. UI 行为变化时更新相关页面截图/GIF/视频。使用合成数据；录制前完成登录，不拍摄密码、一次性 Key、会话 Cookie、服务凭证或真实客户素材。发布的 API 响应仅包含明确选择的非敏感字段。
7. 运行与变更匹配的检查，审查 Git diff 和发布候选。`.local/`、`.env`、数据库、journal、模型权重、安装包、原生二进制和依赖目录不得提交。`scripts/audit-repository.py` 检查完整凭证模式、当前本地秘密、私有路径与大文件，但不能替代人工审查。
8. 用户已授权后续完成的项目变更同步提交与推送到 `git@github.com:copypasteltd/comfyUI-aggregator-proxy.git`。使用当前工作分支、可读提交信息和普通推送；共享历史不强推、不自动 reset。出现远端新增提交时先读取并整合；分支保护或权限失败如实报告并保留本地提交。

```powershell
Set-Location app
./scripts/check.ps1 -Suite Docs
# 按实际变化选择 Python / Frontend / Sub2API / Sub2APILedger / Sub2APINative。
./.venv/Scripts/python.exe scripts/audit-repository.py
Set-Location ..
git diff --check
git status --short
git add <reviewed-files>
git diff --cached --stat
app/.venv/Scripts/python.exe app/scripts/audit-repository.py --staged
git commit -m "docs: describe the user-visible change"
git push
```

新分支首次使用 `git push -u origin <branch>`。`main` 的上游为 `origin/main`。新增安装包通过单独的发布流程处理；源码推送不隐式安装、部署或发布 GitHub Release。现有 `.gitattributes` 保留文件精确字节，并识别 Windows 换行，避免冻结契约、上游补丁与证据摘要因跨平台换行被静默改变；归档日志/XML 报告保留诊断输出的原始空白，新增文本按 `.editorconfig` 使用 UTF-8/LF。

## English

Run native CI with `app/scripts/ci.ps1 -Action Verify` from `app`. The root `.github/workflows` exception contains only the required platform entry; implementation stays in `app`. A dedicated native Windows Runner executes trusted main-branch code; provisioning and activation are documented in [topic 83](docs/83-原生CI门禁与失败证据.md). Public artifacts must exclude raw logs, JUnit, credentials and database backups. Fix generated-file drift before starting a new run.

1. Read `AGENTS.md`, the development rules and the relevant topic before editing. Keep code/configuration/scripts/builds under `app/` and design/contracts/media/evidence under `docs/`. Repository entry points belong at the root.
2. Use native processes; Docker, WSL and VMs are prohibited. Browser automation must close only its own page/context/browser in `finally`.
3. Update frozen documentation/contracts before changing their implementation. Synchronize generated OpenAPI/frontend types, migrations and applicable checks. Preserve PostgreSQL authority, physical GPU fencing, journaling before submission, scope isolation and idempotency.
4. Update matching sections in both READMEs when user behavior, settings, APIs, deployment or verification scope changes. Keep language promises aligned. Add implementation details to the relevant topic and update both documentation indexes for new topics.
5. Add a dated entry at the top of `CHANGELOG.md` for every completed update, describing the concrete change, validation, migration/compatibility and remaining work. Distinguish static, synthetic, actual-device and reused evidence.
6. Refresh relevant screenshots/GIFs/videos after UI changes. Use synthetic fixtures and authenticate before recording. Do not capture passwords, one-time Keys, cookies, service credentials or customer content. Publish only selected non-sensitive API response fields.
7. Run checks appropriate to the change and review the Git diff/publication candidates. Never commit local environments, secrets, databases, journals, weights, archives, native binaries or dependency directories. The audit script checks credential patterns, current local secrets, private paths and file size; review remains necessary.
8. The owner has authorized committing and pushing subsequent completed project changes to `git@github.com:copypasteltd/comfyUI-aggregator-proxy.git`. Use the current branch and ordinary pushes. Do not force-push or reset shared history. Read/integrate new remote changes first; report access/protection failures and preserve local commits.

Use the commands above with the appropriate test suites. Set tracking with `git push -u origin <branch>` for a new branch. Source synchronization does not deploy, install or publish a GitHub Release. Git attributes recognize Windows line endings while preserving exact bytes for hashed contracts, patches and evidence. Archived logs/XML reports retain diagnostic whitespace; author new text as UTF-8/LF.
