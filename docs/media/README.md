# 演示素材 / Demonstration media

[中文项目入口](../../README.md) · [English project entry](../../README.en.md)

所有画面来自实际 Admin 和 Control API，使用独立、临时的原生 PostgreSQL。账号、租户、模型、Workflow、12 个排队任务和 3 个完成的示意产物均为合成夹具；没有运行真实 ComfyUI、GPU 推理或实机容量测试。示意产物由本地程序绘制，不能作为模型质量样例。当前 Admin 使用中文。

All captures use the actual Admin and Control API with disposable native PostgreSQL. Accounts, scopes, models, Workflows, 12 queued tasks and 3 completed illustration artifacts are synthetic fixtures. No real ComfyUI execution, GPU inference or physical-fleet capacity test is recorded. Illustrations are drawn locally and are not model-quality examples. The current UI is Chinese.

## 视频与 GIF / Videos and GIFs

| 素材 / Media | 内容 / Content |
| --- | --- |
| [巡览 GIF / Tour GIF](admin-tour.gif) | 总览、渠道、节点、任务、Workflow、模型、Key、内容 / Overview, channels, nodes, jobs, Workflows, models, Keys and content |
| [巡览 MP4 / Tour MP4](admin-tour.mp4) | 同一实际页面巡览，H.264 / The same actual UI tour in H.264 |
| [Workflow GIF](workflow-lifecycle.gif) | 音频 Workflow 的二次确认、归档、解除归档和发布历史 / Confirmation, archive, unarchive and release history for an audio Workflow |
| [Workflow MP4](workflow-lifecycle.mp4) | 同一连续操作录屏 / The same continuous operation recording |

![Workflow lifecycle demonstration](workflow-lifecycle.gif)

## 截图 / Screenshots

| 文件 / File | 页面 / Page |
| --- | --- |
| [overview.png](overview.png) | 全局队列与 GPU 占用 / Global queue and GPU occupancy |
| [channels.png](channels.png) | ComfyUI 渠道 / ComfyUI channels |
| [nodes.png](nodes.png) | Windows 节点 / Windows nodes |
| [queue.png](queue.png) | 任务与队列 / Jobs and queue |
| [workflows.png](workflows.png) | 六类 Workflow / Six Workflow types |
| [models.png](models.png) | 模型别名与路由 / Model aliases and routing |
| [api-keys.png](api-keys.png) | 多业务 Key / Multiple business Keys |
| [content.png](content.png) | 内容、标签与所有权 / Content, tags and ownership |
| [workflow-confirmation.png](workflow-confirmation.png) | 状态变更二次确认 / Lifecycle confirmation |
| [workflow-history.png](workflow-history.png) | 不可变版本历史 / Immutable release history |
| [mobile-overview.png](mobile-overview.png) | 390px 移动端 / Mobile at 390px |

## 响应与可复现性 / Responses and reproduction

[api-responses.json](api-responses.json) 保存录制期间真实归档/解除归档的请求体、HTTP 状态与响应；认证头、Cookie、CSRF 和登录响应不发布。[capture.json](capture.json) 记录浏览器清理与页面错误；[manifest.json](manifest.json) 记录图片/视频/GIF 的大小、摘要、编码与时长；[隔离环境清理](../evidence/2026-10-25/readme-media-capture.json) 单独记录。

The response file contains actual archive/unarchive request bodies, status codes and responses. Authentication headers, cookies, CSRF and login responses are excluded. Capture/manifest records describe browser cleanup, page errors, file hashes, encoding and duration. Isolated-environment cleanup is recorded separately.

在 `app/`、已准备好的原生开发环境中运行。录制 Python 需要 `playwright==1.58.0` 与本机 Microsoft Edge；可放在独立环境，不必改变服务 Python。`--browser-python` 指向该解释器。脚本仅允许项目所属回环 PostgreSQL，创建并清理唯一演示库，凭证留在忽略目录并在完成后删除。

Run from `app/` in the prepared native environment. The recorder requires `playwright==1.58.0` and local Microsoft Edge. Install it in a separate Python environment if preferred, then pass that interpreter with `--browser-python`. The script restricts itself to the owned loopback PostgreSQL instance, creates/removes a unique demo database and removes private capture credentials.

```powershell
# 示例路径须替换为自己安装了 Playwright 的原生 Python。
./.venv/Scripts/python.exe scripts/capture-readme-media.py --browser-python C:/path/to/media-python/python.exe
# 上一命令输出 private_run；将其作为 --run。
./.venv/Scripts/python.exe scripts/render-readme-media.py --run <private_run>
```

原始 WebM 与日志位于 `app/.local/readme-media/`，不提交。公开 MP4 为无声 H.264、yuv420p、faststart；GIF 使用调色板压缩并循环播放。浏览器三个 context 在 finally 中关闭，不操作用户已有标签页。转换使用锁定的原生 FFmpeg/ffprobe，验证工具摘要并检查输出。

Raw WebM/logs remain in ignored `app/.local/readme-media/`. Public MP4 is silent H.264/yuv420p with faststart; GIF uses a palette and loops. Three owned contexts are closed in `finally`; existing user tabs are untouched. Encoding uses hash-verified native FFmpeg/ffprobe.
