# Model usage, Skill and automatic reclamation · actual delivery

[中文](README.md) · [Delivery record](../../124-模型使用说明与公开Skill交付.md) · [Skill Release](https://github.com/copypasteltd/comfyUI-aggregator-proxy/releases/tag/media-gen-skill-v1.0.0)

Actual Lime Admin at `https://gen.sub2share.xyz`, showing published-model documentation editing, reference slots and a 600-second idle policy. The 13.56-second recording plays at original speed. Login recording stays private, and every browser context was closed.

![Actual model usage and idle settings](model-usage-and-idle-policy.gif)

[Original-speed MP4](model-usage-and-idle-policy.mp4) · [Usage](screenshots/model-usage.png) · [Description editor](screenshots/model-documentation-editor.png) · [Reference slots](screenshots/model-references.png) · [Mobile usage](screenshots/model-usage-mobile.png) · [Completed release](screenshots/memory-completed.png) · [Execution proof](screenshots/memory-evidence.png) · [Mobile memory policy](screenshots/memory-mobile.png)

The extracted public Skill Release generated and downloaded an image, text video, first-frame video and music. Inputs use this project's lime/cup examples and reference artwork. Exact API downloads passed size and SHA-256 checks. Public images omit embedded PNG metadata; public audio/video samples were losslessly remuxed without metadata to avoid distributing private ComfyUI graphs and device paths. Their hashes are recorded separately from the original downloads.

![Actual 720×1080 generation](generated-720x1080.png)

| Sample | Actual specification |
| --- | --- |
| [4090 text video](generated-4090-video.mp4) | 864×480, 24 fps, approximately 5.167 seconds |
| [5070 first-frame video](generated-5070-first-frame.mp4) | 512×288, 24 fps, 1.625 seconds |
| [3080 music](generated-15s-music.flac) | 15 seconds, 48 kHz, stereo FLAC |

[Actual native requests/responses](api-responses.json) omit authentication headers, cookies and idempotency credentials. The temporary test Key is disabled. Recorded task/content IDs belong to that run and expire eight hours after success; they are not permanent download links. The current music model's prompt controls style while lyrics remain fixed in its Workflow.

The [manifest](manifest.json) binds media and source-report hashes. [Media verification](../../evidence/2026-10-28/media-gen-public-media-124.json) records complete image parsing and native FFmpeg decoding. Some published screenshots crop original full-page captures; originals remain in the evidence directory. These records do not accept visual quality, every Workflow, strict device fairness or a 10–20-device fleet.
