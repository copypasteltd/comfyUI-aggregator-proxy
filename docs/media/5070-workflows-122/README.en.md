# Actual 5070 deployment media

[中文](README.md) · [Design and verification](../../122-RTX5070纯HTTP接入与调用验证.md) · [Selected packages and usage](../../examples/5070/README.en.md)

Captured from the deployed Lime Admin on 2026-10-28 using real records: three enabled channels and ten published aliases. No fixture jobs were injected. Private addresses are visually masked; recording starts after login. API declaration forms were edited only in a local draft and restored without saving to production. All owned browser contexts and the browser closed.

![Three actual business videos](generated-workflows.gif)

| Files | Content |
| --- | --- |
| [admin-5070.mp4](admin-5070.mp4) / [GIF](admin-5070.gif) | Original-speed Admin recording and lower-frame-rate preview |
| [overview.png](overview.png) / [mobile](overview-mobile.png) | Actual overview and 390px mobile layout |
| [models.png](models.png), [channels.png](channels.png), [jobs.png](jobs.png), [workflows.png](workflows.png) | Published aliases, channels, real jobs and the three selected workflows |
| [api-model-form.png](api-model-form.png), [api-node-form.png](api-node-form.png) | API-only model options, API version and node classes |
| [Mobile model form](api-model-form-mobile.png), [mobile node form](api-node-form-mobile.png) | 390px single-column layout, keyboard focus and reduced motion |
| [generated-t2v.mp4](generated-t2v.mp4) | Original business API text-to-video delivery |
| [generated-i2v.mp4](generated-i2v.mp4) | Original business API first-frame-video delivery |
| [generated-refs.mp4](generated-refs.mp4) | Original business API two-reference-video delivery |
| [generated-overview.png](generated-overview.png), [generated-workflows.gif](generated-workflows.gif) | Actual extracted frames and side-by-side preview |

Each business video is 512×288, 39 frames at 24 fps, 1.625 seconds, H.264 with AAC. Neutral prompts and [project-owned reference images](../../../app/examples/5070-workflows/) replace private source inputs. MP4 files preserve delivered bytes; images/GIFs are derivatives. [manifest.json](manifest.json) records their separate digests. These short runs verify delivery, not high-resolution or long-form quality. See [browser provenance](../../evidence/2026-10-28/5070-admin-browser-122.json) and [actual delivery](../../evidence/2026-10-28/5070-real-delivery-122.json).

These repository display copies remain after the eight-hour production expiry. ComfyUI originals, HTTP Worker spool, cloud outputs and client downloads follow separate retention rules. Automatic ComfyUI original-file deletion remains the pending design in [121](../../121-ComfyUI原始产物移交即清理设计.md).
