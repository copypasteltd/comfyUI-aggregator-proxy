"""Bounded read-only ComfyUI queue observations; failure never means idle."""
from __future__ import annotations

from typing import Any

import httpx
from generation_domain.errors import DomainError

from windows_worker.comfy_json import MAX_QUEUE_BYTES, async_request_json, checked_queue


async def observe_queue(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        value = checked_queue(await async_request_json(client, "GET", "/queue", maximum=MAX_QUEUE_BYTES))
        return {"reachable": True, "queue_running": len(value["queue_running"]), "queue_pending": len(value["queue_pending"])}
    except httpx.TimeoutException:
        return {"reachable": False, "error_code": "timeout"}
    except httpx.HTTPError:
        return {"reachable": False, "error_code": "unreachable"}
    except DomainError as exc:
        return {"reachable": False, "error_code": "response_too_large" if exc.code == "response_too_large" else "invalid_response"}
    except (ValueError, UnicodeError):
        return {"reachable": False, "error_code": "invalid_response"}
