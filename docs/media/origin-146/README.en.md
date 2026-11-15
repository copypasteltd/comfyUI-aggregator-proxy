# Native ComfyUI workspace: actual captures

[中文](README.md) · [Design and constraints](../../146-原生ComfyUI面板访问与独占操作.md) · [Deployment and verification](../../evidence/2026-11-15/origin-146-production.json)

These captures were taken from the deployed HTTP port 39443 service on 2026-11-15. Login, the device directory and Admin grants reuse the downloaded **04 · Lime** styles and fonts. The original ComfyUI editor retains its layout with Lime navigation and a device-local file notice. No API Keys, passwords, private device addresses or upstream credentials appear in the captures.

![Actual directory and native editor tour](origin-workspace.gif)

| Asset | Content |
| --- | --- |
| [Desktop login](origin-login-desktop.png) / [Mobile login](origin-login-mobile.png) | Actual entry; the mobile viewport is 390×844. |
| [Desktop directory](origin-devices-desktop.png) / [Mobile directory](origin-devices-mobile.png) | Three permitted devices, observed source queues and exclusive access controls. Permission does not imply availability. |
| [Admin grant](origin-admin-grant.png) | Actual cropped drawer. A browser saved the existing three selections and reopened it to confirm persistence; other Keys were unchanged. |
| [5070 native editor](origin-native-5070.png) | The initialized original frontend ran a two-node graph and displayed its result. |
| [Full recording](native-recording.mp4) | 80.2 seconds at original speed, without audio, including native loading, editor and actual submission. |
| [Workspace tour](workspace-tour.mp4) / [GIF](origin-workspace.gif) | The first seven seconds of the directory recording, the last thirteen seconds of the native recording, then a four-second actual Admin still. Segments retain their original speed; intervening loading is cut. The GIF uses 6 fps and a 960-pixel width and is an edited tour. |
| [Actual diagnostic PNG](diagnostic-64x64.png) | A 430-byte, 64×64 Lime image returned by a separate real HTTP/WS diagnostic, distinct from the browser task. |

`EmptyImage → SaveImage` exercises actual ComfyUI submission, execution, events and download. **It uses no trained model and does not validate model quality.** [Redacted browser request and receipt](../../examples/origin-146/README.en.md) are provided separately. Native files stay on the device and do not inherit cloud eight-hour retention.

The 5070 completed native execution. The 3080 ComfyUI was unreachable; the existing 4090 maintenance hold was preserved. Neither received a test submission. Installed extension modules can load, while unsupported private extension APIs, host maintenance and external cloud services remain constrained. This is not universal third-party plugin certification.

The [media manifest](../../evidence/2026-11-15/origin-146-media.json) records byte counts and SHA-256 for all ten assets. All owned browser pages, contexts and processes were closed in `finally`. No local Docker, WSL or virtual machine was used.
