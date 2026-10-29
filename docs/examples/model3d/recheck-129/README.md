# 129 · 最小权限 Key 的真实 3D 调用 / Least-privilege 3D call

2026-10-29，云端 `linux-20261029.129.3` 下载核验，生成发生在 `129.2`，RTX 3080 TRELLIS2。本次临时 Key 的生成权限只有 `meshes:create`，没有图片、视频和音频生成权限；另有发现、上传、任务查询和内容下载所需的接口权限，3D 日额度为 1。Tenant / Project 仍需有正数额度。

| 项目 | 实测 |
| --- | --- |
| 模型 / 任务 | `comfy-3080-3d-trellis2` / `image_to_3d` |
| 任务 ID | `job_0a77f0f12cdb452d890c641636418cd1` |
| 最终状态 | succeeded |
| 产物 | 自包含静态 GLB 2.0，100,424,732 字节 |
| SHA-256 | `a7e1b5b6af2ef03f69f18c7a6f453d8e886811bae35ecb637a5b1a66df08a1f7` |
| 顶点 / 三角面 | 2,092,758 / 4,183,116 |
| 下载 / 保留 | HEAD 200，Range 206，完整摘要一致；完成后 28,800 秒到期 |
| 幂等 / 隔离 | 同请求返回原任务；同项目另一 Key 的查询和下载均拒绝 |

[实际请求](request.json)、[创建响应](created-job.json)、[完成响应](completed-job.json)、[内容元数据](content.json)、[模型用法](model-usage.json)均来自本次调用。两枚验证 Key 已停用；请用自己的 Key 重新上传源图并替换 asset_id，不要复用这些样本 ID。源图和冻结 Workflow 见[上级说明](../README.md)，复查、失败复现与最终安装见[文档 129](../../../129-三维兼容复查与GLB边界修复.md)。二进制 GLB 保存在本地验证目录和受鉴权保护的云端内容存储，不提交到 Git。

This fresh call uses only the mesh generation permission plus discovery, upload, job and content permissions. Both temporary Keys are disabled after verification. Upload your own source image with your own Key and replace the recorded asset ID. These real responses verify the bounded static GLB path; other 3D formats, interactive previews and model quality are outside this check.
