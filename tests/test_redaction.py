import unittest

from archive_search.redaction import Redactor


class RedactionTest(unittest.TestCase):
    def test_redacts_email_phone_and_sensitive_query_values(self):
        redactor = Redactor("secret")
        text = (
            "Email jane.doe@example.com or call +1 (212) 555-1212. "
            "See https://example.com/case?id=123&token=abc123"
        )

        redacted = redactor.redact_text(text)

        self.assertNotIn("jane.doe@example.com", redacted)
        self.assertNotIn("212) 555-1212", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertIn("[REDACTED_EMAIL]", redacted)
        self.assertIn("[REDACTED_PHONE]", redacted)
        self.assertIn("token=%5BREDACTED_QUERY%5D", redacted)

    def test_hash_identifier_is_deterministic_and_normalized(self):
        redactor = Redactor("secret")

        first = redactor.hash_identifier("Jane.Doe@Example.com")
        second = redactor.hash_identifier(" jane.doe@example.com ")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_does_not_redact_date_like_values_as_phone_numbers(self):
        redactor = Redactor("secret")
        text = "Created at 2015-01-23 and updated at 2024-12-31."

        redacted = redactor.redact_text(text)

        self.assertEqual(text, redacted)

    def test_malformed_url_does_not_crash_redaction(self):
        redactor = Redactor("secret")
        text = "Broken link https://[bad-ipv6/case?token=abc123&x=1 should not crash"

        redacted = redactor.redact_text(text)

        self.assertNotIn("abc123", redacted)
        self.assertIn("token=[REDACTED_QUERY]", redacted)

    def test_redact_json_recurses_without_changing_non_strings(self):
        redactor = Redactor("secret")
        payload = {
            "email": "a@example.com",
            "count": 10,
            "nested": ["Call 415-555-9999", None],
        }

        redacted = redactor.redact_json(payload)

        self.assertEqual(redacted["count"], 10)
        self.assertIsNone(redacted["nested"][1])
        self.assertNotIn("a@example.com", str(redacted))
        self.assertNotIn("415-555-9999", str(redacted))


if __name__ == "__main__":
    unittest.main()
