from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .db import Database
from .freshdesk import FreshdeskClient


DEFAULT_ATTACHMENT_DIR = "data/attachments"
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
REDACTED_QUERY_MARKER = "[REDACTED_QUERY]"
CID_RE = re.compile(r"\[cid:([^\]]+)\]", re.IGNORECASE)


@dataclass(frozen=True)
class AttachmentDownloadResult:
    metadata_count: int
    attempted: int
    downloaded: int
    failed: int
    skipped: int
    bytes_written: int


class AttachmentDownloader:
    def __init__(self, client: FreshdeskClient, database: Database, root: Path | str) -> None:
        self.client = client
        self.database = database
        self.root = Path(root).expanduser().resolve()
        self._fresh_attachment_cache: dict[int, dict[tuple[Any, ...], dict[str, Any]]] = {}

    def run(
        self,
        max_attachments: int | None = None,
        ticket_id: int | None = None,
        max_tickets: int | None = None,
        force: bool = False,
    ) -> AttachmentDownloadResult:
        metadata_count = self.database.rebuild_attachment_metadata(
            ticket_id=ticket_id,
            max_tickets=max_tickets,
        )
        metadata_count += self._refresh_inline_image_metadata(
            ticket_id=ticket_id,
            max_attachments=max_attachments,
            max_tickets=max_tickets,
        )
        rows = self.database.iter_attachments_to_download(
            limit=max_attachments,
            ticket_id=ticket_id,
            max_tickets=max_tickets,
            force=force,
        )

        attempted = 0
        downloaded = 0
        failed = 0
        skipped = 0
        bytes_written = 0
        for row in rows:
            attempted += 1
            url = self._download_url(row)
            if not _is_downloadable_url(url):
                skipped += 1
                self.database.mark_attachment_error(row["id"], "Attachment URL is not downloadable")
                continue

            target_path = attachment_target_path(self.root, row)
            relative_path = str(target_path.relative_to(self.root))
            try:
                result = self.client.download_to_path(url, target_path)
            except Exception as exc:
                failed += 1
                self.database.mark_attachment_error(row["id"], str(exc))
                continue

            downloaded += 1
            bytes_written += result.bytes_written
            self.database.mark_attachment_downloaded(
                row["id"],
                relative_path,
                result.bytes_written,
                result.sha256,
                result.content_type,
            )

        return AttachmentDownloadResult(
            metadata_count=metadata_count,
            attempted=attempted,
            downloaded=downloaded,
            failed=failed,
            skipped=skipped,
            bytes_written=bytes_written,
        )

    def _download_url(self, row: dict[str, Any]) -> str:
        live_attachment = self._fresh_attachment(row)
        if live_attachment:
            return live_attachment.get("attachment_url") or live_attachment.get("url") or ""
        return row.get("remote_url") or ""

    def _fresh_attachment(self, row: dict[str, Any]) -> dict[str, Any] | None:
        ticket_id = int(row["ticket_freshdesk_id"])
        if ticket_id not in self._fresh_attachment_cache:
            self._fresh_attachment_cache[ticket_id] = self._load_fresh_ticket_attachments(ticket_id)
        cache = self._fresh_attachment_cache[ticket_id]

        attachment_id = row.get("freshdesk_attachment_id")
        if attachment_id is not None:
            by_id = cache.get(("attachment_id", int(attachment_id)))
            if by_id:
                return by_id
        if row.get("source") == "inline_image":
            return cache.get(
                (
                    "inline_image",
                    int(row["conversation_freshdesk_id"]),
                    int(row["attachment_index"]),
                )
            )
        return cache.get(
            (
                "conversation_index",
                int(row["conversation_freshdesk_id"]),
                int(row["attachment_index"]),
            )
        )

    def _load_fresh_ticket_attachments(self, ticket_id: int) -> dict[tuple[Any, ...], dict[str, Any]]:
        attachments: dict[tuple[Any, ...], dict[str, Any]] = {}
        for conversation in self.client.iter_conversations(ticket_id):
            conversation_id = int(conversation["id"])
            for index, attachment in enumerate(conversation.get("attachments") or []):
                attachments[("conversation_index", conversation_id, index)] = attachment
                attachment_id = attachment.get("id")
                if attachment_id is not None:
                    attachments[("attachment_id", int(attachment_id))] = attachment
            for inline_image in extract_inline_images(conversation):
                attachments[
                    (
                        "inline_image",
                        conversation_id,
                        inline_image["attachment_index"],
                    )
                ] = inline_image
        return attachments

    def _refresh_inline_image_metadata(
        self,
        ticket_id: int | None,
        max_attachments: int | None,
        max_tickets: int | None,
    ) -> int:
        count = 0
        ticket_ids = self.database.iter_inline_image_candidate_ticket_ids(
            ticket_id=ticket_id,
            limit=max_attachments if ticket_id is None and max_tickets is None else None,
            max_tickets=max_tickets,
        )
        for candidate_ticket_id in ticket_ids:
            inline_by_conversation: dict[int, list[dict[str, Any]]] = {}
            for conversation in self.client.iter_conversations(candidate_ticket_id):
                images = extract_inline_images(conversation)
                if images:
                    inline_by_conversation[int(conversation["id"])] = images
            if inline_by_conversation:
                count += self.database.upsert_inline_image_metadata(
                    candidate_ticket_id,
                    inline_by_conversation,
                )
        return count


class InlineImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        data = {key.lower(): value or "" for key, value in attrs}
        src = data.get("src", "").strip()
        if src:
            self.images.append(data)


def extract_inline_images(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    parser = InlineImageParser()
    parser.feed(conversation.get("body") or "")
    cids = _extract_cid_filenames(conversation.get("body_text") or "")
    images = []
    for index, image in enumerate(parser.images):
        src = image.get("src", "")
        if not _is_downloadable_url(src):
            continue
        filename = cids[index] if index < len(cids) else _inline_image_filename(index, image)
        images.append(
            {
                "attachment_index": index,
                "attachment_url": src,
                "safe_remote_url": _strip_query(src),
                "name": filename,
                "content_type": _content_type_from_filename(filename),
                "content_id": cids[index] if index < len(cids) else image.get("alt") or None,
            }
        )
    return images


def _extract_cid_filenames(text: str) -> list[str]:
    filenames = []
    for match in CID_RE.finditer(text):
        value = match.group(1).strip().strip("<>")
        if "@" in value:
            value = value.split("@", 1)[0]
        if value:
            filenames.append(safe_filename(value))
    return filenames


def _inline_image_filename(index: int, image: dict[str, str]) -> str:
    alt = image.get("alt", "").strip()
    if alt and not _is_removed_sender_label(alt):
        return safe_filename(alt)
    return f"inline-image-{index + 1}.img"


def _content_type_from_filename(filename: str) -> str | None:
    suffix = Path(filename).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    return None


def _strip_query(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def attachment_target_path(root: Path, row: dict[str, Any]) -> Path:
    attachment_key = row.get("freshdesk_attachment_id") or row.get("id") or row["attachment_index"]
    return (
        root
        / f"ticket-{int(row['ticket_freshdesk_id'])}"
        / f"conversation-{int(row['conversation_freshdesk_id'])}"
        / f"attachment-{attachment_key}"
        / safe_filename(row.get("filename") or "attachment")
    )


def safe_filename(filename: str, max_length: int = 180) -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", filename).strip(" ._")
    if not cleaned:
        cleaned = "attachment"
    if cleaned.startswith("."):
        cleaned = f"attachment{cleaned}"
    if len(cleaned) <= max_length:
        return cleaned

    stem, suffix = _split_suffix(cleaned)
    suffix = suffix[:32]
    stem_limit = max(max_length - len(suffix), 1)
    return f"{stem[:stem_limit]}{suffix}"


def _split_suffix(filename: str) -> tuple[str, str]:
    path = Path(filename)
    suffix = path.suffix
    if not suffix:
        return filename, ""
    return filename[: -len(suffix)], suffix


def _is_downloadable_url(url: str) -> bool:
    if not url or REDACTED_QUERY_MARKER in url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def _is_removed_sender_label(value: str) -> bool:
    return value.strip().lower() == "image removed by sender"
