import unittest

from archive_search.redaction import Redactor
from archive_search.transform import normalize_conversation, normalize_ticket


class TransformTest(unittest.TestCase):
    def test_normalize_ticket_redacts_before_storage(self):
        redactor = Redactor("secret")
        ticket = {
            "id": 42,
            "subject": "Problem for jane@example.com",
            "description_text": "Call +1 415 555 9999",
            "description": "<p>jane@example.com</p>",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "custom_fields": {"cf_product": "SciBiteSearch"},
            "requester": {"email": "jane@example.com", "name": "Jane Doe"},
            "stats": {"resolved_at": None},
            "tags": ["Vocabs"],
        }

        normalized = normalize_ticket(ticket, redactor)

        self.assertEqual(normalized["freshdesk_id"], 42)
        self.assertEqual(normalized["product_label"], "SciBiteSearch")
        self.assertIsNotNone(normalized["requester_email_hash"])
        self.assertNotIn("jane@example.com", str(normalized))
        self.assertNotIn("415 555 9999", str(normalized))

    def test_normalize_conversation_hashes_email_recipients(self):
        redactor = Redactor("secret")
        conversation = {
            "id": 100,
            "ticket_id": 42,
            "body_text": "Reply to customer@example.com",
            "body": "<p>customer@example.com</p>",
            "private": True,
            "incoming": False,
            "source": 2,
            "from_email": "agent@example.com",
            "to_emails": ["customer@example.com"],
            "attachments": [{"name": "screenshot.png"}],
        }

        normalized = normalize_conversation(conversation, redactor)

        self.assertEqual(normalized["freshdesk_id"], 100)
        self.assertEqual(normalized["attachment_count"], 1)
        self.assertEqual(len(normalized["to_email_hashes"]), 1)
        self.assertNotIn("customer@example.com", str(normalized))
        self.assertNotIn("agent@example.com", str(normalized))


if __name__ == "__main__":
    unittest.main()

