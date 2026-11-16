# Direct ComfyUI operation

Use this mode when the user asks you to operate ComfyUI itself or run a custom Workflow on an authorized device. The gateway supports HTTP, WebSocket, node/model discovery, personal workflows, scoped queue/history, uploads and owned downloads. `comfy_origin.py` provides standard-library HTTP automation and bounded polling; the original browser panel provides live WebSocket/binary previews. No local ComfyUI installation is needed.

## Authorization and session

An administrator enables **API Keys → Native panel**, selects devices and saves the independent grant. The Key's existing channel restrictions also apply. Business scopes or unlimited quotas alone do not grant native access. Do not change another Key's grants or use Admin/Worker credentials to bypass a 403.

Set `MEDIA_GEN_API_KEY` to the provided business Key. Both clients default to the business domain **https://gen.sub2share.xyz**; optionally set `MEDIA_GEN_BASE_URL` to that same origin without a path. The native login is `https://gen.sub2share.xyz/origin/`, its device directory is `/origin/list`, and all `/origin/{slug}/` operations remain on this domain. Do not use server IPs or internal ComfyUI addresses in client examples. Examples below assume a private Key is configured:

```bash
python scripts/comfy_origin.py login
python scripts/comfy_origin.py devices
```

Login POSTs the Key to `/origin/api/session`, receives an HttpOnly Cookie and CSRF value, and stores only the derived session, origin, credential fingerprint and expiry. The default `.comfy-origin-session.json` is **sensitive**. Top-level `--session-file` selects another path. Never print, commit or distribute it. POSIX files are created with mode 0600; use a private directory with appropriate inherited Windows access permissions. Do not copy it into a browser, another origin, shared example or Skill package. Session expiry, original Key rotation/revocation and grant changes are enforced by the service.

The helper prints no Cookie, CSRF or Key. Other HTTP tools may use their own protected Cookie container. Native calls use derived session/CSRF; a business Bearer header alone is insufficient. Browser login through `/origin/` is separate. Native UI pages use `/origin/{slug}/`; never substitute an internal ComfyUI IP.

Sessions and saved submissions are bound to their original origin. When moving from an old address, choose a new private `--session-file` and log in on the business domain. Keep unresolved requests with the original address and identity until reconciled. Do not rewrite their base URL, transfer Cookies across origins or create a duplicate generation during this address change.

## Device selection and exclusive operation

Choose a `slug` from `devices.data`, considering `online`, `queue_running`, `queue_pending`, `busy` and `reservation_state`. An incomplete observation has unknown counts, not an empty queue. Names and availability can change.

```bash
python scripts/comfy_origin.py acquire DEVICE_SLUG
python scripts/comfy_origin.py get DEVICE_SLUG queue
```

`acquire` returns an editor URL and `state=active` after persistent resource locking and source-queue checks. Do not proceed on an error. The reservation spans shared physical GPU/ComfyUI resources and prevents new ordinary API dispatch and idle reclamation. It does not preempt tasks or maintenance. Sessions of the **same logical Key** share its ownership; use different Keys for isolated users.

For browser operation, open the returned URL on the same origin using that browser's own login. Follow the browser tool's instructions and close every owned, unused tab/context/browser in `finally`. The original editor can convert a UI graph when submitting; its saved UI JSON differs from an API-format graph.

## Inspect and prepare a Workflow

```bash
python scripts/comfy_origin.py get DEVICE_SLUG object_info/CheckpointLoaderSimple
python scripts/comfy_origin.py get DEVICE_SLUG object_info/EmptyLatentImage
python scripts/comfy_origin.py get DEVICE_SLUG models
python scripts/comfy_origin.py get DEVICE_SLUG models/checkpoints
python scripts/comfy_origin.py get DEVICE_SLUG embeddings
```

`object_info/{class_type}` declares actual inputs, required/optional fields, types, bounds, choices and output sockets. `models` lists folder categories; `models/{folder}` lists installed filenames. If a source version lacks a folder endpoint, use the loader node's choices in `object_info`. Model weights, custom nodes and graph connections must exist on this device. These are source filenames, not `/v1/models` aliases or approval of missing dependencies.

