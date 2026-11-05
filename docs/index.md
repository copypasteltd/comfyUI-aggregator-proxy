# Infinite Canvas Documentation Index

## Independent ComfyUI canvas

- [Current independent documentation](studio/README.md): static canvas39445 directly calls the existing API39443. API repositories and running services are read-only; no forwarding changes.
- [Current core scope and specific limits](studio/37-交付范围纠正与核心适配现状.md) · [Deployment and usage](studio/21-独立画布部署与使用.md) · [Boundaries](studio/20-独立画布与既有API融合最终边界.md) · [Current progress](studio/10-开发进度与交付记录.md). The historical46-row assessment is not a development backlog or a mandatory all-item release gate.
- [139.2 actual GPU-response recovery and offline originals](studio/24-真实故障恢复与离线素材验收.md): one real image-edit job, seven actual saved-image UI checks, retained failures and MP4/GIF; physical phones and fleet acceptance remain pending.
- [Independent update notes](../CHANGELOG.studio.md). Upstream optional Agent/backend/Docker guides below are not part of the managed deployment; local Docker/WSL/virtual machines are prohibited.

## Overview

- [Quick Start](/docs/overview/quick-start)
- [Features](/docs/overview/features)
- [Deploy on Render](/docs/overview/render)
- [Docker Deployment](/docs/overview/docker)
- [Third-party Prompt Sources](/docs/overview/third-party-prompt-repositories)

## Canvas Guide

- [Canvas Node Guide](/docs/canvas/canvas-node-manual)
- [Canvas Shortcuts](/docs/canvas/canvas-shortcuts)

## Development and Data

- [Local Development](/docs/development/local-development)
- [Canvas Data Structure](/docs/development/canvas-data-structure)
- [How the Local Codex Connection Works](/docs/development/local-codex-canvas)

## Business

- [Open-source License](/docs/business/license)
- [Business Cooperation](/docs/business/business)

## Support and Security

- [Report a Vulnerability](/docs/support/security)
- [Sponsor the Project](/docs/support/sponsor)

## Project Progress

- [Changelog](/docs/progress/changelog)
- [Pending Tests](/docs/progress/pending-test)
- [TODO](/docs/progress/todo)

## Notes

- Managed canvas projects and My Assets primarily live in the current browser origin. Local ZIP/task exports are available; cloud sync/WebDAV is not part of the managed integration. Upstream standalone guides do not authorize adding another service connection.
- The business Key stays in page memory by default and can optionally be remembered in a trusted browser. Managed business requests go directly to the configured existing Aggregator API using credentials: omit.
