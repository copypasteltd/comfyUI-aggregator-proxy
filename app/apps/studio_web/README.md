# Managed ComfyUI Studio / 托管创作画布

[中文完整说明](../../../docs/studio/USAGE.md) · [English guide](../../../docs/studio/USAGE.en.md) · [All 46 requirements and progress](../../../docs/studio/10-开发进度与交付记录.md)

This is the reviewed delivery mirror. Canonical development is in the separate 无限画布/infinite-canvas workspace. Build web/ with Node 22.14.0, npm 10.9.2 and npm ci; set VITE_STUDIO_MODE=managed and VITE_BASE=/studio/, then npm run build. Serve web/dist/ with the documented SPA/MIME/CSP rules. No Agent, Docker or WSL is required. Admin and business APIs remain in the existing aggregator.

The main document rejects arbitrary scripts/plugins; JSON Schema compiles only inside an isolated Worker. Business requests use Bearer credentials with cookies omitted. Seven task types share the original durable service; the canvas is not a second queue. Current release/real-media/mobile limitations are recorded in the progress document.

MIT upstream attribution and original Lime/font licenses are retained; see UPSTREAM.md. Historical research tools under docs/studio/tools are workspace-specific evidence tools, not an automatic production installer. Credentials and private raw logs are not included.
