from __future__ import annotations

import json
from typing import Any

import httpx
from generation_domain.errors import DomainError

from windows_worker.comfy_json import prompt_identifier

OOM_TYPES = frozenset({"torch.OutOfMemoryError", "torch.cuda.OutOfMemoryError"})
COLLECTION_REJECTIONS = frozenset({"output_count_mismatch", "invalid_output_path", "unsafe_path", "output_too_large", "output_media_mismatch"})
MEDIA_REJECTIONS = frozenset({"invalid_image", "unsupported_image", "invalid_video", "invalid_audio", "media_type_mismatch", "output_spec_mismatch"})


def preparation_failure_code(exc: BaseException) -> str:
    # Called only while the durable journal still proves no submit intent.
    # Do not classify arbitrary HTTP/client/configuration failures as temporary.
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return "preparation_transport_failure"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (408, 429, 500, 502, 503, 504):
        return "preparation_transport_failure"
    return "preparation_failed"


def execution_failure_code(history: dict[str, Any], prompt_id: str) -> str:
    messages = history.get("status", {}).get("messages")
    if isinstance(messages, list) and len(messages) <= 1000:
        for message in messages:
            if (isinstance(message, (list, tuple)) and len(message) == 2 and message[0] == "execution_error"
                    and isinstance(message[1], dict) and message[1].get("prompt_id") == prompt_id
                    and isinstance(message[1].get("exception_type"), str) and message[1]["exception_type"] in OOM_TYPES):
                return "comfy_oom"
    return "comfy_execution_failed"


def require_prompt_absent(queue: dict[str, Any], prompt_id: str) -> None:
    for key in ("queue_running", "queue_pending"):
        for item in queue[key]:
            if not isinstance(item, list) or len(item) < 2:
                raise DomainError("comfy_queue_unknown", "队列形状无法证明执行已停止")
            identifier = prompt_identifier(item[1])
            if identifier == prompt_id:
                raise DomainError("comfy_execution_not_stopped", "当前 prompt 仍在队列中，保留执行占用")


def rejected_media(exc: httpx.HTTPStatusError) -> bool:
    response = exc.response
    if response.status_code != 422 or len(response.content) > 4096:
        return False
    try:
        data = json.loads(response.content)
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return isinstance(code, str) and code in MEDIA_REJECTIONS
    except (ValueError, UnicodeError):
        return False
