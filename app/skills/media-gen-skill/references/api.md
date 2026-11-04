# API use and recovery

Example origin: `https://gen.sub2share.xyz`. All paths below are relative to the origin. Send `Authorization: Bearer $MEDIA_GEN_API_KEY`; credentials belong to a logical business Key and are separate from Admin/Worker credentials.

## Discovery

| Request | Scope | Purpose |
| --- | --- | --- |
| GET /v1/models | models:read | Models this Key can discover; each current model includes usage |
| GET /v1/models/{alias} | models:read | One model, its instructions, schema and example |
| GET /v1/capabilities | capabilities:read | Task type, release, specifications, output and usage |
| GET /v1/access-policy | Authenticated business Key | The current Key's access policy |

Use `usage.required_scopes` to identify missing workflow permissions. Image creation uses `images:create`, video uses `videos:create`, audio uses `audio:create`, and 3D uses `meshes:create`, in addition to `jobs:create`. Reading jobs uses `jobs:read`; listing artifact IDs uses `artifacts:read`; metadata uses `content:read`; download uses `content:download`; references and image-to-3D additionally require `assets:upload`.

`usage.request_schema` is a model-specific JSON Schema. `usage.presets` lists complete combinations; defaults apply when fields are omitted. Do not mix the quality of one preset with another preset's size/duration. `fps` and `strength` are optional only on models that advertise them. `negative_prompt` belongs to `input`, not `parameters`. `n` is always 1. Output audio format/sample rate/channels are preset properties, not arbitrary caller options.

For `image_to_3d`, use the dedicated request, quota and download guidance below. Image upload permission and image generation permission are separate; a mesh-only caller does not need `images:create`.

## Image-to-3D compatibility

The example deployment's cloud release 129.3 introduced corrected mesh-only admission and canonical GLB attachment filenames, and validates POSITION counts and hierarchies outside the default scene. Skill version 1.3.0 retains those server behaviors and adds declared typed media/text inputs; installing a Skill does not upgrade another deployment's server or Worker. Discover that deployment's approved model first. The source-image-only example below describes the legacy profile; newer multiview, refinement or texturing variants declare their own named inputs in live usage.

For a complete source-image upload, job and result flow, the minimal scope set is `models:read`, `assets:upload`, `jobs:create`, `jobs:read`, `meshes:create`, `artifacts:read`, `content:read` and `content:download`. The `capabilities` command additionally uses `capabilities:read`. Enable the selected alias/release/channel/task in the Key's policy. Set a nonzero `daily_mesh_count` at Tenant, Project and Key levels; old policies default to zero. Do not add `images:create` to work around an outdated server's mesh-scope defect. Visibility alone is not admission or quota approval.

Upload one static image with the same Key, then use its returned asset ID in the model's example. The following illustrates the currently approved TRELLIS2 request; discover the alias and its current preset before use:

```json
{
  "model": "comfy-3080-3d-trellis2",
  "task_type": "image_to_3d",
  "input": {"source_image": {"asset_id": "REPLACE_WITH_UPLOADED_ASSET_ID"}},
  "parameters": {"quality": "standard", "seed": 17, "n": 1}
}
```

Omit prompt, negative_prompt, mask and references. Only advertised quality, seed and n=1 are accepted; size, seconds, fps and strength are rejected. No alternate `/meshes`, `/3d` or `/result` endpoint is assumed: submit `/v1/jobs`, poll the same job ID, then read and download its `artifacts[].id`.

The approved profile is static, self-contained triangle GLB 2.0 with MIME `model/gltf-binary`, up to 512 MiB, 5 million vertices and 10 million triangles. The server validates the file structure, POSITION accessors and all node hierarchies. `GET /v1/content/{id}` returns the top-level `media_type`, `bytes`, `sha256` and `expires_at`; its `metadata` object contains `format`, `mesh_count`, `vertex_count`, `triangle_count`, `material_count`, `texture_count` and `has_textures`. The helper also accepts `size_bytes` from compatible deployments. It verifies download length and SHA-256; it does not independently validate geometry. A texture, UV/PBR quality or printable topology is not guaranteed. OBJ, FBX, external buffers/textures, multi-file glTF, compressed geometry, animation, skinning and interactive Admin previews are outside this profile. Native 3D does not add Sub2API 3D settlement.

The authenticated `/v1/content/{id}/download` endpoint supports full GET, HEAD and a byte Range with the same Key's content authorization; GET/HEAD/Range carry `Content-Disposition: attachment; filename="content_….glb"`. The helper downloads the complete file and derives its automatic `.glb` extension from MIME. For a manual path use `download CONTENT_ID --output ./model.glb`; an explicit `--output` is preserved. Download before `expires_at` (eight hours after success); resuming a completed job does not renew retention or consume another generation.

## Native asynchronous submission

Copy the model's actual `usage.example_request`; this shape illustrates the fields:

```json
{
  "model": "REPLACE_WITH_DISCOVERED_ALIAS",
  "task_type": "text_to_image",
  "input": {"prompt": "A quiet garden in soft morning light"},
  "parameters": {"quality": "standard", "size": "1024x1024", "seed": 42, "n": 1}
}
```