Use the user's **API-format export** or construct nodes of the form `"node_id": {"class_type": "InstalledNode", "inputs": {...}}`. A connection is `["source_node_id", output_index]`. Check classes/inputs against source schemas and connect a supported output/saver node. Preserve the original and edit a separate local JSON. A top-level `nodes`/`links` document is a UI export: use the editor's API export/conversion rather than guessing widget positions.

You may edit prompt/negative prompt, seed, installed checkpoint/LoRA, width/height, frame count, FPS, audio and mesh fields that the actual node exposes. This is graph editing, not a universal `size`/`seconds` API. Use 720×1080 only if every relevant node accepts it and the device has capacity; video alignment and memory requirements differ. Change real node fields instead of inventing generic parameters. Do not install nodes, download weights or silently substitute another model to fix missing dependencies.

For a small connectivity diagnostic only, save this as `api-workflow.json`:

```json
{
  "1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 13825340}},
  "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "origin/skill-diagnostic"}}
}
```

It creates a real CPU solid image, uses no trained model and does not validate model quality. Run it only when a diagnostic is relevant to the authorized task.

## Upload images, video, audio and 3D inputs

```bash
python scripts/comfy_origin.py upload DEVICE_SLUG reference.png
python scripts/comfy_origin.py upload DEVICE_SLUG source.wav
python scripts/comfy_origin.py upload DEVICE_SLUG coarse.glb
```

Native multipart uses the field **image** even for other supported formats, through `/upload/image`. It does not use `/v1/assets` or asset IDs. The helper streams 64 KiB chunks with a known length, at most 127 MiB. Allowed extensions are PNG/JPEG/WebP/GIF/BMP/TIFF, MP4/WebM/MOV/MKV, WAV/MP3/FLAC/OGG/M4A and GLB/OBJ/PLY. An installed loader must support the file; upload acceptance alone does not prove decoding compatibility.

The server assigns a random name in the Key's input namespace and disables overwrite. The helper returns native `name`, `subfolder`, `type` plus a normalized `descriptor`. For standard `LoadImage`-style nodes, use `subfolder/name` from the receipt. Other loaders may declare different input types; inspect their schema. A local PC path, cloud asset ID or absolute Windows source path is not a substitute.

For an actual ComfyUI mask, save the **owned original's descriptor** as `original.json`:

```bash
python scripts/comfy_origin.py upload DEVICE_SLUG mask.png --original-ref original.json
```

This calls `/upload/mask` with `original_ref`. The original must belong to the same Key/device. Apply the returned descriptor as required by the mask loader. A lost upload receipt is not proof that no source file was created.

## Submit, recover, inspect and download

```bash
python scripts/comfy_origin.py submit DEVICE_SLUG api-workflow.json --state-file ./private/native-request.json
python scripts/comfy_origin.py get DEVICE_SLUG queue
python scripts/comfy_origin.py wait ./private/native-request.json --timeout 900 --output-dir ./native-output
```

`submit` accepts a bare API graph or `{ "prompt": { ... }, "client_id": "...", "extra_data": { ... } }`. It validates basic shape, creates a stable client ID and exclusively saves an intent **before** POSTing `/origin/{slug}/prompt`. At most 3,000 nodes and an 8 MiB body are allowed. State contains exact body, body hash, device, origin, credential fingerprint, Idempotency-Key and later `prompt_id`; no Key/session, but potentially private prompts. Keep it outside Git and do not edit during recovery. The server rewrites client IDs and does not delegate queue priority or source secret overrides.

If a response is lost:

```bash
python scripts/comfy_origin.py resume ./private/native-request.json
```

For an unknown ID, resume uses the **original** intent/body/fingerprint once. The server returns a stored receipt or unknown/reconciliation error; it never resends an existing uncertain intent to ComfyUI. Once an ID is known, resume only queries its history. Never delete state, create a fresh file, change the Idempotency-Key or switch devices to work around an unknown 409. A fresh session from the original Key can recover that intent; rotated credentials may read a known owned prompt, but the helper refuses a different fingerprint for an unknown POST. Expired/closing reservations may require server reconciliation before replay can return a receipt.

