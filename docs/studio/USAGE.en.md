# ComfyUI Creative Canvas

[中文](USAGE.md) · [Assessment and all 46 requirements](06-融合可行性评估与上线约束.md) · [Current progress](10-开发进度与交付记录.md)

A managed React canvas built from basketikun/infinite-canvas v0.20.0, using the existing ComfyUI Aggregator Proxy. [Studio is live](http://43.165.167.179:39443/studio/) with backend 131.1/static 131.9. Eight actual GPU modes have completed on 3080/5070. The full fault matrix, physical phones and 10–20-node capacity remain separately tracked in [all 46 requirements](10-开发进度与交付记录.md).

![Actual public Lime canvas with a generated image](assets/studio/controls-2026-10-29T22-08-02-756Z/desktop-canvas.png)

[Actual samples, eight call receipts and bilingual gallery](media/studio-131/README.en.md) · [Original-speed UI recording](media/studio-131/studio-focused-tour.mp4) · [Mobile canvas](assets/studio/controls-2026-10-29T22-08-02-756Z/mobile-canvas.png)

![Actual public UI demonstration](media/studio-131/studio-focused-tour.gif)

## Architecture and usage

Studio handles composition, references, advertised presets and result branches. The existing API retains business Keys, published Workflows, PostgreSQL jobs, quotas, exclusive GPU ownership and authenticated artifacts. Admin retains channel/Workflow/model/Key management and global/per-channel queues. No second generation queue, resident backend or local Agent is added.

1. Open the high-port HTTP endpoint `http://43.165.167.179:39443/studio/`. Connect using the service origin and an administrator-issued business API Key. Do not place the Key in a URL or use an Admin/Worker credential. Temporary validation Keys are not delivery credentials.
2. Read live model usage, Schema, presets, required references, roles and output types. Key-denied specifications are unavailable. Discovery does not guarantee an idle or healthy GPU.
3. Create a canvas and open New Creation. Select a complete published preset and upload the required source/reference images in order. Audio is a Workflow output rather than an assumed speech endpoint. Each explicit submit creates one job.
4. Before POST, an IndexedDB transaction commits the original body, asset IDs, release digest, identity and idempotency key. Known jobs use GET. If the response is lost, reconcile the original submission; do not change the Key/model/assets to retry.
5. Leave or reload the page and restore with the same connection. Stop Waiting affects browser requests only. Remote cancellation is explicit and the server decides the final state.
6. Verify and load artifacts on demand. Images, video and audio become media nodes; GLB becomes an original-file download card. Continue from an image, save originals or export a local canvas ZIP. Unsupported playback formats remain downloadable without pretending to transcode.
7. Server History reads existing jobs within the current Key's permissions, including from another browser. Offline ZIP imports create independent local copies and convert task cards to notes; import never generates content.
8. Export the task journal separately from the Tasks page. In another browser, connect the original service/Key, choose the JSON, preview every record, then import records only. Existing records remain unchanged. Imported jobs require an explicit original-job query before their status, artifacts or cancellation can be used; records without job IDs cannot submit generation. A saved identity can export while disconnected. Journals omit media and connection Keys but retain original prompts, so keep the file private.

The seven task types cover text-to-image, image editing, multi-reference images, text-to-video, distinct first-frame/multi-reference video models, Workflow audio and image-to-3D. Interactive 3D previews, explicit batches, Key-rotation adoption and physical-phone/large-file acceptance remain separately tracked.

## Retention and identity

ComfyUI output (01), Worker spool (02), cloud artifacts (03) and browser copies (04) are independent. Cloud artifacts expire eight hours after completion. Browser storage is subject to quota and browser/user cleanup; saved originals and exported ZIPs are separate offline copies. Transfer-triggered deletion of 01 is still an upstream service task: deleting ComfyUI history is not proof that files were deleted.

Managed canvas/asset documents and Studio result references share a transaction boundary, including unopened projects and recoverable tasks. Failed saves retain the previous document and show an error; new generation and successful import wait for commits. Export current edits before reloading after a cross-tab conflict. The current v3 browser database preserves v1/v2 records and prevents older clients from bypassing recovery-only behavior; static rollback cannot downgrade it. Ordinary image/file garbage collection, upload-cache cleanup and full quota UI remain pending. [Storage design](12-本机引用事务与保存失败恢复.md) · [131.7 recovery MP4](media/studio-131/studio-storage-recovery-verified.mp4) · [GIF](media/studio-131/studio-storage-recovery-verified.gif), recorded with a synthetic API and injected quota failure, not actual disk exhaustion or GPU execution.

Task journals use an independent format version and SHA-256. Import previews the file, then adds records in one transaction; any conflict or quota error aborts the entire batch. The checksum is not a signature: archived status/revisions require an authenticated server query and do not grant permissions. Export canvas ZIPs/originals separately; this is not WebDAV or cloud sync. [Journal design and limits](13-任务清单备份与只读恢复.md) · [Final demonstration MP4](media/studio-131/studio-task-backup-verified.mp4) · [GIF](media/studio-131/studio-task-backup-verified.gif).

Business fetch uses a fixed origin, Bearer authorization, credentials: omit and rejected redirects. Admin sessions and Worker credentials remain separate. Managed mode rejects arbitrary scripts, plugins and automatic Agent execution. Exports remove credentials by default. Client-side partitioning is not a multi-user security sandbox on a shared computer. Corrupt/unknown journal versions are preserved instead of silently erased.

## Source and native build

The active workspace is `无限画布/`. Canonical frontend: `infinite-canvas/web/`; backend feature checkout: `aggregator-service/`. Reviewed frontend source/locks are mirrored to `aggregator-service/app/apps/studio_web/` and documentation/evidence to its `docs/studio/`. Private tools, databases and credentials stay in ignored `.local/`. The original sibling aggregator checkout is read-only.

Use native Node.js 22.14.0 / npm 10.9.2:

```powershell
cd infinite-canvas/web
npm ci
$env:VITE_STUDIO_MODE='managed'
$env:VITE_BASE='/studio/'
npm run build
```

Serve dist at `/studio/` with a correct SPA fallback, MIME types and Worker path. Main-document strict CSP and the isolated Schema Worker's compilation policy are separate. Keep `/admin/` and `/v1/` on the existing service. No Docker, WSL or VM is used. The user manages domain reverse proxying; HTTP on the existing high port and any-origin CORS remain supported with business authentication.

See current progress for F-01–F-46 and G-01–G-27, failures and rollback. Backend runtime is unchanged from the 82 scoped/1,456 compatibility checks. Current frontend verification covers 126 units, type/build, 18 UI groups, 22 storage groups and 25 task-journal groups. Public upgrades and original-SHA restoration are separate evidence. Eight GPU modes mean one deployed-UI submission and seven API submissions, not all eleven aliases retested. Audio file/browser duration is 30,000ms versus 29,952ms decoder-progress metadata: a recorded 48ms difference within the existing 100ms validator tolerance; exact metering remains follow-up.

Actual backend/static rollback preserves existing jobs/content. A new 5070 ten-minute idle cycle completed with both CPU barriers. Builds, browser tests, GPU media, phones and fleet capacity remain separate evidence. Non-crash test-database settings do not change production durability or prove power-loss recovery.

## Attribution

Upstream [basketikun/infinite-canvas](https://github.com/basketikun/infinite-canvas), MIT, commit `9cd3deacbcc598361807afc16c059a80dd18b6f9`. Preserve its license and author attribution. The managed UI uses the user's required [04 · Lime style](https://html.cafe/x5b381ea1); original CSS, font declarations, hashes and font licenses are preserved in web/src/styles/reference/ 和 web/public/fonts/, with separate adaptations.

## Read-only submission recovery (132 candidate)

After a lost response or journal import, `POST /v1/jobs/lookup` matches the original business Key, frozen body, idempotency key and release digest to restore the original job ID. It requires only `jobs:read` and never generates, uploads or charges again. A not-observed result stays unknown; older services fail clearly without falling back to creation. An IDB write transaction also claims the first send across pages.

[Operation and development contract](14-原提交只读核对与恢复.md) · [Original-speed MP4](media/studio-131/studio-submission-lookup.mp4) · [390px view](assets/studio/submission-lookup-2026-10-30T00-27-20-776Z/mobile-original-job-restored.png). This recording uses the production build with synthetic HTTP, not a GPU run or physical phone.

![Read-only original submission recovery](media/studio-131/studio-submission-lookup.gif)
