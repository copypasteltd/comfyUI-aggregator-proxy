import type { JsonObject } from "./types";
import type { SchemaResult } from "./schema-validator";

let worker: Worker | undefined;
let sequence = 0;
const pending = new Map<number, { resolve: (result: SchemaResult) => void; reject: (error: Error) => void }>();

export async function validateModelSchema(schema: JsonObject, request: unknown): Promise<SchemaResult> {
    if (typeof window === "undefined") return (await import("./schema-validator")).validateSchema(schema, request);
    if (!globalThis.Worker) throw new Error("此浏览器无法启动参数校验，请换用支持 Worker 的浏览器");
    if (!worker) {
        // Never send a profile, API Key, fetch options or executable source to this Worker.
        worker = new Worker(new URL("./schema-worker.ts", import.meta.url), { type: "module" });
        worker.onmessage = (event: MessageEvent<{ id: number; result?: SchemaResult; error?: string }>) => {
            const item = pending.get(event.data.id);
            if (!item) return;
            pending.delete(event.data.id);
            if (event.data.result) item.resolve(event.data.result);
            else item.reject(new Error(event.data.error || "参数校验失败"));
        };
        worker.onerror = () => {
            pending.forEach((item) => item.reject(new Error("参数校验器未能启动，请检查页面版本或联系管理员")));
            pending.clear(); worker?.terminate(); worker = undefined;
        };
    }
    return new Promise((resolve, reject) => {
        const id = ++sequence;
        pending.set(id, { resolve, reject });
        try { worker!.postMessage({ id, schema, request }); }
        catch (error) { pending.delete(id); reject(error); }
    });
}
