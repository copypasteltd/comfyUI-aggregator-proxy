# 93 · 真实 Workflow 离线发布包构建

本增量优先补齐真实资源接入时的发布准备：将已有的六类 Workflow 发布草稿绑定到本地 ComfyUI、模型及自定义节点版本，生成可直接交给现有 Admin 导入流程的 `manifest.json`。关联待办 18～24、27、46、74；这些完整验收项仍需真实生成、质量评审和渠道批准。

工具位于 `app/scripts/workflow-package.py`，使用本项目原生 Python 和 Git。它只读取操作者明确指定的本机文件，不连接 ComfyUI、不下载模型、不安装节点、不运行 Workflow、不使用 Docker/WSL，不写入 ComfyUI 目录或数据库。新包输出到全新目录，拒绝覆盖。

## 输入和构建

先通过 Admin 的参数与元数据表单，或直接编辑文件，准备符合现有 `WorkflowManifest` 的发布草稿。六类能力共用同一入口：`text_to_image`、`image_to_image`、`references_to_image`、`text_to_video`、`references_to_video`、`text_to_audio`。音频仍是 ComfyUI Workflow 产物，使用格式 1.1；其余沿用 1.0。

执行图必须是 API-format；绑定、最终输出、受控规格及运行预算仍需按真实 Workflow 填写。工具不会猜测提示词、参考图作用、视频时长或音频节点的语义。UI-format 文件需先在 ComfyUI 导出为 API 格式。

草稿中的 `dependency_lock` 可以先只填写实际使用的模型相对路径和节点目录：

```json
{
  "models": [{"path": "checkpoints/existing-model.safetensors"}],
  "custom_nodes": [{"directory": "existing-node-repository"}]
}
```

模型路径相对于指定 ComfyUI 的 `models/`，节点目录相对于 `custom_nodes/`。这是依赖声明片段，不能单独当作发布包导入。工具流式计算真实模型 SHA-256/字节数，读取本地 ComfyUI 和节点 Git HEAD，替换草稿中的旧锁值。Git 仓库必须具有自身 `.git` 且工作目录干净；模型、节点列表须由操作者完整核对，工具只验证所声明的依赖。

从 `app/` 执行，预先创建输出的父目录：

```powershell
New-Item -ItemType Directory -Force .local/workflow-packages
./.venv/Scripts/python.exe -X utf8 scripts/workflow-package.py build --manifest .local/my-workflow-draft.json --comfy-root C:/ComfyUI --output .local/workflow-packages/my-workflow-v1
```

默认单模型读取上限为 64 GiB，可通过 `--max-model-bytes` 显式设置，最大 1 TiB。输入与生成的 manifest 均受现有 1 MiB、有界结构、无损 JSON 及重复键拒绝规则约束。依赖路径拒绝越界、UNC、设备别名、备用数据流、符号链接和 junction；模型读取前后核对文件身份及修改信息，拒绝读取期间变化。

## 输出与复验

新目录包含三个文件：

| 文件 | 用途 |
| --- | --- |
| `manifest.json` | 使用现有冻结格式的发布草稿，可直接导入 Admin |
| `provenance.json` | 源文件摘要、manifest 规范摘要、声明依赖数量及验证范围 |
| `checksums.json` | 前两个文件的 SHA-256；最后写入，缺失或中断时复验失败 |

包不复制权重或节点代码。保留完整本地路径、私有图和模型信息的输入/输出包应位于忽略目录，不能将真实客户数据或私有权重加入 Git。命令输出仅提供固定错误码、能力类型、摘要及计数，不输出图内容、提示词、原始异常或本机绝对路径。

```powershell
./.venv/Scripts/python.exe -X utf8 scripts/workflow-package.py verify --package .local/workflow-packages/my-workflow-v1
./.venv/Scripts/python.exe -X utf8 scripts/workflow-package.py verify --package .local/workflow-packages/my-workflow-v1 --comfy-root C:/ComfyUI
```

第一条验证成员、字节摘要、manifest 结构/语义及范围记录；第二条同时重新读取实际模型和 Git 版本，拒绝本地依赖漂移。校验和用于完整性检查，不是发布者签名或可信来源认证。

离线构建会清除所有运行画像的 `approved`、`evidence_id`、`peak_vram_bytes`、`warm_p95_milliseconds` 声明。它们不能证明当前本机环境已经通过生成、质量或性能测试。包始终记录 `runtime_verified=false`、`production_approved=false`，完整依赖覆盖也明确保持待核对。已有可信的渠道批准由服务端另行管理，工具不会修改它。

## 接入现有核心链路

1. 在 Admin → Workflow 管理导入生成的 `manifest.json`，核对项目、能力、图、参数与规格。
2. 冻结发布版本，通过已有渠道同步与依赖探测确认实际环境。
3. 建立模型/渠道绑定，提交已有管理验证任务，保留真实图片、视频或音频产物。
4. 完成质量、资源画像及参考语义核对，再通过现有渠道运行批准和模型发布流程。
5. 对 Sub2API、业务 Key、下载及结算进行真实端到端验收。

该工具复用现有导入、冻结、同步、验证与批准链路，不增加对外端点、不放宽业务 Key 权限、不更改 GPU 调度或重试。

## 验证记录

针对六类包构建、既有导入解析、执行图编译、第二参考图、源图、整数 seed、依赖漂移、篡改、中断包、覆盖拒绝、路径与 junction、运行批准伪造、真实原生 Git 及脱敏失败输出进行专项验证。实际结果以本增量独立报告为准；没有运行真实 GPU，也不将历史全量 CI 结果写成新代码的完整回归。

[专项验证](./evidence/2026-10-26/workflow-package-python-93.json) 通过 70 项检查，零失败/错误/跳过，包含六类导入/编译和实际原生 Git/CLI；Ruff、工具类型检查通过。[首轮失败记录](./evidence/2026-10-26/workflow-package-first-failure-93.json) 保留 21 项中 7 项失败及已修正的 Windows/Git 原因。原始 JUnit/日志保存在忽略的本地证据目录，不重写旧报告。WP-12 据已实现的离线发布准备更新为进行中，真实六类产品化仍待资源；69 项完整出口保持。
