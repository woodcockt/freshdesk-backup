CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS ticket_field_metadata (
    name text PRIMARY KEY,
    freshdesk_id bigint,
    label text,
    field_type text,
    is_default boolean,
    choices jsonb,
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tickets (
    freshdesk_id bigint PRIMARY KEY,
    subject text NOT NULL DEFAULT '',
    description_text text NOT NULL DEFAULT '',
    description_html text,
    structured_description jsonb,
    created_at timestamptz,
    updated_at timestamptz,
    due_by timestamptz,
    fr_due_by timestamptz,
    nr_due_by timestamptz,
    closed_at timestamptz,
    resolved_at timestamptz,
    first_responded_at timestamptz,
    status integer,
    priority integer,
    source integer,
    type text,
    product_id bigint,
    product_label text,
    group_id bigint,
    requester_id bigint,
    responder_id bigint,
    company_id bigint,
    support_email_hash text,
    requester_email_hash text,
    requester_phone_hash text,
    requester_name_hash text,
    tags text[] NOT NULL DEFAULT '{}',
    custom_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    stats jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    synced_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ticket_conversations (
    freshdesk_id bigint PRIMARY KEY,
    ticket_freshdesk_id bigint NOT NULL REFERENCES tickets(freshdesk_id) ON DELETE CASCADE,
    body_text text NOT NULL DEFAULT '',
    body_html text,
    private boolean,
    incoming boolean,
    source integer,
    user_id bigint,
    support_email_hash text,
    from_email_hash text,
    to_email_hashes text[] NOT NULL DEFAULT '{}',
    cc_email_hashes text[] NOT NULL DEFAULT '{}',
    bcc_email_hashes text[] NOT NULL DEFAULT '{}',
    attachment_count integer NOT NULL DEFAULT 0,
    attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz,
    updated_at timestamptz,
    last_edited_at timestamptz,
    last_edited_user_id bigint,
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    synced_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_state (
    id text PRIMARY KEY,
    last_updated_at timestamptz,
    last_ticket_id bigint,
    last_run_started_at timestamptz,
    last_success_at timestamptz,
    total_tickets bigint NOT NULL DEFAULT 0,
    total_conversations bigint NOT NULL DEFAULT 0,
    error_count bigint NOT NULL DEFAULT 0,
    last_error text
);

CREATE INDEX IF NOT EXISTS tickets_updated_at_idx ON tickets(updated_at);
CREATE INDEX IF NOT EXISTS tickets_created_at_idx ON tickets(created_at);
CREATE INDEX IF NOT EXISTS tickets_product_label_idx ON tickets USING gin (product_label gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tickets_tags_idx ON tickets USING gin (tags);
CREATE INDEX IF NOT EXISTS ticket_conversations_ticket_idx
    ON ticket_conversations(ticket_freshdesk_id, created_at);

CREATE OR REPLACE VIEW ticket_search_documents AS
WITH conversation_text AS (
    SELECT
        ticket_freshdesk_id,
        string_agg(body_text, E'\n\n' ORDER BY created_at NULLS LAST, freshdesk_id) AS conversations_text
    FROM ticket_conversations
    GROUP BY ticket_freshdesk_id
)
SELECT
    t.freshdesk_id,
    t.subject,
    t.description_text,
    t.product_label,
    t.tags,
    t.status,
    t.priority,
    t.created_at,
    t.updated_at,
    concat_ws(
        E'\n\n',
        t.subject,
        t.product_label,
        array_to_string(t.tags, ' '),
        t.description_text,
        c.conversations_text
    ) AS search_text,
    (
        setweight(to_tsvector('english', unaccent(coalesce(t.subject, ''))), 'A') ||
        setweight(to_tsvector('english', unaccent(coalesce(t.product_label, ''))), 'A') ||
        setweight(to_tsvector('english', unaccent(coalesce(array_to_string(t.tags, ' '), ''))), 'A') ||
        setweight(to_tsvector('english', unaccent(coalesce(t.description_text, ''))), 'B') ||
        setweight(to_tsvector('english', unaccent(coalesce(c.conversations_text, ''))), 'C')
    ) AS search_vector
FROM tickets t
LEFT JOIN conversation_text c ON c.ticket_freshdesk_id = t.freshdesk_id;

