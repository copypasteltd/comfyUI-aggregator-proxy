# TRELLIS2 单图生成 3D / Image-to-3D

此目录包含实际 3080 ComfyUI 图转换的 [Workflow 1.2 发布包](trellis2.workflow.json) 和本地绘制的 [中性机器人输入](neutral-robot.png)。不包含模型权重、API Key 或客户素材。实际验证、批准、部署版本和限制见 [128](../../128-ComfyUI三维模型产物与3080适配.md)。发布包中的 runtime_profiles 不伪造 approved；运行批准记录由平台在真实验证成功后另行维护。

The package contains 26 API nodes, one final Save3DAdvanced node and five locked model files. It preserves the existing TRELLIS2 structure, shape and color stages, while removing the unrelated input-preview SaveImage. All three sampler seeds bind to the caller's seed. Model file paths, bytes, SHA-256 and ComfyUI commit are frozen; weights are not downloaded or bundled. Color is carried by mesh vertex attributes, without a promise of UV/PBR textures.

## API

先用有权限的业务 Key 查询 `GET /v1/models`、`GET /v1/models/comfy-3080-3d-trellis2` 或 `/v1/capabilities`。仅以实时发现可见且完成运行批准的发布为准。向 `/v1/assets` 上传 neutral-robot.png，multipart 字段为 **file**，同 Key 使用返回的 asset ID：

```json
{
  "model": "comfy-3080-3d-trellis2",
  "task_type": "image_to_3d",
  "input": {"source_image": {"asset_id": "REPLACE_WITH_UPLOADED_ASSET_ID"}},
  "parameters": {"quality": "standard", "seed": 17, "n": 1}
}
```

POST `/v1/jobs` with `Authorization: Bearer YOUR_BUSINESS_KEY` and a stable `Idempotency-Key`. Poll `GET /v1/jobs/{id}`. When `status=succeeded`, retrieve `/v1/content/{artifacts[0].id}` and `/v1/content/{artifacts[0].id}/download`. Save as `.glb`, verify bytes/SHA-256 and download before expires_at. A polling timeout does not cancel the server job or authorize a duplicate submission.

当前输入为一张静态 PNG/JPEG/WebP（最多 20 MiB、2500 万像素）；不接受 prompt、negative_prompt、mask、references、size、seconds、fps 或 strength。standard 对应发布包固定参数，n 固定 1。GLB 是自包含静态三角网格 2.0，最大 512 MiB；下载时仍需同一逻辑业务 Key。旧 Key 不会自动获得 meshes:create 或 3D 额度。

## Media Gen Skill 1.1.0

```sh
python scripts/media_gen.py models
python scripts/media_gen.py describe comfy-3080-3d-trellis2
python scripts/media_gen.py generate --model comfy-3080-3d-trellis2 --source-image neutral-robot.png --seed 17 --wait --timeout 1800 --output-dir model-output
```

Admin 中可以查看任务、模型说明、发布/渠道、3D 内容元数据并下载。Use Blender or another GLB viewer to inspect the model. Interactive Admin rotation/mesh rendering, animated/skinned/compressed GLB, external textures, OBJ/MTL, multi-file glTF and Sub2API 3D settlement remain outside this delivery profile.
