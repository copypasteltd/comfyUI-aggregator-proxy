# StoryFlow AI Canvas 🎨

**StoryFlow AI Canvas** 是一个基于 React 构建的纯前端节点式 AI 创作画板。它将 Google 的 **Gemini 3 Pro** (Nano Banana) 视觉大模型能力与自由流动的节点图表相结合，让用户可以通过可视化的连线方式，完成从文本生图、细节优化、到后期组合的完整创意工作流。

---

## ✨ 核心特性 / Features

* **♾️ 无限画布节点系统**：自由平移、缩放，支持多节点、复杂的有向无环图（DAG）连线逻辑。
* **🧠 Gemini 3 Pro 深度集成**：内置对生图底层 API 的集成支持，并包含防止过载（Overloaded）的指数退避重试机制。
* **🔒 安全的 API Key 管理**：支持用户自带 Key（Bring Your Own Key），所有凭证仅保留在浏览器的 `localStorage` 中，**绝对不会上传**。
* **🛠️ 丰富的节点类型**：
  * **📝 文本节点 (Text)**：灵感的起点，为下游提供基础文本。
  * **🖼️ AI 生图 (Image Gen)**：支持文生图、图+图生图（参考构图）。
  * **✨ 高清重绘 (Redraw)**：对指定的 4K+ 图像块进行 AI 细节增强和重构。
  * **🔬 超分放大 (Upscale)**：**纯前端实现**的渐进式高斯+锐化无损超分，输出真实物理抗锯齿的 **8K / 16K** 画质，无需消耗额外后端算力。
  * **📤 上传图片 (Upload)**：将本地工作流引入画布。
  * **🍰 图像合并 (Merge)**：可视化拖拽合成多张图片。
  * **✂️ 图片编辑 (Edit)**：直接在画布内框选、标记与裁剪。

## 🚀 快速开始 / Get Started

本项目完全使用纯前端构建，无需复杂的后端数据库。

### 环境要求
* [Node.js](https://nodejs.org/) (推荐 v18+)

### 1. 克隆项目
```bash
git clone https://github.com/your-username/storyflow-ai-canvas.git
cd storyflow-ai-canvas
```

### 2. 安装依赖
```bash
npm install
# 或者
yarn install / pnpm install
```

### 3. 运行开发服务器
```bash
npm run dev
```

### 4. 获取 API Key
由于应用使用 Gemini 3 Pro 的能力，您需要一个免费的 API Key 才能开始。
前往 [Google AI Studio 申请 API Key](https://aistudio.google.com/apikey)。

打开 `http://localhost:3000` 后，按界面提示在右上角 🔑 图标处输入您的 Key 即可立即体验！

---

## 🏗️ 架构 & 技术栈 / Tech Stack

* **核心框架**：React 18 + TypeScript + Vite
* **样式方案**：Tailwind CSS
* **图标库**：完全基于内联的无依赖 SVG 图标构建 (`components/Icons.tsx`)
* **AI 大模型服务**：`@google/genai` (Gemini 3 Pro)
* **超分算法**：自定义纯 HTML5 Canvas Progressive Bilinear Upsampling + Unsharp Mask

## 💡 产品展示

### 1. 连线化生图
通过将带有 "Prompt" 的文本节点连接到 "AI 生图" 节点，即可根据要求生成图片。连接已有的图片至 "AI 生图" 节点，将作为参考构图并进行扩散重绘！

### 2. 在浏览器里跑 "8K"
不依靠付费的高昂云端超分 API，StoryFlow 利用浏览器原生的 Canvas 编解码能力和分层采样进行插值，配合多线程优化，瞬间让你的作品放大到海报级尺寸。

---

## 🤝 贡献代码 / Contributing

欢迎提出 Issue，或者直接提交 Pull Request！我们非常乐意看到社区对以下方向的改进：
- 加入更多的图片处理节点（如去背景、滤镜）。
- 支持导出完整的有向无环图数据，以 `.json` 格式保存工作空间。
- 更多图表排列布局的自动优化。

## 📜 开源协议 / License

本项目基于 [MIT License](./LICENSE) 开源，您可以自由地使用、修改和分发。
