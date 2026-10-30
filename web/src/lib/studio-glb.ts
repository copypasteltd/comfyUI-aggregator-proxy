// Security preflight only; Three.js GLTFLoader owns glTF decoding and rendering.
const supported = new Set([
    "KHR_lights_punctual", "KHR_materials_anisotropy", "KHR_materials_clearcoat", "KHR_materials_dispersion",
    "KHR_materials_emissive_strength", "KHR_materials_ior", "KHR_materials_specular", "KHR_materials_transmission",
    "KHR_materials_iridescence", "KHR_materials_unlit", "KHR_materials_volume", "KHR_mesh_quantization",
    "KHR_texture_transform", "EXT_materials_bump", "EXT_mesh_gpu_instancing", "EXT_texture_avif", "EXT_texture_webp",
]);
const decoders = new Set(["KHR_draco_mesh_compression", "KHR_meshopt_compression", "EXT_meshopt_compression", "KHR_texture_basisu"]);
export const embeddedGlbUri = (value: string) => /^data:(?:application\/(?:octet-stream|gltf-buffer)|image\/(?:png|jpeg|webp|avif));base64,[a-z\d+/=\s]*$/i.test(value);

export function inspectStudioGlb(data: ArrayBuffer): string[] {
    const header = new DataView(data);
    if (data.byteLength < 20 || header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2 || header.getUint32(8, true) !== data.byteLength || header.getUint32(16, true) !== 0x4e4f534a) throw new Error("GLB 文件头、版本或长度无效，请另存原件检查");
    const jsonLength = header.getUint32(12, true);
    if (jsonLength % 4 || jsonLength < 2 || 20 + jsonLength > data.byteLength) throw new Error("GLB JSON 数据不完整");
    for (let offset = 20 + jsonLength; offset < data.byteLength;) {
        if (offset + 8 > data.byteLength) throw new Error("GLB 数据块不完整");
        const length = header.getUint32(offset, true);
        if (length % 4 || offset + 8 + length > data.byteLength || header.getUint32(offset + 4, true) === 0x4e4f534a) throw new Error("GLB 数据块长度或顺序无效");
        offset += 8 + length;
    }
    let json;
    try { json = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(data, 20, jsonLength))); }
    catch { throw new Error("GLB JSON 无法读取"); }
    if (json?.asset?.version !== "2.0" || (json.asset.minVersion && json.asset.minVersion !== "2.0")) throw new Error("仅支持 glTF 2.0 的 GLB 原件");
    for (const name of ["extensionsUsed", "extensionsRequired"]) if (json[name] !== undefined && (!Array.isArray(json[name]) || json[name].some((item: unknown) => typeof item !== "string"))) throw new Error("GLB 扩展声明无效");
    const used: string[] = json.extensionsUsed || [], required: string[] = json.extensionsRequired || [];
    for (const extension of required) if (!supported.has(extension) && !decoders.has(extension)) throw new Error(`暂不支持必要扩展 ${extension}，请另存原件打开`);
    for (const extension of [...used, ...required]) if (decoders.has(extension)) throw new Error(`暂未内置 ${extension} 解码器，请另存原件打开`);
    if (required.some((extension) => !used.includes(extension))) throw new Error("GLB 必要扩展未在已用扩展中声明，请另存原件检查");
    const pending = [json];
    while (pending.length) {
        const value = pending.pop();
        if (!value || typeof value !== "object") continue;
        if ("uri" in value && (typeof value.uri !== "string" || !embeddedGlbUri(value.uri))) throw new Error("预览仅允许内嵌资源，已阻止 GLB 外链；请另存原件打开");
        if (value.extensions && Object.keys(value.extensions).some((name) => decoders.has(name))) throw new Error("此 GLB 需要额外解码器，请另存原件打开");
        for (const child of Object.values(value)) if (child && typeof child === "object") pending.push(child);
    }
    for (const image of json.images || []) if (image.bufferView !== undefined && !/^image\/(png|jpeg|webp|avif)$/.test(image.mimeType || "")) throw new Error("GLB 内嵌贴图格式暂不支持，请另存原件打开");
    return used.filter((extension) => !supported.has(extension));
}
