import Ajv2020 from "ajv/dist/2020";
import type { ValidateFunction } from "ajv";
import { assertLocalSchema } from "./schema-local";
import type { JsonObject } from "./types";

const ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: false });
const validators = new Map<string, { source: string; schema: JsonObject; validate: ValidateFunction }>();
export type SchemaResult = { valid: boolean; errors: string[] };

// This module runs in a dedicated no-network Worker in the browser, or directly in Node tests.
export function validateSchema(schema: JsonObject, request: unknown): SchemaResult {
    assertLocalSchema(schema);
    const source = JSON.stringify(schema);
    const properties = schema.properties as Record<string, JsonObject> | undefined;
    const alias = String(properties?.model?.const || schema.title || "request");
    let cached = validators.get(alias);
    if (!cached || cached.source !== source) {
        if (cached) ajv.removeSchema(cached.schema);
        cached = { source, schema, validate: ajv.compile(schema) };
        validators.set(alias, cached);
    }
    const valid = Boolean(cached.validate(request));
    return { valid, errors: valid ? [] : (cached.validate.errors || []).map((error) => `${error.instancePath || "/"} ${error.message || "参数不符合模型要求"}`) };
}