The illustrated size/task type is not a promise that a particular model supports it. POST JSON to `/v1/jobs` with a stable `Idempotency-Key`. HTTP 202 returns a job ID and a Location header. Read `GET /v1/jobs/{job_id}` until a terminal state. On success, read `artifacts[].id`; there is no separate `/result` endpoint. Read `/v1/content/{content_id}` for MIME, bytes, SHA-256, expiry and actual media metadata; download `/v1/content/{content_id}/download` with the same Key.

## Reference assets

POST multipart/form-data to `/v1/assets` with exactly one **file** and optional **kind** (`image`, `video`, `audio`, `model`; default `image`). Image uploads accept static PNG/JPEG/WebP, at most 20 MiB and 25 million pixels. Typed video accepts MP4/WebM up to 512 MiB/120 seconds, audio up to 256 MiB/600 seconds, and model accepts self-contained static GLB up to 512 MiB. The server decodes actual contents instead of trusting the extension. The client streams uploads with a known length. A stable `Idempotency-Key` can recover the same upload for 24 hours. Use the returned `id` with the same Key; local paths and URLs are not asset IDs.

An image edit supplies `input.source_image: {"asset_id": "asset_..."}`. Supply a mask only if the live model schema permits it. Reference image/video tasks supply an ordered array:

```json
{
  "prompt": "Preserve these subjects",
  "references": [
    {"asset_id": "asset_FIRST_UPLOAD_ID", "role": "reference_0"},
    {"asset_id": "asset_SECOND_UPLOAD_ID", "role": "reference_1"}
  ]
}
```

Use exactly the count in `usage.references.exact_reference_count`. For a legacy video model with one reference the role must be **first_frame**. With multiple legacy references, array positions determine frozen Workflow slots. Do not pass `weight` unless that exact slot advertises support; allowed values are 0–2. The legacy `references` array accepts images only. New typed inputs use `input_slots` and `text_slots` as described below. Some legacy audio models consume `input.prompt`; new versions expose separate dialogue, score, style and transcript fields.

## Named media, text, audio policy and continuation

Workflow 1.3 advertises each `usage.input_slots` entry with its name, kind, semantic role, required flag, MIME types and byte limit. Upload with the same Key and correct kind, then submit `input.media: [{"slot":"source_video","asset_id":"asset_..."}]`. A model may require first/last images, multiple views of one object, a speaker reference, a source video or a coarse GLB. Provide the exact roles; do not substitute a local path, an absolute device path or a network URL.

Named text uses `input.texts: {"dialogue":"[S1] ...\n[S2] ...","speaker_1_transcript":"..."}`. Required names come from `usage.text_slots`. Reference transcripts must match the corresponding uploaded recordings; dialogue is the newly generated speech. Music variants can declare separate `style`, `lyrics` and `abc` notation fields. Up to 16 media slots and 16 text slots are accepted; total named text is at most 32,000 characters. Optional fields may be omitted; unknown, duplicate or missing required slots fail before admission.

The helper exposes `--media SLOT=FILE`, `--text NAME=TEXT` and `--text-file NAME=FILE`. It uploads using the advertised kind, supplies returned IDs, and preserves semantic slot names. Omit `--prompt` when the schema has no prompt field. For an explicit request, upload each file with `upload FILE --kind KIND`, edit the live example and use `submit request.json`.

`usage.output.audio_policy` states the frozen video soundtrack behavior: `generated` creates audio, `source_video` retains the selected source video's audio, `reference_audio` uses the declared audio reference, and `silent` returns no audio track. This is part of the model's contract and is not an arbitrary request switch. Conditioning on a voice does not promise lossless source audio.

A continuation model declares `usage.context.required=true` and a compatibility digest. Pass `input.context: {"job_id":"job_..."}` or `--parent-job JOB_ID`. The parent must be successful, have produced the matching owned context, and belong to the same Tenant, Project and API Key. Context normally expires after eight hours; an admitted unfinished child protects its referenced context from premature cleanup. Do not share context files across Keys or accept caller-provided raw context paths.

## Idempotency, timeouts and download safety

The helper writes state under the chosen output directory before submission, with origin, exact request, request hash, credential fingerprint and Idempotency-Key. Keep this private: it contains the prompt and asset IDs, although it contains no key. Do not edit it while recovering an uncertain request. Resume uses the original body and key. Once a job ID is known, resume only reads that job and does not POST again. A different API key is rejected for an uncertain POST because idempotency is scoped to the logical Key; a rotated key may read a known job if it still represents the same logical Key.

Polling is bounded. A client timeout does not cancel a job, stop a GPU or prove failure. Continue reading the same ID. On 429 respect the service's Retry-After. On 401/403 correct authentication or authorization; do not retry with random credentials. On 409 inspect the structured error; an idempotency conflict requires restoring the original request. On 422 consult the model schema/presets/reference contract. On 503 retry reads with a bounded budget; an uncertain POST must retain its original Idempotency-Key and body.

Downloads are fetched from the configured origin's authenticated content endpoint, not from an arbitrary URL found in a response. Redirects are refused. Verify exact bytes and SHA-256, keep existing output files, and remove only the helper's own unfinished temporary file on error. Expired or revoked content can no longer be downloaded. Save needed outputs before expiry; a local downloaded copy has its own lifecycle.
