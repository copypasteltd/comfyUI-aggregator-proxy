import { validateSchema } from "./schema-validator";
import type { JsonObject } from "./types";

self.onmessage = (event: MessageEvent<{ id: number; schema: JsonObject; request: unknown }>) => {
    try { self.postMessage({ id: event.data.id, result: validateSchema(event.data.schema, event.data.request) }); }
    catch (error) { self.postMessage({ id: event.data.id, error: error instanceof Error ? error.message : "模型参数校验失败" }); }
};