`wait` polls the owned prompt with a total timeout and refreshes the directory every minute to maintain reservation heartbeat. It recognizes actual history success/error. Timeout does not cancel work or return the device; continue waiting on the same state. Transport/auth errors do not trigger infinite retries. For 401/403 fix session/grants; for 409 inspect the code/reservation; for 400 inspect node validation/history. Do not broaden permissions or silently rerun a failed graph.

Use `get DEVICE_SLUG history/PROMPT_ID` for manual inspection. History is keyed by prompt ID. Read actual status/outputs. File nodes can return image, GIF/video, audio, GLB/OBJ/PLY or other files. The helper collects and deduplicates safe `{ "filename", "subfolder", "type" }` descriptors across output nodes and downloads them when `--output-dir` is supplied. External URLs, raw device paths and metadata-only outputs are not file descriptors. Plugins that omit a usable descriptor need separate adaptation; not every custom output is certified.

```bash
python scripts/comfy_origin.py download DEVICE_SLUG output-descriptor.json --output ./result.glb
```

Downloads use same-origin authenticated `/view`, refuse redirects/traversal/overwrite, cap each at 2 GiB/30 minutes and remove only their own temporary files on failure. Automatic names use the prompt ID/index and safe extension. Results include local bytes/SHA-256 and whether Content-Length was available. Native history supplies **no authoritative cloud SHA-256**: `source_checksum_verified=false` unless a known independent `--sha256` is provided. A local hash is not independent verification. Direct HTTP tools can also use authenticated HEAD/Range.

## Source and personal workflows

```bash
python scripts/comfy_origin.py save-workflow DEVICE_SLUG draft.json ui-workflow.json
python scripts/comfy_origin.py get DEVICE_SLUG "userdata?dir=workflows&recurse=true"
python scripts/comfy_origin.py get DEVICE_SLUG userdata/workflows/@personal/draft.json
python scripts/comfy_origin.py delete-workflow DEVICE_SLUG draft.json
```

Personal saved workflows may be UI JSON; saving does not execute them. Storage is scoped to Key/device. Save refuses overwrite unless `--overwrite` is explicitly supplied. Delete only files whose removal the task authorizes. Combined personal workflow/settings text is limited to 8 MiB. Read settings using `get DEVICE_SLUG settings`; the original panel supports its scoped settings writes.

The original panel's Workflows tree and `userdata?dir=workflows&recurse=true` include **all live source files in their original folders**, plus the separate `@personal` private mount. Source files are shared with the device's direct panel and other trusted operators. A missing model/custom node does not hide a saved Workflow. Check dependencies before execution; do not assume every file is a published API model. v1/v2 directory endpoints support Unicode names, nested folders and metadata; source text is read on demand, without a cloud directory snapshot. Reads are limited to 10,000 files / 4 MiB metadata and 4 MiB per text file. Unreachable sources produce an explicit error.

Read a source graph using `get DEVICE_SLUG userdata/workflows/FOLDER/NAME.json`. The panel also opens Markdown/CSV/text documents. Your browser workspace, autosave and settings remain private. Existing private workflows are retained under `@personal`; this is not a copy of the source catalog. If a real source folder is named `@personal` or `@source`, it is exposed under `@source/@personal/` or `@source/@source/` respectively.

When the task authorizes modifying a shared source file, acquire the device first, then use a **new private operation file for each intended modification**:

```bash
python scripts/comfy_origin.py save-workflow DEVICE_SLUG FOLDER/draft.json ui-workflow.json --shared --operation-file ./private/save.json
python scripts/comfy_origin.py move-workflow DEVICE_SLUG FOLDER/draft.json FOLDER/renamed.json --operation-file ./private/move.json
python scripts/comfy_origin.py delete-workflow DEVICE_SLUG FOLDER/renamed.json --shared --operation-file ./private/delete.json
```

Source save/move refuses overwrite by default; add `--overwrite` only for an authorized replacement. Saving does not execute a graph. The helper persists the exact operation, hash, original Key fingerprint and Idempotency-Key before sending it; keep these files private because they can contain workflow prompts. If the response is lost, run `resume-workflow ./private/save.json` (or the original move/delete intent). Reuse the original identity and body: the server replays a known receipt or observes the file to reconcile an unknown outcome, and never blindly repeats an uncertain source write. An unresolved 409 is not permission to create a fresh operation file. A completed local intent returns its saved receipt without another write.

