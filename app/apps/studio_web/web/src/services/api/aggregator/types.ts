export const taskTypes = ["text_to_image", "image_to_image", "references_to_image", "text_to_video", "references_to_video", "text_to_audio", "image_to_3d"] as const;
export type TaskType = (typeof taskTypes)[number];
export type JsonObject = Record<string, unknown>;
export type Preset = Record<string, string | number | boolean>;

export type ReferenceSlot = { path: string; required: boolean; description: string; weight_supported: boolean };
export type ModelUsage = {
    schema_version: "1.0";
    display_name: string;
    description: string;
    notes: string[];
    tags: string[];
    task_type: TaskType;
    required_scopes: string[];
    defaults: Preset;
    presets: Preset[];
    request_schema: JsonObject;
    example_request: JobRequest;
    parameter_details: { path: string; required: boolean; description: string; rules: JsonObject }[];
    references: { exact_reference_count: number; slots: ReferenceSlot[]; media_types: string[]; max_upload_bytes: number; max_image_pixels: number };
    output: { media_types: string[]; max_bytes: number; expected_count: number };
    output_retention_seconds: number;
    availability_note: string;
};
export type PublishedModel = { id: string; usage?: ModelUsage };
export type Capability = PublishedModel & {
    task_type: TaskType;
    asynchronous: true;
    release: { version: number; digest: string };
    presets: Preset[];
    supported_parameters: string[];
    output: ModelUsage["output"];
};
export type CatalogModel = Capability & { usage: ModelUsage; unavailable_reason?: string };
export type AccessPolicy = {
    scopes: string[];
    limits: Record<string, { mode: "bounded" | "unlimited"; value: number | null }>;
    [key: string]: unknown;
};
export type Catalog = { models: CatalogModel[]; policy: AccessPolicy; releaseGuard: boolean; submissionLookup?: boolean };
export type JobRequest = { model: string; task_type: TaskType; input: JsonObject; parameters: JsonObject; client_request_id?: string };
export type ContentMetadata = {
    id: string;
    bytes: number;
    sha256: string;
    media_type: string;
    expires_at: number;
    metadata: Record<string, string | number | boolean>;
};
export type UploadedAsset = { id: string; bytes: number; sha256: string; media_type: string; status: "ready"; expires_at: number; width: number; height: number };
export const terminalStatuses = new Set(["succeeded", "failed", "cancelled", "expired"]);
export type RemoteJob = {
    id: string;
    object: "generation.job";
    model: string;
    task_type: TaskType;
    status: string;
    updated_revision: number;
    created_at: number;
    completed_at?: number;
    artifacts?: { id: string; [key: string]: unknown }[];
    error?: { code?: string; message?: string } | null;
    [key: string]: unknown;
};
export type ListPage<T> = { data: T[]; has_more: boolean; next_cursor: string | null };
export type SubmissionLookup = { object: "generation.submission_lookup"; outcome: "found"; job: RemoteJob } | { object: "generation.submission_lookup"; outcome: "not_observed"; job: null };
export type ApiResult<T> = { data: T; retryAt?: number; requestId?: string; studioContract?: string; submissionLookup?: string };
