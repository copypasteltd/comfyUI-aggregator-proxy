# ComfyUI Creative Canvas

[中文](README.md) · [Usage and deployment](docs/studio/21-独立画布部署与使用.md) · [Current scope and limits](docs/studio/37-交付范围纠正与核心适配现状.md) · [Documentation](docs/studio/README.md) · [Release notes](docs/studio/23-独立画布更新记录.md)

An independent Web canvas adapted from [basketikun/infinite-canvas](https://github.com/basketikun/infinite-canvas). It reuses the existing pan/zoom, nodes, connections and projects, and connects them to the existing ComfyUI Aggregator models, reference inputs, asynchronous jobs and artifacts. The browser calls that API directly; no new generation backend, queue or GPU proxy is required.

Static **154.1** is deployed. The 45 published models/50 presets and usage 1.0/1.1 named media, workflow text and compatible-parent contract are adapted. Following device recovery, five selected real modes pass generation and canvas retrieval: text-to-image, named-reference editing, workflow music, first-frame video and two-reference video. Earlier failures remain recorded. See [recovery and real acceptance](docs/studio/39-设备恢复与真实生成补充验收.md) and [implementation details](docs/studio/38-现有API新版输入适配与整轮验收.md).

| Canvas | Existing business API | Management |
| --- | --- | --- |
| [Open canvas](http://43.165.167.179:39445/studio/) | `http://43.165.167.179:39443` | [Existing Admin](http://43.165.167.179:39443/admin/) |

## Design and capabilities

```mermaid
flowchart LR
    W[Existing canvas + frontend adapter] -->|Business Key / existing v1| A[Original Aggregator API]
    A --> G[Original queue / Worker / ComfyUI]
    A -->|Status and authenticated content| W
    W --> L[Browser-local projects / media / task records]
```

| Capability | Behavior |
| --- | --- |
| Discovery | Read the Key-filtered descriptions, Schema, complete presets and reference slots; never infer parameters from a model name |
| Images and references | Adapt text-to-image, image editing and multiple-reference workflows; selected canvas images can populate published slots |
| Video | Use the separately published text, first-frame and multiple-reference workflows; do not silently discard required references |
| Audio and 3D | Audio is a ComfyUI Workflow output; GLB has a result card, on-demand preview and original download. Workflow audio is not assumed to be TTS |
| Asynchronous jobs | Upload → explicit submission → query the original job → load/save artifacts; restore existing records instead of blindly creating replacement jobs |
| Projects and media | Reuse canvas editing and projects; retain local persistence, project ZIP/task exports and existing reference protections |
| Lime themes and examples | Reuse the downloaded 04·Lime style, persist light/dark preference and offer eight editable examples with no generation before confirmation |

Business requests use a Bearer Key and `credentials: omit`, without an Admin session. Only the existing `/v1/` API is called. Forwarding, scheduling, Workers, databases and Admin remain unchanged. Management stays in the original Admin. Optional assistants, cloud sync, new formats and fleet capacity work do not automatically become canvas tasks.

## How to use

1. Open [Connection](http://43.165.167.179:39445/studio/config). Enter the **original API origin** and a **business API Key** supplied by its administrator. Do not enter the canvas port, Admin path or ComfyUI 8188. The Key stays in page memory by default; remembering it in a trusted browser is optional.
2. Read [Models and usage](http://43.165.167.179:39445/studio/models), then choose a permitted model and complete preset. Dimensions and duration must follow the published preset. Catalog visibility does not establish an idle device or sufficient quota.
3. Create/open a canvas, or open an editable project from [Examples](http://43.165.167.179:39445/studio/library). Examples can be viewed without a Key and do not upload or submit merely by opening.
4. Select images and choose “新建创作”. Review the prompt, model, complete preset and reference order/roles, then explicitly click “提交1项生成任务”. Named inputs can accept declared image/video/audio/GLB files. Fill workflow text fields explicitly; continuation verifies an original parent job. Do not add a prompt when its Schema has none.
5. Query the original task from its node or task page. Continue editing or restore the record later. Cancellation needs server confirmation. On success, click “载入产物” to fetch and verify authenticated content, then play, reuse or save the original.
6. Save the project and export important originals or a project ZIP containing its files. Task JSON helps reconcile original jobs; it does not replace an artwork backup. Business permissions are configured in the original Admin.

Cloud artifacts normally expire **eight hours after completion**, as stated by API metadata; downloaded local copies are independent. Projects and media primarily live in this browser, not cloud sync. A new domain, port or browser profile does not inherit them: export/import and verify the new copies first. Preserve journal v5 and do not clear or downgrade databases.

[Usage documentation](docs/studio/21-独立画布部署与使用.md) describes the exact existing endpoints, saving, recovery and deployment. [Canvas creation](docs/studio/35-画布参考创作与生成表单.md) covers references and result placement.

## Named workflow inputs

Usage 1.0 and 1.1 are supported. Follow `input_slots` for exact role, file kind, MIME and size; uploads keep the kind with their original idempotency key. `text_slots` expose dialogue, transcripts, lyrics, scores or other published fields. A continuation passes only a successful, compatible, unexpired parent job ID owned by the connected Key. The API performs final media and admission validation. [Implementation and verification status](docs/studio/38-现有API新版输入适配与整轮验收.md).

## Pages and recordings

| Named image input · light | Named image input · dark |
| --- | --- |
| ![Named input light](docs/studio/media/studio-154/synthetic-chromium-2026-11-05T22-06-19-914Z/named-edit-light.png) | ![Named input dark](docs/studio/media/studio-154/synthetic-chromium-2026-11-05T22-06-19-914Z/named-edit-dark.png) |

![Named-input and result UI demonstration](docs/studio/media/studio-154/named-inputs.gif)

[Complete page MP4](docs/studio/media/studio-154/named-inputs.mp4) · The demonstration uses synthetic API/media, not GPU generation.


| Light | Dark |
| --- | --- |
| ![Light examples page](docs/studio/media/studio-151/gallery-public-chromium-2026-10-30T20-57-06-617Z/examples-light-desktop.png) | ![Dark examples page](docs/studio/media/studio-151/gallery-public-chromium-2026-10-30T20-57-06-617Z/examples-dark-desktop.png) |

[Historical theme/example GIF](docs/studio/media/studio-151/themes-and-examples-public.gif)

[Complete MP4](docs/studio/media/studio-151/themes-and-examples-public.mp4) · [Theme/example details, 3D and 390px views](docs/studio/36-明暗主题与预设创作案例.md) · [Historical real-media gallery](docs/studio/media/studio-131/README.md). The theme/example recording uses a synthetic API and does not prove GPU execution. The historical gallery separates actual API generation from browser retrieval. It is not a fresh all-model run on the current version; the recent multiple-reference video preparation failure remains recorded.

## Native development and static deployment

From the independent `infinite-canvas` Git repository root, use native Node/npm (verified with Node 22.14.0/npm 10.9.2):

```powershell
Set-Location web
npm ci
$env:VITE_BASE='/studio/'
$env:VITE_STUDIO_MODE='managed'
$env:VITE_AGGREGATOR_ORIGIN='http://43.165.167.179:39443'
npm run dev
```

Open `http://localhost:3000/studio/`. Keep these variables for production, run `npm run typecheck` and `npm run build`, and deploy only `web/dist/`. The default API value contains a public origin, never a Key. Omit it to let users enter their own origin.

`managed` selects the existing Aggregator integration and disables upstream custom scripts, plugins and local Agents; it still deploys independently. Local Docker, WSL and virtual machines are prohibited. Keep the existing high HTTP port; the user handles domain proxying. No HTTPS gate is added and the API's existing any-origin CORS is unchanged. Static updates switch only the canvas directory, preserve data/older-page assets and do not restart the API. See [deployment instructions](docs/studio/21-独立画布部署与使用.md), including compatible-recovery limitations.

## Fresh real-generation examples

| Text-to-image | Named-reference editing |
| --- | --- |
| ![Real text-to-image](docs/studio/media/studio-155/text-image.png) | ![Real named-reference edit](docs/studio/media/studio-155/named-edit.png) |

![Real video retrieval and reload](docs/studio/media/studio-155/video-recovery.gif)

[Original first-frame video](docs/studio/media/studio-155/video-first-frame.mp4) · [Original two-reference video](docs/studio/media/studio-155/video-two-references.mp4) · [Original workflow music](docs/studio/media/studio-155/named-music.flac) · [Complete generation page MP4](docs/studio/media/studio-155/image-audio-generation.mp4) · [Generation page GIF](docs/studio/media/studio-155/image-audio-generation.gif). The retrieval recording uses the original successful jobs, with no new generation submissions. [Document 39](docs/studio/39-设备恢复与真实生成补充验收.md) states the exact evidence scope.

## Verification and current limits

- The unchanged 154.1 source has 279 passed units, type checking and production build; 45 models/50 presets and eight legacy examples were checked. The stronger Chromium/Firefox runs each complete 12 light/dark form submissions and returned artifacts, verifying every committed Blob's length, MIME and SHA. Mixed named-video/GLB-input and continuation combinations remain explicitly synthetic fixtures.
- Static 154.1, journal v5, 04·Lime and original API sources/cloud configuration are retained. The user authorised one normal 3080 host restart; existing Admin operations restore 39 approved 3080 bindings and 3 approved 5070 bindings. No new model publication or scheduler change is implied.
- Five real modes pass generation, rendered-canvas delivery, independent download and original-profile reload. Earlier expired/failed jobs remain unchanged. The old unknown execution closes safely with execution_host_restarted; that is not a successful old generation. See document 39.
- This is not an execution run of all 45 models or a 10–20-node capacity result. Unpublished named-video/continuation modes still need an actual original-API release. Windows WebKit diagnostics failed; actual Safari and physical touch remain untested. The 390px checks use viewport emulation. These validation boundaries do not create additional frontend features.


[Core checklist and specific issues](docs/studio/37-交付范围纠正与核心适配现状.md) · [Current progress](docs/studio/10-开发进度与交付记录.md) · [Historical delivery details](docs/studio/history/10-历史交付与46项评估.md). Follow-up work addresses explicit requirements and actual defects. Documentation and release notes stay synchronized on the authorized `feat/independent-canvas` branch; no upstream, main or original API branch push.

Original authorship and the [MIT license](LICENSE) are retained. Downloaded 04·Lime reference styles/fonts and source notices remain with static delivery.
