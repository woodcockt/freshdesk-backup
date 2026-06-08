from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .db import Database
from .freshdesk import FreshdeskClient
from .redaction import Redactor
from .transform import normalize_conversation, normalize_ticket


@dataclass
class SyncResult:
    tickets: int = 0
    conversations: int = 0
    last_updated_at: str | None = None


class SyncService:
    def __init__(
        self,
        client: FreshdeskClient,
        database: Database,
        redactor: Redactor,
        default_since: str,
        per_page: int,
    ) -> None:
        self.client = client
        self.database = database
        self.redactor = redactor
        self.default_since = default_since
        self.per_page = per_page

    def run(self, since: str | None = None, max_tickets: int | None = None) -> SyncResult:
        self.database.apply_migrations()
        self.database.mark_sync_started()
        result = SyncResult()

        try:
            fields = self.client.list_ticket_fields()
            self.database.upsert_ticket_fields(self.redactor.redact_json(fields))

            saved_cursor = self.database.get_sync_cursor()
            cursor = since or saved_cursor or self.default_since
            skip_existing_boundary = since is None and saved_cursor is not None
            cursor_dt = _parse_freshdesk_datetime(cursor) if skip_existing_boundary else None
            for raw_ticket in self.client.iter_tickets(cursor, per_page=self.per_page):
                if (
                    skip_existing_boundary
                    and cursor_dt is not None
                    and _ticket_at_or_before_cursor(raw_ticket, cursor_dt)
                    and self.database.ticket_exists(raw_ticket["id"])
                ):
                    continue

                ticket = normalize_ticket(raw_ticket, self.redactor)
                conversations = [
                    normalize_conversation(raw_conversation, self.redactor)
                    for raw_conversation in self.client.iter_conversations(raw_ticket["id"])
                ]
                self.database.upsert_ticket_with_conversations(ticket, conversations)
                result.tickets += 1
                result.conversations += len(conversations)
                if raw_ticket.get("updated_at"):
                    result.last_updated_at = raw_ticket["updated_at"]
                if max_tickets is not None and result.tickets >= max_tickets:
                    break

            self.database.mark_sync_success(
                result.last_updated_at,
                result.tickets,
                result.conversations,
            )
            return result
        except Exception as exc:
            self.database.mark_sync_error(str(exc))
            raise


def _ticket_at_or_before_cursor(ticket: dict, cursor_dt: datetime) -> bool:
    updated_at = ticket.get("updated_at")
    if not updated_at:
        return False
    return _parse_freshdesk_datetime(updated_at) <= cursor_dt


def _parse_freshdesk_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