The browser automatically supplies CSRF and modification identities. Source saves, save-as, file renames and deletion modify the **actual source device** and require active exclusive ownership, native grant and audit. Cross shared/private moves are refused; read and Save As explicitly instead. Only UTF-8 JSON/Markdown/CSV/text files within the source workflows directory may be modified; OS files, model weights and installation APIs are outside this feature. Workflows in the private mount can still be read/saved if the source is offline.

## Explicit cancellation

When the user requests cancellation, `cancel DEVICE_SLUG PROMPT_ID` removes that owned **pending** prompt; it cannot stop a running one. `interrupt DEVICE_SLUG` requests interruption of this Key's current running work. The gateway refuses interruption while another source/Key owns a running task. Interruption is not stopped proof: inspect history/queue and retain ownership until reconciled. Never clear global history or call Manager, `/free`, shell or host reboot.

## Return the device and end the session

```bash
python scripts/comfy_origin.py release DEVICE_SLUG
python scripts/comfy_origin.py logout
```

Release after the work. `closing` means confirmation is pending; only `closed` proves tracked submissions have known outcomes and the source queue is empty. Recheck `devices` or request release again with bounded observation. Offline sources, unknown execution and maintenance cannot be force-unlocked. Preserve state and report the actual pending condition if confirmation is unavailable. Logout invalidates only this session; it does not cancel tasks. Do not log out while claiming an unresolved reservation was returned.

## Storage and trust boundary

Native records are independent of `/v1/jobs`, public model allowlists, business quotas/metering and cloud content. Input/output/temp files remain on the device. The gateway stores ownership/receipts and personal text, not another cloud media copy. Native files do not inherit cloud eight-hour deletion; local downloads are another copy. Device cleanup follows its existing policy.

Installed custom nodes execute with host authority; native mode is not a malicious-node sandbox. Grants permit requested graph operations, not node installation, weight downloads, OS commands, server maintenance, other users' content or external publication. Unknown private extension APIs and external cloud services are not universally exposed. The browser supports authenticated WS text/binary previews; this client uses HTTP polling and does not include a WebSocket implementation.

## Native HTTP map

| Endpoint | Use |
| --- | --- |
| `POST /origin/api/session` | JSON api_key; get Cookie and CSRF. |
| `GET/DELETE /origin/api/session` | Inspect/invalidate this session; DELETE needs CSRF. |
| `GET /origin/api/devices` | Authorized slugs, source counts and reservation heartbeat. |
| `POST /origin/api/devices/{slug}/acquire` | Persistent exclusive access; CSRF. |
| `POST /origin/api/devices/{slug}/release` | Safe return/reconciliation; CSRF. |
| `GET /origin/{slug}/object_info[/class]`, `/models[/folder]` | Node schemas and installed model filenames. |
| `POST /origin/{slug}/upload/image`, `/upload/mask` | Active reservation, Cookie/CSRF; multipart image, optional owned original_ref. |
| `POST /origin/{slug}/prompt` | Active reservation, Cookie/CSRF, 16–128-character Idempotency-Key and API prompt. |
| `GET /origin/{slug}/queue`, `/history[/prompt_id]` | This logical Key's native task details. Directory source totals are separate. |
| `GET /origin/{slug}/view?...` | Owned filename/subfolder/type; HEAD/Range use the same auth. |
| `GET /origin/{slug}/userdata?dir=workflows&recurse=true`, `/v2/userdata?path=workflows` | Live source tree plus Key-private `@personal`; no model filtering. |
| `/origin/{slug}/userdata/workflows/...` | Source file read; POST save/move and DELETE require active reservation, CSRF and Idempotency-Key. |
| `/origin/{slug}/userdata/workflows/@personal/...`, `/settings` | Key/device-private files/settings; writes require CSRF. |
| `POST /origin/{slug}/queue`, `/interrupt` | Explicit pending cancellation/current interruption, not force-release. |
| `WS /origin/{slug}/ws?clientId=...` | Authenticated owned events/previews; client IDs rewritten upstream. |

HTTP/WS recheck the native session, original credential, Key/parents and device grants. Caller-supplied identifiers do not confer ownership. Business Bearer auth and `/v1/jobs` continue independently.
