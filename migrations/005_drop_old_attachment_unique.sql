ALTER TABLE ticket_attachments
    DROP CONSTRAINT IF EXISTS ticket_attachments_conversation_freshdesk_id_attachment_index_key;
ALTER TABLE ticket_attachments
    DROP CONSTRAINT IF EXISTS ticket_attachments_conversation_freshdesk_id_attachment_ind_key;
