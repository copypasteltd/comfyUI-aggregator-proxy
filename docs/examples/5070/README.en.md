# Selected RTX 5070 workflows

The [workflow files](../../../app/examples/5070-workflows/) contain three frozen manifests, a catalog and two neutral reference images. They exclude weights, credentials, private source prompts and the other candidate workflows. See [the live verification record](../../122-RTX5070纯HTTP接入与调用验证.md).

| Alias | Input | Frozen output |
| --- | --- | --- |
| comfy-5070-video-h3 | Text prompt | 512×288, 39 frames at 24 fps, 1.625 seconds |
| comfy-5070-video-h3-i2v | Prompt and one first frame | Same |
| comfy-5070-video-h3-refs | Prompt and two reference images | Same |

Import a manifest into Admin, freeze it, bind it to an explicitly configured remote_http channel, synchronize the API declaration, run validation, approve the runtime profile and publish the model. The configured GPU resource key must match the manifest. The API declaration describes available nodes and model options; it does not claim host-file checksums. The existing local Worker dependency mode remains available.

Grant the business Key access to the new aliases, releases, channel and required scopes (including jobs:create/read, videos:create, assets:upload and content:read/download). Existing Key policies do not expand automatically. The temporary verification Key is disabled. Set COMFY_AGGREGATOR_API_KEY and run the existing client from the project root:

```text
python app/scripts/verify_reference_api.py --base-url http://43.165.167.179:39443 --model comfy-5070-video-h3 --mode text-video --prompt "A green lime beside a white cup, soft daylight, fixed camera." --size 512x288 --seconds 1.625 --request-id my-5070-t2v-001 --output app/.local/my-5070-t2v-001
```

For first-frame video, use comfy-5070-video-h3-i2v, mode video-first-frame and one --reference argument. For two references, use comfy-5070-video-h3-refs, mode video-refs and two --reference arguments. The included reference-0.png and reference-1.png are safe sample inputs. Use a separate request ID and output directory for each different request, retaining them when resuming a request.

For direct HTTP, POST /v1/videos with Authorization: Bearer, Content-Type: application/json and a stable Idempotency-Key:

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

Upload references to /v1/assets first. First-frame requests use input_reference.asset_id; two-reference requests use a references array with subject/style roles. Poll GET /v1/jobs/{id} and download GET /v1/content/{id}/download using the same Key. Reuse the original idempotency key after an ambiguous timeout. The immutable manifests intentionally retain approved=false: importing into another environment still requires real validation and a separate runtime approval.

The client uploads assets, submits /v1/videos, polls the task and verifies the authenticated original download, idempotency, SHA-256, HEAD and Range. Other dimensions and durations require a newly validated preset. These short runs demonstrate delivery; they do not establish long-form quality or fleet capacity. Cloud originals expire eight hours after success. Worker spool files remain for at least one hour after cloud acknowledgement. Automatic deletion of the original ComfyUI files is still a separate pending design.
