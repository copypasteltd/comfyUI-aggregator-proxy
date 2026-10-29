# Nexa Canvas

**Nexa Canvas** is an open-source, browser-based AI workflow canvas. It lets you build visual generation pipelines by connecting **text**, **image**, and **video** nodes on an infinite canvas — similar in spirit to node-based creative tools, but designed around **bring-your-own-key (BYOK)** usage: you connect your own AI providers, and nothing is billed or managed by the app itself.

> Build creative AI workflows on a canvas. Use your own API keys. No backend required to get started.

---

## What It Does

Most AI tools give you a single prompt box. Nexa Canvas gives you a **visual workspace** where each step in a creative process is a node you can move, connect, and iterate on.

Typical use cases:

- **Script → image → video** — Write a scene in a text node, feed it into an image node, then into a video node.
- **Prompt exploration** — Place multiple generation nodes side by side and compare outputs from different prompts or models.
- **Reference-driven generation** — Upload an image or video into a node and use it as context for downstream AI calls.
- **Personal creative lab** — Experiment with OpenAI-compatible APIs or your own custom gateway without switching between tabs.

Everything runs in the browser. Your canvas layout, generated outputs, and API settings are stored locally in `localStorage` — there is no account system and no server-side database in this project.

---

## How It Works

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Text Node  │────▶│ Image Node  │────▶│ Video Node  │
│  (prompt)   │     │  (visual)   │     │  (motion)   │
└─────────────┘     └─────────────┘     └─────────────┘
```

1. **Add nodes** from the left toolbar (Text, Image, or Video).
2. **Connect nodes** by dragging from the `+` handle on one node to another.
3. **Enter a prompt** on each node (or rely on the connected upstream output as context).
4. **Generate** — the app calls your configured API and renders the result inside the node.
5. **Preview fullscreen** by clicking on generated content.

Connected inputs are passed into generation requests so downstream nodes can use upstream text, images, or metadata as context.

---

## Features

| Feature | Description |
|--------|-------------|
| **Infinite canvas** | Pan and zoom across your workflow using [React Flow](https://reactflow.dev/). |
| **Text nodes** | Generate copy, scripts, or creative briefs via OpenAI-compatible chat APIs or a custom endpoint. |
| **Image nodes** | Generate images via OpenAI-compatible image APIs or a custom endpoint. Upload local images as references. |
| **Video nodes** | Generate videos through a custom POST endpoint (e.g. your proxy to Veo, Kling, Runway, Replicate). Upload local videos as references. |
| **Node connections** | Wire outputs into inputs to build multi-step pipelines. |
| **Model selector UI** | Browse provider/model lists per node type (UI catalog; actual API model is configured in API Settings). |
| **API Settings modal** | Configure OpenAI-compatible keys, base URL, models, and optional custom endpoints. |
| **Generation queue** | Track running, completed, and failed generation tasks in real time. |
| **Local persistence** | Canvas nodes, edges, comments, and API settings survive page reloads. |
| **Comments** | Annotate the canvas in comment mode. |
| **Fullscreen preview** | Inspect generated text, images, or videos in a modal viewer. |

---

## Tech Stack

- **React 19** + **Vite 7**
- **React Flow 11** — node graph canvas
- **Lucide React** — icons
- No backend — client-side only

---

## Getting Started

### Prerequisites

- Node.js 18+
- npm (or pnpm/yarn)

### Install & run

```bash
git clone https://github.com/YusufAlper17/nexa-canvas.git
cd nexa-canvas
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

### Production build

```bash
npm run build
npm run preview
```

Static files are output to `dist/` and can be deployed to Vercel, Netlify, Cloudflare Pages, or any static host.

---

## API Configuration

Open **API Settings** from the top bar or the **Add API Key** indicator in the bottom-left corner.

### OpenAI-compatible provider (text & image)

Works with OpenAI and any API that mirrors these endpoints:

| Setting | Default | Used for |
|---------|---------|----------|
| API Key | — | `Authorization: Bearer …` |
| Base URL | `https://api.openai.com/v1` | Provider root |
| Text Model | `gpt-4o-mini` | `POST /chat/completions` |
| Image Model | `dall-e-3` | `POST /images/generations` |
| Image Size | `1024x1024` | Image dimensions |

