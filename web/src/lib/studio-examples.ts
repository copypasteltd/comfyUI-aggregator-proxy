import type { Catalog, CatalogModel, TaskType } from "@/services/api/aggregator/types";
import { firstAllowedPreset } from "@/services/api/aggregator/composer";

export type StudioExample = { id: string; title: string; description: string; taskType: TaskType; references: number; prompt: string; steps: string };
export const studioExamples: StudioExample[] = [
    { id: "product-photo", title: "产品摄影", description: "从一段描述开始，设计干净的产品展示图。", taskType: "text_to_image", references: 0, prompt: "一台青柠绿色的圆润小机器人，白色无缝背景，柔和的摄影棚光线，正面三分之四视角，主体完整，产品摄影，无文字，无水印。", steps: "修改创作描述，选择服务公布的图像规格，再创建任务。" },
    { id: "product-edit", title: "换一个场景", description: "沿用一张物体图，尝试新的环境和光线。", taskType: "image_to_image", references: 1, prompt: "保留参考图中机器人的形状和颜色，将背景换成干净的浅灰色展台与柔和侧光，保持主体完整清晰，无文字。", steps: "示例图片可替换；源图片按模型公布的输入槽位绑定。" },
    { id: "reference-style", title: "双图创作", description: "用两张参考图组织主体与配色方向。", taskType: "references_to_image", references: 2, prompt: "参考第一张图的机器人主体，结合第二张图的造型与配色，创作一张统一风格的产品概念图，干净背景，主体完整。", steps: "先核对表单中的参考角色与顺序，再修改描述；角色遵循服务说明。" },
    { id: "video-scene", title: "让场景动起来", description: "用文字描述一段短镜头的主体和运动。", taskType: "text_to_video", references: 0, prompt: "柔和摄影棚中，一台青柠绿色小机器人缓慢转向镜头，镜头轻轻推进，光线稳定，动作自然，无文字。", steps: "尺寸、帧率和时长使用模型公布的完整规格组合。" },
    { id: "video-first-frame", title: "首帧动画", description: "以一张图片为起点，设计后续镜头运动。", taskType: "references_to_video", references: 1, prompt: "以参考图作为镜头起点，机器人轻轻转头，镜头缓慢推进，保持主体外观与背景稳定，动作柔和。", steps: "需要明确公布一个参考槽位的首帧模型，不用多参考模型替代。" },
    { id: "video-references", title: "双参考视频", description: "组织两张有序参考图，探索一段动态创作。", taskType: "references_to_video", references: 2, prompt: "结合两张参考图，展示小机器人在柔和光线下缓慢转动，镜头平稳，保持主体一致，动作自然。", steps: "根据公布的角色核对两张图片。当前设备是否能完成生成以实际任务结果为准。" },
    { id: "workflow-audio", title: "创作一段配乐", description: "通过已发布的 ComfyUI 音频工作流生成声音。", taskType: "text_to_audio", references: 0, prompt: "轻快、温暖的电子器乐配乐，柔和的合成器与轻打击乐，适合小机器人产品展示，无人声。", steps: "这是 Workflow 音频创作；可填写字段与时长遵循当前模型说明。" },
    { id: "object-3d", title: "物体转 3D", description: "从单张清晰物体图开始，获取三维模型文件。", taskType: "image_to_3d", references: 1, prompt: "", steps: "可替换示例物体图。仅提交模型需要的源图片，完成后保存 GLB 并按需预览。" },
];

export function exampleModel(example: Pick<StudioExample, "taskType" | "references">, catalog: Catalog): CatalogModel | undefined {
    return catalog.models.find((model) => model.task_type === example.taskType && !model.unavailable_reason
        && firstAllowedPreset(model, catalog.policy) >= 0
        && !model.usage.references.slots.some((slot) => slot.path === "input.mask" && slot.required)
        && model.usage.references.slots.filter((slot) => slot.path !== "input.mask").length === example.references);
}
