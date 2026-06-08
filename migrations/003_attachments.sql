CREATE TABLE IF NOT EXISTS ticket_attachments (
    id bigserial PRIMARY KEY,
    freshdesk_attachment_id bigint,
    ticket_freshdesk_id bigint NOT NULL REFERENCES tickets(freshdesk_id) ON DELETE CASCADE,
    conversation_freshdesk_id bigint NOT NULL REFERENCES ticket_conversations(freshdesk_id)
        ON DELETE CASCADE,
    attachment_index integer NOT NULL,
    filename text NOT NULL DEFAULT 'attachment',
    content_type text,
    size_bytes bigint,
    remote_url text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    local_path text,
    local_size_bytes bigint,
    sha256 text,
    downloaded_at timestamptz,
    download_error text,
    created_at timestamptz,
    updated_at timestamptz,
    synced_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_freshdesk_id, attachment_index)
);

CREATE INDEX IF NOT EXISTS ticket_attachments_ticket_idx
    ON ticket_attachments(ticket_freshdesk_id);
CREATE INDEX IF NOT EXISTS ticket_attachments_conversation_idx
    ON ticket_attachments(conversation_freshdesk_id);
CREATE INDEX IF NOT EXISTS ticket_attachments_downloaded_idx
    ON ticket_attachments(downloaded_at)
    WHERE downloaded_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS ticket_attachments_pending_idx
    ON ticket_attachments(id)
    WHERE local_path IS NULL AND remote_url IS NOT NULL;
