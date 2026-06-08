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

    def refresh_search_documents(self) -> None:
        with self.connect() as conn:
            conn.execute("REFRESH MATERIALIZED VIEW ticket_search_documents")

    def iter_typesense_documents(self, batch_size: int = 500):
        sql = """
            SELECT
                d.freshdesk_id,
                d.subject,
                d.description_text,
                d.product_label,
                d.tags,
                d.status,
                d.priority,
                d.created_at,
                d.updated_at,
                d.search_text,
                COALESCE(a.attachment_count, 0) AS attachment_count
            FROM ticket_search_documents d
            LEFT JOIN (
                SELECT ticket_freshdesk_id, SUM(attachment_count)::integer AS attachment_count
                FROM ticket_conversations
                GROUP BY ticket_freshdesk_id
            ) a ON a.ticket_freshdesk_id = d.freshdesk_id
            ORDER BY d.freshdesk_id
        """
        with self.connect() as conn:
            with conn.cursor(name="typesense_documents") as cur:
                cur.execute(sql)
                while True:
                    rows = cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield rows

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

    def rebuild_attachment_metadata(
        self,
        ticket_id: int | None = None,
        max_tickets: int | None = None,
    ) -> int:
        count = 0
        filters = ["attachment_count > 0"]
        params: list[Any] = []
        if ticket_id is not None:
            filters.append("ticket_freshdesk_id = %s")
            params.append(ticket_id)
        elif max_tickets is not None:
            filters.append(
                """
                ticket_freshdesk_id IN (
                    SELECT freshdesk_id
                    FROM tickets
                    ORDER BY freshdesk_id
                    LIMIT %s
                )
                """
            )
            params.append(max_tickets)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT freshdesk_id, ticket_freshdesk_id, attachments
                FROM ticket_conversations
                WHERE {" AND ".join(filters)}
                ORDER BY ticket_freshdesk_id, freshdesk_id
                """,
                params,
            ).fetchall()
            for row in rows:
                count += self._upsert_attachment_rows(
                    conn,
                    row["freshdesk_id"],
                    row["ticket_freshdesk_id"],
                    row["attachments"] or [],
                )
        return count

    def iter_inline_image_candidate_ticket_ids(
        self,
        ticket_id: int | None = None,
        limit: int | None = None,
        max_tickets: int | None = None,
    ) -> list[int]:
        filters = [
            """
            EXISTS (
                SELECT 1
                FROM ticket_conversations c
                WHERE c.ticket_freshdesk_id = t.freshdesk_id
                  AND (
                    c.body_text ILIKE '%%[cid:%%'
                    OR c.body_html ILIKE '%%<img%%'
                    OR c.body_html ILIKE '%%attachment.freshdesk.com/inline/attachment%%'
                  )
            )
            """
        ]
        params: list[Any] = []
        if ticket_id is not None:
            filters.append("t.freshdesk_id = %s")
            params.append(ticket_id)
        elif max_tickets is not None:
            filters.append(
                """
                t.freshdesk_id IN (
                    SELECT freshdesk_id
                    FROM tickets
                    ORDER BY freshdesk_id
                    LIMIT %s
                )
                """
            )
            params.append(max_tickets)
        sql = f"""
            SELECT t.freshdesk_id
            FROM tickets t
            WHERE {" AND ".join(filters)}
            ORDER BY t.freshdesk_id
        """
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            return [int(row["freshdesk_id"]) for row in conn.execute(sql, params).fetchall()]

    def upsert_inline_image_metadata(
        self,
        ticket_id: int,
        images_by_conversation: dict[int, list[dict[str, Any]]],
    ) -> int:
        count = 0
        with self.connect() as conn:
            for conversation_id, images in images_by_conversation.items():
                count += self._upsert_attachment_rows(
                    conn,
                    conversation_id,
                    ticket_id,
                    images,
                    source="inline_image",
                )
        return count

    def iter_attachments_to_download(
        self,
        limit: int | None = None,
        ticket_id: int | None = None,
        max_tickets: int | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        filters = ["((remote_url IS NOT NULL AND remote_url <> '') OR source = 'inline_image')"]
        params: list[Any] = []
        if not force:
            filters.append("local_path IS NULL")
        if ticket_id is not None:
            filters.append("ticket_freshdesk_id = %s")
            params.append(ticket_id)
        elif max_tickets is not None:
            filters.append(
                """
                ticket_freshdesk_id IN (
                    SELECT freshdesk_id
                    FROM tickets
                    ORDER BY freshdesk_id
                    LIMIT %s
                )
                """
            )
            params.append(max_tickets)
        sql = f"""
            SELECT *
            FROM ticket_attachments
            WHERE {" AND ".join(filters)}
            ORDER BY ticket_freshdesk_id, conversation_freshdesk_id, attachment_index
        """
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def get_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM ticket_attachments WHERE id = %s",
                (attachment_id,),
            ).fetchone()

    def mark_attachment_downloaded(
        self,
        attachment_id: int,
        local_path: str,
        local_size_bytes: int,
        sha256: str,
        content_type: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ticket_attachments
                SET local_path = %s,
                    local_size_bytes = %s,
                    sha256 = %s,
                    content_type = COALESCE(%s, content_type),
                    downloaded_at = now(),
                    download_error = NULL,
                    synced_at = now()
                WHERE id = %s
                """,
                (local_path, local_size_bytes, sha256, content_type, attachment_id),
            )

    def mark_attachment_error(self, attachment_id: int, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ticket_attachments
                SET download_error = %s,
                    synced_at = now()
                WHERE id = %s
                """,
                (error[:1000], attachment_id),
            )

    def get_attachment_summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT
                    count(*) AS attachment_count,
                    count(*) FILTER (WHERE local_path IS NOT NULL) AS downloaded_count,
                    count(*) FILTER (WHERE local_path IS NULL AND remote_url IS NOT NULL)
                        AS pending_count,
                    count(*) FILTER (WHERE download_error IS NOT NULL) AS error_count,
                    coalesce(sum(size_bytes), 0) AS remote_size_bytes,
                    coalesce(sum(local_size_bytes), 0) AS local_size_bytes
                FROM ticket_attachments
                """
            ).fetchone()

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
            attachments = conn.execute(
                """
                SELECT *
                FROM ticket_attachments
                WHERE ticket_freshdesk_id = %s
                ORDER BY conversation_freshdesk_id, attachment_index
                """,
                (freshdesk_id,),
            ).fetchall()
            attachments_by_conversation: dict[int, list[dict[str, Any]]] = {}
            for attachment in attachments:
                attachments_by_conversation.setdefault(
                    attachment["conversation_freshdesk_id"],
                    [],
                ).append(attachment)
            for conversation in conversations:
                conversation["downloaded_attachments"] = attachments_by_conversation.get(
                    conversation["freshdesk_id"],
                    [],
                )
            return {"ticket": ticket, "conversations": conversations}

    def get_filter_options(self) -> dict[str, Any]:
        with self.connect() as conn:
            products = conn.execute(
                """
                SELECT product_label AS value, count(*) AS count
                FROM tickets
                WHERE product_label IS NOT NULL AND product_label <> ''
                GROUP BY product_label
                ORDER BY count DESC, product_label ASC
                LIMIT 100
                """
            ).fetchall()
            tags = conn.execute(
                """
                SELECT tag AS value, count(*) AS count
                FROM tickets, unnest(tags) AS tag
                WHERE tag <> ''
                GROUP BY tag
                ORDER BY count DESC, tag ASC
                LIMIT 100
                """
            ).fetchall()
            status_counts = conn.execute(
                """
                SELECT status AS value, count(*) AS count
                FROM tickets
                WHERE status IS NOT NULL
                GROUP BY status
                ORDER BY status ASC
                """
            ).fetchall()
            priority_counts = conn.execute(
                """
                SELECT priority AS value, count(*) AS count
                FROM tickets
                WHERE priority IS NOT NULL
                GROUP BY priority
                ORDER BY priority ASC
                """
            ).fetchall()
            summary = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM tickets) AS ticket_count,
                    (SELECT count(*) FROM ticket_conversations) AS conversation_count,
                    (SELECT coalesce(sum(attachment_count), 0) FROM ticket_conversations)
                        AS attachment_count
                """
            ).fetchone()
            return {
                "products": products,
                "tags": tags,
                "statuses": status_counts,
                "priorities": priority_counts,
                "summary": summary,
            }

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
        self._upsert_attachment_rows(
            conn,
            conversation["freshdesk_id"],
            conversation["ticket_freshdesk_id"],
            conversation.get("attachments") or [],
        )

    def _upsert_attachment_rows(
        self,
        conn,
        conversation_id: int,
        ticket_id: int,
        attachments: list[dict[str, Any]],
        source: str = "attachment",
    ) -> int:
        seen_indexes = []
        for index, attachment in enumerate(attachments):
            attachment_index = _optional_int(attachment.get("attachment_index"))
            if attachment_index is None:
                attachment_index = index
            seen_indexes.append(attachment_index)
            filename = _attachment_filename(attachment)
            conn.execute(
                """
                INSERT INTO ticket_attachments (
                    freshdesk_attachment_id, ticket_freshdesk_id,
                    conversation_freshdesk_id, attachment_index, source, content_id,
                    filename, content_type, size_bytes, remote_url, metadata,
                    created_at, updated_at, synced_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, now()
                )
                ON CONFLICT (conversation_freshdesk_id, source, attachment_index) DO UPDATE SET
                    freshdesk_attachment_id = EXCLUDED.freshdesk_attachment_id,
                    ticket_freshdesk_id = EXCLUDED.ticket_freshdesk_id,
                    content_id = EXCLUDED.content_id,
                    filename = EXCLUDED.filename,
                    content_type = EXCLUDED.content_type,
                    size_bytes = EXCLUDED.size_bytes,
                    remote_url = EXCLUDED.remote_url,
                    metadata = EXCLUDED.metadata,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    synced_at = now()
                """,
                (
                    _optional_int(attachment.get("id")),
                    ticket_id,
                    conversation_id,
                    attachment_index,
                    source,
                    attachment.get("content_id"),
                    filename,
                    attachment.get("content_type"),
                    _optional_int(attachment.get("size") or attachment.get("file_size")),
                    attachment.get("safe_remote_url")
                    or attachment.get("attachment_url")
                    or attachment.get("url"),
                    json.dumps(attachment),
                    attachment.get("created_at"),
                    attachment.get("updated_at"),
                ),
            )

        if seen_indexes:
            conn.execute(
                """
                DELETE FROM ticket_attachments
                WHERE conversation_freshdesk_id = %s
                  AND source = %s
                  AND attachment_index <> ALL(%s)
                """,
                (conversation_id, source, seen_indexes),
            )
        else:
            conn.execute(
                "DELETE FROM ticket_attachments WHERE conversation_freshdesk_id = %s AND source = %s",
                (conversation_id, source),
            )
        return len(attachments)


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


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attachment_filename(attachment: dict[str, Any]) -> str:
    filename = str(attachment.get("name") or attachment.get("filename") or "attachment").strip()
    return filename or "attachment"
