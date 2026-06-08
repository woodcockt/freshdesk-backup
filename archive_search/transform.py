from __future__ import annotations

from typing import Any

from .redaction import Redactor


def normalize_ticket(ticket: dict[str, Any], redactor: Redactor) -> dict[str, Any]:
    custom_fields = ticket.get("custom_fields") or {}
    requester = ticket.get("requester") or {}
    stats = ticket.get("stats") or {}
    tags = [redactor.redact_text(tag) for tag in ticket.get("tags") or []]
    product_label = custom_fields.get("cf_product")

    return {
        "freshdesk_id": ticket["id"],
        "subject": redactor.redact_text(ticket.get("subject")),
        "description_text": redactor.redact_text(ticket.get("description_text")),
        "description_html": redactor.redact_text(ticket.get("description")),
        "structured_description": redactor.redact_json(ticket.get("structured_description")),
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "due_by": ticket.get("due_by"),
        "fr_due_by": ticket.get("fr_due_by"),
        "nr_due_by": ticket.get("nr_due_by"),
        "closed_at": stats.get("closed_at"),
        "resolved_at": stats.get("resolved_at"),
        "first_responded_at": stats.get("first_responded_at"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "source": ticket.get("source"),
        "type": redactor.redact_text(ticket.get("type")),
        "product_id": ticket.get("product_id"),
        "product_label": redactor.redact_text(product_label),
        "group_id": ticket.get("group_id"),
        "requester_id": ticket.get("requester_id"),
        "responder_id": ticket.get("responder_id"),
        "company_id": ticket.get("company_id"),
        "support_email_hash": redactor.hash_identifier(ticket.get("support_email")),
        "requester_email_hash": redactor.hash_identifier(requester.get("email")),
        "requester_phone_hash": redactor.hash_identifier(requester.get("phone") or requester.get("mobile")),
        "requester_name_hash": redactor.hash_identifier(requester.get("name")),
        "tags": tags,
        "custom_fields": redactor.redact_json(custom_fields),
        "stats": redactor.redact_json(stats),
        "raw": redactor.redact_json(ticket),
    }


def normalize_conversation(conversation: dict[str, Any], redactor: Redactor) -> dict[str, Any]:
    attachments = conversation.get("attachments") or []
    return {
        "freshdesk_id": conversation["id"],
        "ticket_freshdesk_id": conversation["ticket_id"],
        "body_text": redactor.redact_text(conversation.get("body_text")),
        "body_html": redactor.redact_text(conversation.get("body")),
        "private": conversation.get("private"),
        "incoming": conversation.get("incoming"),
        "source": conversation.get("source"),
        "user_id": conversation.get("user_id"),
        "support_email_hash": redactor.hash_identifier(conversation.get("support_email")),
        "from_email_hash": redactor.hash_identifier(conversation.get("from_email")),
        "to_email_hashes": redactor.hash_list(conversation.get("to_emails")),
        "cc_email_hashes": redactor.hash_list(conversation.get("cc_emails")),
        "bcc_email_hashes": redactor.hash_list(conversation.get("bcc_emails")),
        "attachment_count": len(attachments),
        "attachments": redactor.redact_json(attachments),
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "last_edited_at": conversation.get("last_edited_at"),
        "last_edited_user_id": conversation.get("last_edited_user_id"),
        "raw": redactor.redact_json(conversation),
    }

