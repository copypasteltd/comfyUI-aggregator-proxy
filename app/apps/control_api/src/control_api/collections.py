from fastapi import Query, Request
from generation_contracts.collections import CollectionQuery, JobStatus
from generation_domain.errors import DomainError


def _keys(request: Request, *, jobs: bool) -> None:
    allowed = {"limit", "cursor", "model"} | ({"status"} if jobs else set())
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise DomainError("invalid_query", "查询包含未知或重复参数", 400)


def job_query(request: Request, limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, min_length=1, max_length=4096), model: str | None = Query(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"), status: JobStatus | None = None) -> CollectionQuery:
    _keys(request, jobs=True)
    return CollectionQuery(limit=limit, cursor=cursor, model=model, status=status)


def content_query(request: Request, limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, min_length=1, max_length=4096), model: str | None = Query(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")) -> CollectionQuery:
    _keys(request, jobs=False)
    return CollectionQuery(limit=limit, cursor=cursor, model=model)
