import type { JsonObject } from "./types";

export function assertLocalSchema(schema: unknown): asserts schema is JsonObject {
    if (!schema || typeof schema !== "object" || Array.isArray(schema) || (schema as JsonObject).$schema !== "https://json-schema.org/draft/2020-12/schema") throw new Error("模型参数说明的 Schema 版本暂不支持");
    const visit = (value: unknown) => {
        if (Array.isArray(value)) { value.forEach(visit); return; }
        if (!value || typeof value !== "object") return;
        for (const [key, child] of Object.entries(value)) {
            if ((key === "$ref" || key === "$dynamicRef") && (typeof child !== "string" || !child.startsWith("#"))) throw new Error("模型参数说明不允许外部引用");
            visit(child);
        }
    };
    visit(schema);
}
