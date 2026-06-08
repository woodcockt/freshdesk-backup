from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .db import Database


COLLECTION_SCHEMA_FIELDS = [
    {"name": "freshdesk_id", "type": "int64"},
    {"name": "subject", "type": "string"},
    {"name": "description_text", "type": "string"},
    {"name": "product_label", "type": "string", "facet": True, "optional": True},
    {"name": "tags", "type": "string[]", "facet": True},
    {"name": "status", "type": "int32", "facet": True, "optional": True},
    {"name": "priority", "type": "int32", "facet": True, "optional": True},
    {"name": "created_at", "type": "string", "optional": True},
    {"name": "updated_at", "type": "string", "optional": True},
    {"name": "created_at_ts", "type": "int64", "facet": True, "range_index": True},
    {"name": "updated_at_ts", "type": "int64", "facet": True, "range_index": True},
    {"name": "attachment_count", "type": "int32", "facet": True},
    {"name": "search_text", "type": "string"},
]

QUERY_BY = "subject,product_label,tags,description_text,search_text"
QUERY_BY_WEIGHTS = "8,6,5,3,1"
MAX_TEXT_CHARS = 200_000


class TypesenseError(RuntimeError):
    pass


@dataclass(frozen=True)
class TypesenseSearchResult:
    rows: list[dict[str, Any]]
    found: int
    search_ms: int | None = None


class TypesenseClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        collection: str = "freshdesk_tickets",
        timeout: float = 30.0,
    ) -> None:
        self.url = url.rstrip("/") + "/"
        self.api_key = api_key
        self.collection = collection
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def create_collection(self, recreate: bool = False) -> None:
        if recreate:
            self.delete_collection(ignore_missing=True)

        schema = {
            "name": self.collection,
            "fields": COLLECTION_SCHEMA_FIELDS,
            "default_sorting_field": "updated_at_ts",
            "token_separators": ["_", "-", "."],
        }
        try:
            self._request("POST", "/collections", schema)
        except TypesenseError as exc:
            if "already exists" not in str(exc).lower():
                raise

    def delete_collection(self, ignore_missing: bool = False) -> None:
        try:
            self._request("DELETE", f"/collections/{self.collection}")
        except TypesenseError as exc:
            if not ignore_missing or "404" not in str(exc):
                raise

    def import_documents(self, documents: list[dict[str, Any]]) -> tuple[int, int]:
        if not documents:
            return (0, 0)

        payload = "\n".join(json.dumps(document, separators=(",", ":")) for document in documents)
        response = self._request(
            "POST",
            f"/collections/{self.collection}/documents/import",
            payload + "\n",
            {"action": "upsert"},
            content_type="text/plain",
        )
        imported = 0
        failed = 0
        for line in str(response).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("success"):
                imported += 1
            else:
                failed += 1
        return (imported, failed)

    def search(
        self,
        query: str | None,
        limit: int = 25,
        product: str | None = None,
        tags: list[str] | None = None,
        status: int | None = None,
        priority: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        updated_from: str | None = None,
        updated_to: str | None = None,
    ) -> TypesenseSearchResult:
        filters = _build_filter_by(
            product=product,
            tags=tags,
            status=status,
            priority=priority,
            created_from=created_from,
            created_to=created_to,
            updated_from=updated_from,
            updated_to=updated_to,
        )
        params: dict[str, Any] = {
            "q": query or "*",
            "query_by": QUERY_BY,
            "query_by_weights": QUERY_BY_WEIGHTS,
            "text_match_type": "max_weight",
            "per_page": min(max(limit, 1), 250),
            "highlight_fields": "subject,description_text,search_text",
            "highlight_affix_num_tokens": 16,
            "highlight_start_tag": "<<",
            "highlight_end_tag": ">>",
        }
        if filters:
            params["filter_by"] = " && ".join(filters)
        if not query:
            params["sort_by"] = "updated_at_ts:desc"

        payload = self._request(
            "GET",
            f"/collections/{self.collection}/documents/search",
            params=params,
        )
        rows = [_hit_to_row(hit) for hit in payload.get("hits", [])]
        return TypesenseSearchResult(
            rows=rows,
            found=int(payload.get("found", len(rows))),
            search_ms=payload.get("search_time_ms"),
        )

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, Any] | None = None,
        content_type: str = "application/json",
    ) -> Any:
        url = urljoin(self.url, path.lstrip("/"))
        if params:
            url = f"{url}?{urlencode(params)}"

        data = None
        if body is not None:
            if isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = json.dumps(body).encode("utf-8")

        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "X-TYPESENSE-API-KEY": self.api_key,
                "Content-Type": content_type,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                content = response.headers.get("Content-Type", "")
                if "application/json" in content:
                    return json.loads(raw) if raw else {}
                return raw
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TypesenseError(f"Typesense HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TypesenseError(f"Typesense request failed: {exc}") from exc


def index_typesense(
    database: Database,
    client: TypesenseClient,
    batch_size: int = 500,
    recreate: bool = False,
) -> tuple[int, int]:
    database.refresh_search_documents()
    client.create_collection(recreate=recreate)

    imported = 0
    failed = 0
    for rows in database.iter_typesense_documents(batch_size=batch_size):
        documents = [row_to_document(row) for row in rows]
        batch_imported, batch_failed = client.import_documents(documents)
        imported += batch_imported
        failed += batch_failed
    return imported, failed


def row_to_document(row: dict[str, Any]) -> dict[str, Any]:
    freshdesk_id = int(row["freshdesk_id"])
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    return {
        "id": str(freshdesk_id),
        "freshdesk_id": freshdesk_id,
        "subject": _clip(row.get("subject") or ""),
        "description_text": _clip(row.get("description_text") or ""),
        "product_label": row.get("product_label") or "",
        "tags": row.get("tags") or [],
        "status": row.get("status"),
        "priority": row.get("priority"),
        "created_at": _isoformat(created_at),
        "updated_at": _isoformat(updated_at),
        "created_at_ts": _timestamp(created_at),
        "updated_at_ts": _timestamp(updated_at),
        "attachment_count": int(row.get("attachment_count") or 0),
        "search_text": _clip(row.get("search_text") or ""),
    }


def _hit_to_row(hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document") or {}
    return {
        "freshdesk_id": int(document["freshdesk_id"]),
        "subject": document.get("subject") or "",
        "product_label": document.get("product_label") or "",
        "tags": document.get("tags") or [],
        "status": document.get("status"),
        "priority": document.get("priority"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "rank": hit.get("text_match"),
        "excerpt": _best_highlight(hit) or _clip(document.get("search_text") or "", 280),
    }


def _best_highlight(hit: dict[str, Any]) -> str:
    highlights = hit.get("highlights") or []
    by_field = {item.get("field"): item for item in highlights}
    for field in ("subject", "description_text", "search_text"):
        item = by_field.get(field)
        if item and item.get("snippet"):
            return item["snippet"]
        if item and item.get("value"):
            return item["value"]
    return ""


def _build_filter_by(
    product: str | None = None,
    tags: list[str] | None = None,
    status: int | None = None,
    priority: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
) -> list[str]:
    filters = []
    if product:
        filters.append(f"product_label:={_literal(product)}")
    if tags:
        tag_filters = [_literal(tag) for tag in tags if tag]
        if tag_filters:
            filters.append(f"tags:=[{', '.join(tag_filters)}]")
    if status is not None:
        filters.append(f"status:={int(status)}")
    if priority is not None:
        filters.append(f"priority:={int(priority)}")

    created_range = _range_filter("created_at_ts", created_from, created_to)
    if created_range:
        filters.append(created_range)
    updated_range = _range_filter("updated_at_ts", updated_from, updated_to)
    if updated_range:
        filters.append(updated_range)
    return filters


def _range_filter(field: str, start: str | None, end: str | None) -> str:
    start_ts = _parse_date_ts(start, end_of_day=False) if start else None
    end_ts = _parse_date_ts(end, end_of_day=True) if end else None
    if start_ts is None and end_ts is None:
        return ""
    if start_ts is None:
        return f"{field}:<={end_ts}"
    if end_ts is None:
        return f"{field}:>={start_ts}"
    return f"{field}:[{start_ts}..{end_ts}]"


def _parse_date_ts(value: str, end_of_day: bool) -> int:
    if "T" in value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed_date = date.fromisoformat(value)
        parsed = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            23 if end_of_day else 0,
            59 if end_of_day else 0,
            59 if end_of_day else 0,
            tzinfo=timezone.utc,
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _literal(value: str) -> str:
    escaped = value.replace("`", "\\`")
    return f"`{escaped}`"


def _timestamp(value: Any) -> int:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _clip(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]