Compatible with many gateways (OpenRouter, Together, local LM Studio proxies, etc.) as long as they expose the same REST shape.

### Custom endpoints (text, image, video)

For providers that do not match OpenAI's API, point Nexa Canvas at your own gateway or serverless function. Custom endpoints take priority when configured.

| Setting | Purpose |
|---------|---------|
| Custom API Key | Optional `Bearer` / `x-api-key` header value |
| Text Endpoint | `POST` URL for text generation |
| Image Endpoint | `POST` URL for image generation |
| Video Endpoint | `POST` URL for video generation (required for video nodes) |
| Video Model | Model identifier sent in the request body |

### Security note

API keys are stored **only in your browser's `localStorage`**. They are never committed to this repository.

For public deployments, consider routing requests through a **serverless proxy** so keys never ship to end users' browsers. This repo intentionally stays frontend-only for simplicity and transparency.

---

## Custom Endpoint Contract

Your endpoint receives a `POST` request with `Content-Type: application/json`.

### Request body

```json
{
  "type": "text",
  "prompt": "A cinematic shot of a cat in a car",
  "input": {
    "content": "Optional connected upstream output",
    "url": "Optional image/video URL from a parent node"
  },
  "model": "your-model-id",
  "aspectRatio": "16:9",
  "style": "cinematic"
}
```

`type` is one of `"text"`, `"image"`, or `"video"`. `input`, `aspectRatio`, and `style` are included when relevant.

### Accepted response shapes

**Text** — any of:

```json
{ "text": "Generated content…" }
{ "content": "Generated content…" }
{ "choices": [{ "message": { "content": "…" } }] }
```

**Image** — any of:

```json
{ "url": "https://…" }
{ "image_url": "https://…" }
{ "data": [{ "url": "https://…" }] }
{ "b64_json": "base64-encoded-png" }
```

**Video** — any of:

```json
{ "url": "https://…" }
{ "video_url": "https://…" }
{ "data": [{ "url": "https://…" }] }
```

Non-2xx responses surface as inline errors on the node and in the queue panel.

---

## Project Structure

```
src/
├── components/
│   ├── Canvas/          # React Flow canvas, nodes, comments
│   ├── Settings/        # API Settings modal
│   ├── Toolbar/         # Top bar & left toolbar
│   ├── Queue/           # Generation queue panel
│   └── …
├── services/
│   ├── apiSettings.js   # localStorage key management
│   ├── generationService.js  # API calls (text / image / video)
│   └── generationQueue.js    # Task state & subscriptions
└── data/
    ├── appConfig.js     # App name & branding
    └── models.js        # Model catalog for the UI selector
```

---

## Keyboard & Interaction

| Action | How |
|--------|-----|
| Add node | Left toolbar `+` → Text / Image / Video |
| Connect nodes | Drag from `+` handle on the right of a source node to the left of a target |
| Delete node | Select node → `Delete` or `Backspace` |
| Multi-select | `Shift` + click |
| Generate | Click the arrow button on a node's prompt bar |
| Upload media | Click or drag-and-drop on an empty image/video node |
| Comments | Toggle comment mode in the left toolbar, then click the canvas |

---

## Current Limitations

- **Video generation** requires a custom endpoint — there is no built-in direct integration with a specific video provider.
- **Model selector labels** are a UI catalog; the model IDs sent to OpenAI-compatible APIs come from API Settings, not from the per-node dropdown.
- **No cloud sync** — workflows are per-browser. Clearing site data removes your canvas and keys.
- **No authentication** — single-user, local-first by design.
- **CORS** — browser requests go directly to your configured APIs; providers must allow cross-origin requests or you must use a proxy.

---

## Deployment

Deploy the `dist/` folder after `npm run build` to any static host.

Example with Vercel:

```bash
npm run build
# deploy dist/ or connect the repo to Vercel with build command: npm run build
```

Set no environment variables for API keys — users configure keys inside the app. If you add a backend proxy later, document those env vars in your own deployment README.

---

## Contributing

Contributions are welcome. Please open an issue before large changes. Focus areas:

- Additional provider adapters
- Serverless proxy examples
- Node types (audio, document, etc.)
- Cloud project sync

---

## License

Add a license before publishing (MIT is recommended for open-source projects).

---

## Author

Built by [Yusuf Alper Ilhan](https://github.com/YusufAlper17).
