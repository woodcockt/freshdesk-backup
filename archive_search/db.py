from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ROOT


MIGRATIONS_DIR = ROOT / "migrations"


class Database:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Install dependencies first: pip install -r requirements.txt") from exc

        self._psycopg = psycopg
        self._dict_row = dict_row
        self.database_url = database_url

    def connect(self):
        return self._psycopg.connect(self.database_url, row_factory=self._dict_row)

    def apply_migrations(self) -> None:
        with self.connect() as conn:
            conn.autocommit = True
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.name
                if version in applied:
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))

    def upsert_ticket_fields(self, fields: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                for field in fields:
                    cur.execute(
                        """
                        INSERT INTO ticket_field_metadata (
                            freshdesk_id, name, label, field_type, is_default, choices, raw
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (name) DO UPDATE SET
                            freshdesk_id = EXCLUDED.freshdesk_id,
                            label = EXCLUDED.label,
                            field_type = EXCLUDED.field_type,
                            is_default = EXCLUDED.is_default,
                            choices = EXCLUDED.choices,
                            raw = EXCLUDED.raw,
                            updated_at = now()
                        """,
                        (
                            field.get("id"),
                            field.get("name"),
                            field.get("label"),
                            field.get("type"),
                            field.get("default"),
                            json.dumps(field.get("choices")),
                            json.dumps(field),
                        ),
                    )

    def get_sync_cursor(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_updated_at FROM sync_state WHERE id = 'freshdesk_tickets'"
            ).fetchone()
            if not row or row["last_updated_at"] is None:
                return None
            return row["last_updated_at"].isoformat().replace("+00:00", "Z")

    def ticket_exists(self, freshdesk_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM tickets WHERE freshdesk_id = %s) AS exists",
                (freshdesk_id,),
            ).fetchone()
            return bool(row["exists"])

    def mark_sync_started(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (id, last_run_started_at)
                VALUES ('freshdesk_tickets', now())
                ON CONFLICT (id) DO UPDATE SET
                    last_run_started_at = now(),
                    last_error = NULL
                """
            )

    def mark_sync_error(self, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (id, last_error, error_count)
                VALUES ('freshdesk_tickets', %s, 1)
                ON CONFLICT (id) DO UPDATE SET
                    last_error = EXCLUDED.last_error,
                    error_count = sync_state.error_count + 1
                """,
                (error[:1000],),
            )

    def mark_sync_success(
        self,
        last_updated_at: str | None,
        ticket_count: int,
        conversation_count: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    id, last_updated_at, last_success_at, total_tickets,
                    total_conversations, last_error
                )
                VALUES ('freshdesk_tickets', %s, now(), %s, %s, NULL)
                ON CONFLICT (id) DO UPDATE SET
                    last_updated_at = COALESCE(EXCLUDED.last_updated_at, sync_state.last_updated_at),
                    last_success_at = now(),
                    total_tickets = sync_state.total_tickets + EXCLUDED.total_tickets,
                    total_conversations = sync_state.total_conversations + EXCLUDED.total_conversations,
                    last_error = NULL
                """,
                (last_updated_at, ticket_count, conversation_count),
            )

    def upsert_ticket_with_conversations(
        self,
        ticket: dict[str, Any],
        conversations: list[dict[str, Any]],
    ) -> None:
        with self.connect() as conn:
            self._upsert_ticket(conn, ticket)
            seen_conversation_ids = []
            for conversation in conversations:
                seen_conversation_ids.append(conversation["freshdesk_id"])
                self._upsert_conversation(conn, conversation)
            if seen_conversation_ids:
                conn.execute(
                    """
                    DELETE FROM ticket_conversations
                    WHERE ticket_freshdesk_id = %s
                      AND freshdesk_id <> ALL(%s)
                    """,
                    (ticket["freshdesk_id"], seen_conversation_ids),
                )

    def search(
        self,
        query: str | None,
        limit: int = 10,
        product: str | None = None,
        tags: list[str] | None = None,
        status: int | None = None,
        priority: int | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        updated_from: str | None = None,
        updated_to: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = []
        params: list[Any] = []
        if product:
            filters.append("product_label ILIKE %s")
            params.append(f"%{product}%")
        if tags:
            filters.append("tags && %s::text[]")
            params.append(tags)
        if status is not None:
            filters.append("status = %s")
            params.append(status)
        if priority is not None:
            filters.append("priority = %s")
            params.append(priority)
        if created_from:
            filters.append("created_at >= %s")
            params.append(created_from)
        if created_to:
            filters.append("created_at <= %s")
            params.append(created_to)
        if updated_from:
            filters.append("updated_at >= %s")
            params.append(updated_from)
        if updated_to:
            filters.append("updated_at <= %s")
            params.append(updated_to)

        where = " AND ".join(filters) if filters else "true"
        with self.connect() as conn:
            if query:
                sql = f"""
                    WITH q AS (
                        SELECT websearch_to_tsquery('english', unaccent(%s)) AS query
                    )
                    SELECT
                        freshdesk_id,
                        subject,
                        product_label,
                        tags,
                        status,
                        priority,
                        created_at,
                        updated_at,
                        ts_rank_cd(search_vector, q.query) AS rank,
                        ts_headline(
                            'english',
                            search_text,
                            q.query,
                            'StartSel=<<, StopSel=>>, MaxWords=35, MinWords=12'
                        ) AS excerpt
                    FROM ticket_search_documents, q
                    WHERE {where}
                      AND search_vector @@ q.query
                    ORDER BY rank DESC, updated_at DESC NULLS LAST
                    LIMIT %s
                """
                return conn.execute(sql, [query, *params, limit]).fetchall()

            sql = f"""
                SELECT
                    freshdesk_id,
                    subject,
                    product_label,
                    tags,
                    status,
                    priority,
                    created_at,
                    updated_at,
                    0::float AS rank,
                    left(search_text, 280) AS excerpt
                FROM ticket_search_documents
                WHERE {where}
                ORDER BY updated_at DESC NULLS LAST
                LIMIT %s
            """
            return conn.execute(sql, [*params, limit]).fetchall()

    def show_ticket(self, freshdesk_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            ticket = conn.execute(
                "SELECT * FROM tickets WHERE freshdesk_id = %s",
                (freshdesk_id,),
            ).fetchone()
            if not ticket:
                return None
            conversations = conn.execute(
                """
                SELECT *
                FROM ticket_conversations
                WHERE ticket_freshdesk_id = %s
                ORDER BY created_at ASC NULLS LAST, freshdesk_id ASC
                """,
                (freshdesk_id,),
            ).fetchall()
            return {"ticket": ticket, "conversations": conversations}

    def _upsert_ticket(self, conn, ticket: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO tickets (
                freshdesk_id, subject, description_text, description_html,
                structured_description, created_at, updated_at, due_by, fr_due_by,
                nr_due_by, closed_at, resolved_at, first_responded_at, status,
                priority, source, type, product_id, product_label, group_id,
                requester_id, responder_id, company_id, support_email_hash,
                requester_email_hash, requester_phone_hash, requester_name_hash,
                tags, custom_fields, stats, raw, synced_at
            )
            VALUES (
                %(freshdesk_id)s, %(subject)s, %(description_text)s,
                %(description_html)s, %(structured_description)s::jsonb,
                %(created_at)s, %(updated_at)s, %(due_by)s, %(fr_due_by)s,
                %(nr_due_by)s, %(closed_at)s, %(resolved_at)s,
                %(first_responded_at)s, %(status)s, %(priority)s, %(source)s,
                %(type)s, %(product_id)s, %(product_label)s, %(group_id)s,
                %(requester_id)s, %(responder_id)s, %(company_id)s,
                %(support_email_hash)s, %(requester_email_hash)s,
                %(requester_phone_hash)s, %(requester_name_hash)s,
                %(tags)s, %(custom_fields)s::jsonb, %(stats)s::jsonb,
                %(raw)s::jsonb, now()
            )
            ON CONFLICT (freshdesk_id) DO UPDATE SET
                subject = EXCLUDED.subject,
                description_text = EXCLUDED.description_text,
                description_html = EXCLUDED.description_html,
                structured_description = EXCLUDED.structured_description,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                due_by = EXCLUDED.due_by,
                fr_due_by = EXCLUDED.fr_due_by,
                nr_due_by = EXCLUDED.nr_due_by,
                closed_at = EXCLUDED.closed_at,
                resolved_at = EXCLUDED.resolved_at,
                first_responded_at = EXCLUDED.first_responded_at,
                status = EXCLUDED.status,
                priority = EXCLUDED.priority,
                source = EXCLUDED.source,
                type = EXCLUDED.type,
                product_id = EXCLUDED.product_id,
                product_label = EXCLUDED.product_label,
                group_id = EXCLUDED.group_id,
                requester_id = EXCLUDED.requester_id,
                responder_id = EXCLUDED.responder_id,
                company_id = EXCLUDED.company_id,
                support_email_hash = EXCLUDED.support_email_hash,
                requester_email_hash = EXCLUDED.requester_email_hash,
                requester_phone_hash = EXCLUDED.requester_phone_hash,
                requester_name_hash = EXCLUDED.requester_name_hash,
                tags = EXCLUDED.tags,
                custom_fields = EXCLUDED.custom_fields,
                stats = EXCLUDED.stats,
                raw = EXCLUDED.raw,
                synced_at = now()
            """,
            _json_params(ticket),
        )

    def _upsert_conversation(self, conn, conversation: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO ticket_conversations (
                freshdesk_id, ticket_freshdesk_id, body_text, body_html, private,
                incoming, source, user_id, support_email_hash, from_email_hash,
                to_email_hashes, cc_email_hashes, bcc_email_hashes,
                attachment_count, attachments, created_at, updated_at,
                last_edited_at, last_edited_user_id, raw, synced_at
            )
            VALUES (
                %(freshdesk_id)s, %(ticket_freshdesk_id)s, %(body_text)s,
                %(body_html)s, %(private)s, %(incoming)s, %(source)s,
                %(user_id)s, %(support_email_hash)s, %(from_email_hash)s,
                %(to_email_hashes)s, %(cc_email_hashes)s, %(bcc_email_hashes)s,
                %(attachment_count)s, %(attachments)s::jsonb, %(created_at)s,
                %(updated_at)s, %(last_edited_at)s, %(last_edited_user_id)s,
                %(raw)s::jsonb, now()
            )
            ON CONFLICT (freshdesk_id) DO UPDATE SET
                ticket_freshdesk_id = EXCLUDED.ticket_freshdesk_id,
                body_text = EXCLUDED.body_text,
                body_html = EXCLUDED.body_html,
                private = EXCLUDED.private,
                incoming = EXCLUDED.incoming,
                source = EXCLUDED.source,
                user_id = EXCLUDED.user_id,
                support_email_hash = EXCLUDED.support_email_hash,
                from_email_hash = EXCLUDED.from_email_hash,
                to_email_hashes = EXCLUDED.to_email_hashes,
                cc_email_hashes = EXCLUDED.cc_email_hashes,
                bcc_email_hashes = EXCLUDED.bcc_email_hashes,
                attachment_count = EXCLUDED.attachment_count,
                attachments = EXCLUDED.attachments,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                last_edited_at = EXCLUDED.last_edited_at,
                last_edited_user_id = EXCLUDED.last_edited_user_id,
                raw = EXCLUDED.raw,
                synced_at = now()
            """,
            _json_params(conversation),
        )


def _json_params(values: dict[str, Any]) -> dict[str, Any]:
    json_keys = {
        "attachments",
        "custom_fields",
        "raw",
        "stats",
        "structured_description",
    }
    return {
        key: json.dumps(value) if key in json_keys else value
        for key, value in values.items()
    }
