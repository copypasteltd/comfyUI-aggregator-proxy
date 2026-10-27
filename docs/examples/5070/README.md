# RTX 5070 精选工作流包

本包只包含经过平台真实生成验证的三个发布包及自行绘制的中性参考图。文件位于 [app/examples/5070-workflows](../../../app/examples/5070-workflows/)；不包含权重、凭证、原始私有提示词或其余候选草稿。结果与适用范围见 [122](../../122-RTX5070纯HTTP接入与调用验证.md)。

| 文件 | 公开模型别名 | 输入 | 已冻结规格 |
| --- | --- | --- | --- |
| t2v.json | comfy-5070-video-h3 | prompt | 512×288、1.625 秒、24 fps |
| i2v.json | comfy-5070-video-h3-i2v | prompt + 1 张首帧图 | 同上 |
| refs.json | comfy-5070-video-h3-refs | prompt + 2 张参考图 | 同上 |

模型使用已有 H3 FL2VA/Ref2VA INT8、Qwen3VL 编码器、视频/音频 VAE；双参考流程使用既有 Turbo LoRA。具体选项在各文件的 dependency_lock.model_inputs 中。API 声明只核对 ComfyUI API 版本、节点及选项，不代表权重文件经过主机哈希核验。

部署者在 Admin 导入所选 JSON 为草稿，冻结版本，再绑定配置了 remote_http 的渠道；先完成渠道应用和探测，再同步依赖，执行真实验证并批准运行画像，最后发布模型映射。GPU 资源键需匹配目标 Worker 配置。同一 GPU 的其他入口须共用资源键。不要直接把包内的示例参考文件名视为远端已有素材；平台会上传客户资产并替换 LoadImage 输入。

业务 API Key 需要授权新增模型、冻结版本、渠道及 jobs:create、jobs:read、videos:create、assets:upload、content:read、content:download 等所需权限。旧 Key 的限制不会自动扩张。本轮临时验证 Key 已禁用。以下命令使用项目现有核验客户端，先设置环境变量 COMFY_AGGREGATOR_API_KEY；输出保存在 app/ 内，重试时保留目录和 request-id。

```powershell
python app/scripts/verify_reference_api.py --base-url http://43.165.167.179:39443 --model comfy-5070-video-h3 --mode text-video --prompt "A green lime beside a white cup, soft daylight, fixed camera." --size 512x288 --seconds 1.625 --request-id my-5070-t2v-001 --output app/.local/my-5070-t2v-001
```

首帧调用改用模型 comfy-5070-video-h3-i2v、模式 video-first-frame，并添加一次 `--reference app/examples/5070-workflows/reference-0.png`；双参考调用改用 comfy-5070-video-h3-refs、模式 video-refs，分别添加 reference-0.png 和 reference-1.png 两次 --reference。每种不同请求使用不同的 request-id 和输出目录。

对应原生 HTTP 请求先 POST /v1/assets 上传参考图，再 POST /v1/videos：首帧使用 input_reference.asset_id，双参考使用 references 数组（subject/style）。保存返回的任务 ID，查询 GET /v1/jobs/{id}；成功后通过 GET /v1/content/{id}/download 鉴权下载。客户端脚本已检查重复提交的任务 ID、文件大小、SHA-256、HEAD 和 Range。

文生视频的 POST /v1/videos JSON 如下；请求头带 `Authorization: Bearer <业务 Key>`、`Content-Type: application/json` 和固定 `Idempotency-Key`：

```json
{
  "model": "comfy-5070-video-h3",
  "prompt": "A green lime beside a white cup, soft daylight, fixed camera.",
  "size": "512x288",
  "seconds": 1.625,
  "quality": "standard",
  "workflow_options": {"seed": 20261028}
}
```

同一内容/参数重试沿用同一幂等键；不要在网络超时后生成新键重复提交。首帧/双参考 JSON 使用对应别名，在上面加入 input_reference 或 references。包内运行画像的 approved=false 是正常的：冻结内容不携带运行授权，导入其他环境后仍需实际验证和独立批准。

仅接受冻结规格，720×1080 或其他时长需要另建预设并通过真实验证。三个短样本证明调用链路可用，高清、长片、视觉质量和容量另行验收。正式内容在成功后八小时到期；HTTP Worker 暂存保留到云端确认后至少一小时；ComfyUI 原文件清理仍是 [121 的待实现设计](../../121-ComfyUI原始产物移交即清理设计.md)。
