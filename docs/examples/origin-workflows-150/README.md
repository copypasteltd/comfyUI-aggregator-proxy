# 原生共享 Workflow · 调用示例

[完整方案及实际检查](../../150-原生工作流完整目录与源站操作修复.md) · [Skill 原生参考](../../../app/skills/media-gen-skill/references/comfy-origin.md)

先安装 [Skill 1.5.1](https://gen.sub2share.xyz/skills/media-gen/1.5.1/skill.md)，或使用仓库 `app/skills/media-gen-skill/scripts/comfy_origin.py`。以下命令在 Skill 解压目录执行；单文件安装先按其说明提取客户端。环境变量 `MEDIA_GEN_API_KEY` 由使用者私下设置，Key 须在 Admin 中取得指定设备的原生访问权限。

```powershell
python scripts/comfy_origin.py --session-file private/native.json login
python scripts/comfy_origin.py --session-file private/native.json devices
python scripts/comfy_origin.py --session-file private/native.json get rtx4090 "api/userdata?dir=workflows&recurse=true&split=false"
python scripts/comfy_origin.py --session-file private/native.json get rtx4090 "api/v2/userdata?path=workflows"
```

默认服务为 https://gen.sub2share.xyz。GET 返回真实源目录；这些原生接口使用登录后派生的会话，不能仅用业务 Bearer 调用。业务公开模型仍使用 `/v1/models`，数量与源文件不同。

下面只对**自己新建**的文件操作。`workflow.json` 是自己准备的 ComfyUI UI 格式 Workflow；仅保存文件，不执行生成。先取得设备占用；若已有自己的占用则沿用，不抢占其他用户。

```powershell
python scripts/comfy_origin.py --session-file private/native.json acquire rtx4090
python scripts/comfy_origin.py --session-file private/native.json save-workflow rtx4090 my-example.json workflow.json --shared --operation-file private/save.json
python scripts/comfy_origin.py --session-file private/native.json move-workflow rtx4090 my-example.json my-renamed.json --operation-file private/rename.json
python scripts/comfy_origin.py --session-file private/native.json delete-workflow rtx4090 my-renamed.json --shared --operation-file private/delete.json
```

每次新修改使用新的操作文件。若响应未知，使用相应原文件恢复，例如 `python scripts/comfy_origin.py --session-file private/native.json resume-workflow private/save.json`。不要删意图文件、换 Key 或重新命名操作后盲重投。个人保存省略 `--shared` 和 `--operation-file`，写入 `workflows/@personal/`；跨个人/共享目录移动使用另存为。

完成且所有自己的生成明确结束后，归还**本次由自己新取得的**设备，再退出：

```powershell
python scripts/comfy_origin.py --session-file private/native.json release rtx4090
python scripts/comfy_origin.py --session-file private/native.json logout
```

如果只是沿用用户已有占用，不应代替用户归还。会话文件和操作文件均应私藏；不要提交仓库或作为公共示例上传。[实际 30 项目录/共享 CRUD 证据](../../evidence/2026-11-16/origin-workflows-150-live.json) 和[最终浏览器验证](../../evidence/2026-11-16/origin-workflows-150-browser.json) 与上述通用命令分开记录，不将示例标成另一次实机执行。

English: sign in with an independently authorized Key, discover device workflows, acquire only an available device, then explicitly opt into shared writes with durable operation files. These commands save, rename and delete your own source file without running it. Recover uncertain writes with `resume-workflow`; personal saves default to `@personal`. Return only ownership acquired for your own operation and log out. Keep all session and intent files private.
