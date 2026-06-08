ALTER TABLE ticket_attachments
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'attachment',
    ADD COLUMN IF NOT EXISTS content_id text;

ALTER TABLE ticket_attachments
    DROP CONSTRAINT IF EXISTS ticket_attachments_conversation_freshdesk_id_attachment_index_key;
ALTER TABLE ticket_attachments
    DROP CONSTRAINT IF EXISTS ticket_attachments_conversation_freshdesk_id_attachment_ind_key;

CREATE UNIQUE INDEX IF NOT EXISTS ticket_attachments_conversation_source_index_idx
    ON ticket_attachments(conversation_freshdesk_id, source, attachment_index);

CREATE INDEX IF NOT EXISTS ticket_attachments_source_idx
    ON ticket_attachments(source);
