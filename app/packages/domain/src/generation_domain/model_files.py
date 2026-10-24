from __future__ import annotations

import unicodedata

from generation_domain.errors import DomainError
from generation_domain.workflows import relative_file

MODEL_SUFFIXES = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx"}
MODEL_KINDS = {"checkpoints": "checkpoint", "diffusion_models": "diffusion_model", "unet": "unet", "vae": "vae", "loras": "lora", "text_encoders": "text_encoder", "clip": "clip", "embeddings": "embedding", "upscale_models": "upscaler", "controlnet": "controlnet"}


def model_file_path(value: str) -> str:
    relative_file(value)
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    if (not 1 <= len(value) <= 1024 or len(parts) < 2 or parts[0] != "models"
            or unicodedata.normalize("NFC", value) != value
            or any(unicodedata.category(c) in ("Cc", "Cf") or c in '<>"|?*' for c in value)
            or any(part.startswith(".") or part.rstrip(" .") != part or part.split(".", 1)[0].upper() in reserved for part in parts)
            or "." not in parts[-1] or "." + parts[-1].rsplit(".", 1)[-1].lower() not in MODEL_SUFFIXES):
        raise DomainError("model_file_not_allowed", "模型路径不在允许的模型目录或文件格式内", 422)
    return value


def inferred_model_kind(path: str) -> str:
    return MODEL_KINDS.get(path.split("/")[1], "other")
