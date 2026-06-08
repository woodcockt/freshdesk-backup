from __future__ import annotations

import base64
import hashlib
import json
import ssl
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, Request, build_opener


UrlOpen = Callable[[Request, float], Any]


def default_urlopen(request: Request, timeout: float) -> Any:
    try:
        import truststore

        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        try:
            import certifi

            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
    opener = build_opener(HTTPSHandler(context=context))
    return opener.open(request, timeout=timeout)


class FreshdeskError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedFile:
    bytes_written: int
    sha256: str
    content_type: str | None = None


@dataclass(frozen=True)
class FreshdeskClient:
    domain: str
    api_key: str
    timeout: float = 30.0
    max_retries: int = 4
    urlopen_impl: UrlOpen = default_urlopen

    def list_ticket_fields(self) -> list[dict[str, Any]]:
        return self.get_json("/api/v2/ticket_fields")

    def iter_tickets(
        self,
        updated_since: str,
        per_page: int = 100,
        max_pages: int = 300,
    ) -> Iterator[dict[str, Any]]:
        for page in range(1, max_pages + 1):
            payload = self.get_json(
                "/api/v2/tickets",
                {
                    "updated_since": updated_since,
                    "order_by": "updated_at",
                    "order_type": "asc",
                    "include": "description,requester,stats",
                    "per_page": per_page,
                    "page": page,
                },
            )
            if not payload:
                return
            if not isinstance(payload, list):
                raise FreshdeskError("Expected ticket list response")
            yield from payload
            if len(payload) < per_page:
                return

        raise FreshdeskError(
            f"Freshdesk returned {max_pages} full pages from {updated_since}; "
            "rerun sync after the saved cursor advances, or reduce the sync window."
        )

    def iter_conversations(self, ticket_id: int) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            payload = self.get_json(
                f"/api/v2/tickets/{ticket_id}/conversations",
                {"page": page},
            )
            if not payload:
                return
            if not isinstance(payload, list):
                raise FreshdeskError("Expected conversation list response")
            yield from payload
            if len(payload) < 30:
                return
            page += 1

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"https://{self.domain}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        request = Request(
            url,
            headers={
                "Authorization": self._auth_header(),
                "Content-Type": "application/json",
            },
            method="GET",
        )
        return self._send_json(request)

    def download_to_path(
        self,
        url: str,
        target_path: Path,
        authenticate: bool = False,
    ) -> DownloadedFile:
        headers = {"Authorization": self._auth_header()} if authenticate else {}
        request = Request(url, headers=headers, method="GET")
        return self._send_binary(request, target_path)

    def _send_json(self, request: Request) -> Any:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                with self.urlopen_impl(request, timeout=self.timeout) as response:
                    body = response.read()
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    if remaining is not None and remaining.isdigit() and int(remaining) <= 1:
                        time.sleep(1)
                    if not body:
                        return None
                    return json.loads(body.decode("utf-8"))
            except HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after else delay)
                    delay *= 2
                    continue
                if 500 <= exc.code < 600 and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise FreshdeskError(f"Freshdesk HTTP {exc.code}: {detail}") from exc

        raise FreshdeskError("Freshdesk request failed after retries")

    def _send_binary(self, request: Request, target_path: Path) -> DownloadedFile:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = target_path.with_name(f"{target_path.name}.part")
                digest = hashlib.sha256()
                bytes_written = 0
                with self.urlopen_impl(request, timeout=self.timeout) as response:
                    with tmp_path.open("wb") as handle:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            digest.update(chunk)
                            bytes_written += len(chunk)
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    if remaining is not None and remaining.isdigit() and int(remaining) <= 1:
                        time.sleep(1)
                    tmp_path.replace(target_path)
                    return DownloadedFile(
                        bytes_written=bytes_written,
                        sha256=digest.hexdigest(),
                        content_type=response.headers.get("Content-Type"),
                    )
            except HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after else delay)
                    delay *= 2
                    continue
                if 500 <= exc.code < 600 and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise FreshdeskError(f"Freshdesk HTTP {exc.code}: {detail}") from exc

        raise FreshdeskError("Freshdesk download failed after retries")

    def _auth_header(self) -> str:
        credentials = f"{self.api_key}:X".encode("utf-8")
        auth = base64.b64encode(credentials).decode("ascii")
        return f"Basic {auth}"
