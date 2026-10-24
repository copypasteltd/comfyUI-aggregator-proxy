from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from generation_domain.errors import DomainError

from windows_worker.comfy_json import (
    MAX_HISTORY_BYTES,
    MAX_QUEUE_BYTES,
    MAX_RECEIPT_BYTES,
    checked_history,
    checked_queue,
    prompt_identifier,
    request_json,
)
from windows_worker.journal import Journal


class SubmissionUnknown(DomainError):
    def __init__(self) -> None:
        super().__init__("submission_unknown", "提交结果未知，保留 GPU 占用并核对原任务")


class ComfyClient:
    def __init__(self, base_url: str, authorization: str | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DomainError("invalid_channel_url", "渠道 URL 必须是已配置的 HTTP(S) 地址", 422)
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers={"Authorization": authorization} if authorization else {}, timeout=httpx.Timeout(connect=5, read=20, write=20, pool=5), follow_redirects=False, transport=transport, trust_env=False)

    def submit(self, journal: Journal, attempt_id: str, graph: dict[str, Any], workflow_digest: str, *, association: str | None = None) -> str:
        if association is None:
            association = journal.intent(attempt_id, workflow_digest)
        else:
            row = journal.get(attempt_id)
            if not row or row["stage"] != "submit_intent" or row["association_id"] != association:
                raise DomainError("journal_conflict", "缺少已持久化的提交关联标识")
        journal.begin_post(attempt_id, association)
        try:
            value = request_json(self.http, "POST", "/prompt", maximum=MAX_RECEIPT_BYTES, json={"prompt": graph, "client_id": association, "extra_data": {"render_control": {"association_id": association, "attempt_id": attempt_id}}})
            prompt = prompt_identifier(value.get("prompt_id"))
        except (httpx.HTTPError, DomainError, ValueError) as exc:
            # Even an HTTP failure may follow server acceptance. Never retry here.
            raise SubmissionUnknown() from exc
        journal.submitted(attempt_id, prompt)
        return prompt

    def reconcile(self, association: str, prompt_id: str | None) -> dict[str, Any]:
        try:
            queue = checked_queue(request_json(self.http, "GET", "/queue", maximum=MAX_QUEUE_BYTES))
            for group in ("queue_running", "queue_pending"):
                for entry in queue.get(group, []):
                    if self._association(entry) == association:
                        return {"observation": "found", "source": group, "prompt_id": prompt_identifier(entry[1])}
            history = checked_history(request_json(self.http, "GET", "/history/" + prompt_identifier(prompt_id) if prompt_id else "/history", maximum=MAX_HISTORY_BYTES, params={"max_items": 1000}))
            for key, value in history.items():
                if key == prompt_id or self._association(value.get("prompt", [])) == association:
                    return {"observation": "found", "source": "history", "prompt_id": prompt_identifier(key)}
        except (httpx.HTTPError, DomainError, ValueError, TypeError, IndexError):
            return {"observation": "unknown", "safe_to_resubmit": False}
        # Absence cannot prove an execution stopped, especially after process restart.
        return {"observation": "absent", "safe_to_resubmit": False}

    @staticmethod
    def _association(entry: Any) -> str | None:
        if not isinstance(entry, list) or len(entry) < 4 or not isinstance(entry[3], dict):
            return None
        data = entry[3].get("render_control", {})
        return data.get("association_id") if isinstance(data, dict) else None

    def close(self) -> None:
        self.http.close()
