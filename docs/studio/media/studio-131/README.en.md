# 131 · Actual Studio, samples and call receipts

[中文](README.md) · [Public Studio](http://43.165.167.179:39443/studio/) · [All 46 requirements](../../10-开发进度与交付记录.md) · [Usage and deployment](../../11-线上使用与原生发布运维.md)

Current deployment: static 136.1/backend 132.2. The eight GPU modes and original tour were recorded on 131.6. Text-to-image was submitted through the deployed canvas; seven other jobs used the same published API through a native client. A real browser then loaded each original from server history into its library, decoded images, played video/audio, and downloaded all eight originals with matching byte counts and SHA. That historical GLB evidence covered its download card. Static 134.1 adds interactive viewing and cleanup with separate evidence below.

131.9 adds a [task-journal MP4](studio-task-backup-verified.mp4), [GIF](studio-task-backup-verified.gif) and [source/hash manifest](studio-task-backup-verified-manifest.json). Actual production UI covers export, preview/confirmation, explicit original-job query, corrupt-file/quota failures and offline export. All 25 groups pass with zero business writes, using a synthetic API and a 390px emulated viewport, not GPU execution or a physical phone. Screenshots: [desktop preview](../../assets/studio/task-backup-2026-10-29T23-35-14-126Z/desktop-import-preview.png), [mobile preview](../../assets/studio/task-backup-2026-10-29T23-35-14-126Z/mobile-import-preview.png), [pending verification](../../assets/studio/task-backup-2026-10-29T23-35-14-126Z/mobile-restored-records.png), [original job confirmed](../../assets/studio/task-backup-2026-10-29T23-35-14-126Z/desktop-known-job-queried.png), [invalid backup](../../assets/studio/task-backup-2026-10-29T23-35-14-126Z/desktop-invalid-backup.png), [quota failure](../../assets/studio/task-backup-2026-10-29T23-35-14-126Z/desktop-import-quota-failure.png). Journal JSON and artwork ZIP must be backed up separately; [behavior and limits](../../13-任务清单备份与只读恢复.md). Earlier studio-task-backup files record intermediate 131.8 behavior; use the verified recording for the current version.

131.7 adds a [save-failure/retry MP4](studio-storage-recovery-verified.mp4), [GIF](studio-storage-recovery-verified.gif) and [source/hash manifest](studio-storage-recovery-verified-manifest.json). This records the local production UI with a synthetic API, injected quota failure, phone viewport and two-tab conflict; it is not actual disk exhaustion, GPU execution or a physical phone. Screenshots: [390px unsaved warning](../../assets/studio/storage-2026-10-29T23-00-07-628Z/mobile-save-failed.png), [after retry](../../assets/studio/storage-2026-10-29T23-00-07-628Z/mobile-saved-after-retry.png), [cross-tab conflict](../../assets/studio/storage-2026-10-29T23-00-07-628Z/desktop-cross-tab-conflict.png).

| ID | Mode / public model | Actual original | Published sample |
| --- | --- | --- | --- |
| M-01 | Text image / comfy-3080-image-lustify-krea2 | 1024×1024 PNG; 965,536 bytes | [Image](image.png) |
| M-02 | Image edit / comfy-3080-image-edit-wai | 720×1080 PNG; 722,985 bytes | [Image](edit.png) |
| M-03 | Two-reference image / comfy-3080-image-refs-wai | 720×1080 PNG; 1,001,038 bytes | [Image](references-image.png) |
| M-04 | Text video / comfy-5070-video-h3 | 512×288, 1.625s MP4; 59,009 bytes | [Video](text-video.mp4) |
| M-05 | First-frame video / comfy-5070-video-h3-i2v | 512×288, 1.625s MP4; 60,266 bytes | [Video](first-frame-video.mp4) |
| M-06 | Two-reference video / comfy-5070-video-h3-refs | Two distinct slots; 512×288, 1.625s MP4; 66,156 bytes | [Video](references-video.mp4) |
| M-07 | Workflow audio / comfy-3080-music-yue2 | 48kHz stereo FLAC; file/browser duration 30s; 2,233,080 bytes | [Audio](audio.flac) |
| M-08 | Image-to-3D / comfy-3080-3d-trellis2 | 76,942,864-byte GLB; 1,597,077 vertices, 3,217,656 triangles, no textures | [Download card](../../assets/studio/library-2026-10-29T22-17-34-870Z/glb-delivery-card.png); full digest in manifest |

Public samples remove embedded Workflow/container metadata. Their byte counts and hashes differ from the actual downloaded originals; both are recorded in the [media manifest](media-manifest.json). PNG pixels are unchanged; audio/video streams were copied without re-encoding. The large GLB remains in ignored private local storage rather than Git, with its metadata, geometry probe and verified full browser download receipt published. These are this task's own test robot/music assets, not customer content.

Audio metadata reports 29,952ms; the independent file probe and browser report 30,000ms. The recorded 48ms difference is within the existing 100ms audio-validator tolerance; exact millisecond metering remains follow-up. An instrumental-style prompt does not override lyrics embedded in a frozen Workflow. This is not a TTS endpoint.

## Pages and recordings

![Current public desktop canvas](../../assets/studio/controls-2026-10-29T22-08-02-756Z/desktop-canvas.png)

![Actual public UI, original-speed excerpt](studio-focused-tour.gif)

[Original-speed MP4](studio-focused-tour.mp4) · [Recording manifest](studio-focused-tour-manifest.json) · [Mobile canvas](../../assets/studio/controls-2026-10-29T22-08-02-756Z/mobile-canvas.png) · [Landscape](../../assets/studio/controls-2026-10-29T22-08-02-756Z/landscape-canvas.png) · [Two-reference form](../../assets/studio/controls-2026-10-29T22-08-02-756Z/mobile-multi-reference-composer.png) · [Library](../../assets/studio/library-2026-10-29T22-17-34-870Z/desktop-library.png) · [Mobile library](../../assets/studio/library-2026-10-29T22-17-34-870Z/mobile-library.png)

The recording shows actual UI activity without the Key configuration screen. It is not a complete GPU-wait recording. Mobile screenshots use Chromium's 390px viewport, not physical Android/iOS acceptance. Older studio-tour files retain their historical package evidence; current demonstrations use studio-focused-tour.

## Calls and boundaries

[Eight actual request/response receipts](api-calls.json) include bodies, release digests, original-body SHA, job IDs and content metadata. Authorization is omitted and idempotency tokens are hashed. Expiring test asset IDs and disabled test Keys make these review receipts, not permanent executable examples. With your own business Key, discover current /models, /capabilities and usage, upload your own references and follow that release's Schema/presets/slots. Reconcile an uncertain original submission without changing its Key or assets.

[GPU originals](../../research/studio-implementation/live-media-131.json) · [Nine browser checks](../../research/studio-implementation/live-library-2026-10-29T22-17-34-870Z.json) · [Four current UI checks](../../research/studio-implementation/live-controls-2026-10-29T22-08-02-756Z.json) · [API checks and temporary Key revocation](../../research/studio-implementation/live-api-131.json) · [Actual upgrade/browser restart](../../research/studio-implementation/live-upgrade-2026-10-29T21-30-26-230Z.json)

Browser private-byte samples rose from about 347 to 651MiB, and JS heap from 8.2 to 17.1MiB, while loading all originals including the 76.94MB GLB. These are observations, not peak-memory, leak or phone-capacity acceptance; summed working sets may double-count shared pages. All owned browser objects closed in finally. Both temporary business Keys are disabled and return 401.

All eight cloud artifacts expire 28,800s after job completion; local originals and saved files are independent. This evidence does not certify every one of the 11 aliases, all codecs/faults, transfer-triggered ComfyUI cleanup, physical phones, global fair pooling or 10–20-node capacity. The complete backlog retains its F identifiers.

## 132.2 deployed: read-only submission lookup

Original-speed production UI with synthetic HTTP; not GPU or physical-phone evidence. Covers no-ID recovery, not-observed results and original job restoration without replay.

[MP4](studio-submission-lookup.mp4) · [GIF](studio-submission-lookup.gif) · [SHA/decoder manifest](studio-submission-lookup-manifest.json) · [390px screenshot](../../assets/studio/submission-lookup-2026-10-30T00-27-20-776Z/mobile-original-job-restored.png)

## Batch creation and partial recovery

[Original-speed MP4](studio-batch.mp4), [GIF](studio-batch.gif) and [hash/decode manifest](studio-batch-manifest.json) show the production UI with synthetic HTTP and native IndexedDB: three members, lost second response, restart, continue only the pending member, original-job cards and v2 read-only import. Desktop/390px emulation; no new GPU or physical-phone acceptance. [Contract](../../15-批量创作与部分失败恢复.md).

## Interactive GLB preview and cleanup

[Original-speed MP4](studio-model3d.mp4), [GIF](studio-model3d.gif) and [decode/hash manifest](studio-model3d-manifest.json) cover an existing real GLB, synthetic textures/errors, cleanup and desktop/390px emulation. Static 134.1 has 17 GLB groups plus four actual public HTTP/CSP checks, with no new GPU generation. Screenshots: [real 76.94MB GLB](../../assets/studio/model3d-2026-10-30T02-24-32-253Z/desktop-real-comfyui-glb.png), [touch emulation](../../assets/studio/model3d-2026-10-30T02-24-32-253Z/mobile-touch-glb.png), [public texture fixture](../../assets/studio/public-model3d-2026-10-30T02-27-32-469Z/public-offline-textured-glb.png). [Usage, compatibility and limits](../../16-GLB交互预览与资源释放.md).

## Local usage and reference-cache cleanup

[MP4](studio-upload-cache.mp4) · [GIF](studio-upload-cache.gif) · [SHA / full-decode manifest](studio-upload-cache-manifest.json) · [Desktop](../../assets/studio/upload-cache-2026-10-30T03-06-13-206Z/desktop-cache-controls.png) · [390px confirmation](../../assets/studio/upload-cache-2026-10-30T03-06-13-206Z/mobile-cache-confirmation.png) · [Actual public page](../../assets/studio/public-storage-space-2026-10-30T03-11-34-336Z/public-storage-desktop.png).

135.1 adds three-database payload/site-quota displays, identity-scoped confirmed cache removal, reference/two-page/late-response protection and button-hover contrast. All 28 groups pass. Recording uses production UI with synthetic API and native IndexedDB, not GPU execution, a physical phone or actual disk exhaustion. Four public no-Key page checks pass separately. Ordinary file collection and uncertain uploads remain pending.

MP4: 537,769 bytes, SHA-256 `b515bdafdb54822ef21457821e5fa7e5a7247487824d0ee0e0547b9a68d00111`. GIF: 5,207,650 bytes, SHA-256 `28c65e54aeb40051ee6f439bf223784c4d88eda96613e0d0bc55f036502974fd`.

## Key rotation and original-task access

[Original-speed MP4](studio-task-access.mp4) · [GIF](studio-task-access.gif) · [Hashes / full decode](studio-task-access-manifest.json) · [Original canvas](../../assets/studio/task-access-2026-10-30T04-05-17-889Z/desktop-original-canvas.png) · [390px](../../assets/studio/task-access-2026-10-30T04-05-17-889Z/mobile-confirmation.png) · [Actual public route](../../assets/studio/public-storage-space-2026-10-30T04-18-31-346Z/public-task-access-desktop.png).

136.1 adds 31 native-browser groups. The recording shows explicit GET confirmation, unchanged submission, original canvas, new-identity download, reload and old-identity backup preview using a synthetic API and native IndexedDB. Actual permissions/GPU receipts are separate. This is not a phone or GPU-generation recording. MP4: 490,050 bytes; GIF: 5,237,892 bytes. Both fully decode and bind to the source report.

The [new actual 720×1080 image](key-rotation-1361.png) completed once and was downloaded through the rotated credential. [Delivery, request and decode receipt](../../research/studio-implementation/key-rotation-delivery-1361.json) records 747,994 bytes and SHA. End-to-end time was **1,483 seconds (24m43s)** for this single sample; queue/loading/execution were not separated. This proves delivery, and does not establish a latency or throughput target. [Five disabled test Keys / six rejected credentials](../../research/studio-implementation/validation-key-closure-1361.json) are checked separately.
