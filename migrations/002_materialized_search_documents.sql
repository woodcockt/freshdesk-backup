DROP VIEW IF EXISTS ticket_search_documents;
DROP MATERIALIZED VIEW IF EXISTS ticket_search_documents;

CREATE MATERIALIZED VIEW ticket_search_documents AS
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

CREATE UNIQUE INDEX ticket_search_documents_freshdesk_id_idx
    ON ticket_search_documents(freshdesk_id);
CREATE INDEX ticket_search_documents_search_vector_idx
    ON ticket_search_documents USING gin(search_vector);
CREATE INDEX ticket_search_documents_updated_at_idx
    ON ticket_search_documents(updated_at);
CREATE INDEX ticket_search_documents_tags_idx
    ON ticket_search_documents USING gin(tags);

