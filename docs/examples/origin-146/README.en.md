# Native workspace: actual request and calling flow

[中文](README.md) · [Complete design and constraints](../../146-原生ComfyUI面板访问与独占操作.md) · [Actual captures](../../media/origin-146/README.en.md)

Open the [native workspace](http://43.165.167.179:39443/origin/), enter a Key with explicit Admin device grants, acquire a device in the directory, then use the original ComfyUI editor. The UI handles sessions, submission idempotency and live connections. Ordinary business scheduling continues through `/v1/models` and the asynchronous business API.

| Order | Request and purpose |
| --- | --- |
| 1 | `POST /origin/api/session` with `{ "api_key": "YOUR_API_KEY" }`. Retain its HttpOnly Cookie and returned `csrf_token` in the same client session. Never put the Key in a URL. |
| 2 | `GET /origin/api/devices`; choose a `slug` from its `data`. Missing permission, offline sources, maintenance and existing occupancy cannot be bypassed. |
| 3 | `POST /origin/api/devices/{slug}/acquire` with `X-Origin-CSRF`; receive a persistent exclusive reservation and editor `url`. |
| 4 | `POST /origin/{slug}/prompt` with the Cookie, `X-Origin-CSRF` and a unique `Idempotency-Key`. Supply a ComfyUI **API-format** graph. Reuse the same identifier for the same submission intent. Never replace it and blindly resubmit an uncertain receipt. |
| 5 | Read live events through `/origin/{slug}/ws?clientId=YOUR_CLIENT_ID`, or query `/origin/{slug}/history/{prompt_id}`. Download owned output descriptors through `/origin/{slug}/view`, using the same session. |
| 6 | `POST /origin/api/devices/{slug}/release`. `closing` awaits reconciliation; `closed` requires every submission to have a confirmed outcome and the source queue to be empty. Finally send a CSRF-authenticated `DELETE /origin/api/session`. |

Use your own CSRF value for writes. The server rechecks the original Key, explicit device grants and reservation. Another client's IDs, prompt IDs, filenames or device slugs never confer ownership. The complete design covers 127 MiB uploads, mask ownership, personal workflows/settings and HEAD/Range. Automation must retain outstanding submission and release states; signing out does not stop execution.

[workflow-request.json](workflow-request.json) is the actual browser submission: `EmptyImage` creates a 64×64 Lime image and `SaveImage` saves it. Original UI workflow metadata is retained; **only its browser client_id is replaced with `example-native-client`**. This placeholder cannot reconnect to the historical live session. The service separately rewrites real client IDs into a logical-Key namespace upstream.

[actual-browser-response.json](actual-browser-response.json) is the actual receipt for that submission, including its historical `prompt_id`. It is not a template for a new response; replaying the example creates a new task. The [editor screenshot](../../media/origin-146/origin-native-5070.png) and recording show its result. The separate HTTP diagnostic produced [diagnostic-64x64.png](../../media/origin-146/diagnostic-64x64.png) for hash and Range checks.

This diagnostic requires no model weights and does not certify model quality or all modalities. Native tasks are separate from `/v1/jobs`, do not upload to cloud content and do not inherit eight-hour deletion. Installed custom nodes run with host authority, so native access is for trusted operators.
