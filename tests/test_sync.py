import unittest

from archive_search.redaction import Redactor
from archive_search.sync import SyncService


class FakeClient:
    def __init__(self, tickets):
        self.tickets = tickets
        self.conversation_calls = 0

    def list_ticket_fields(self):
        return []

    def iter_tickets(self, updated_since, per_page=100):
        yield from self.tickets

    def iter_conversations(self, ticket_id):
        self.conversation_calls += 1
        return iter([])


class FakeDatabase:
    def __init__(self, cursor=None, existing_ids=None):
        self.cursor = cursor
        self.existing_ids = set(existing_ids or [])
        self.upserts = []

    def apply_migrations(self):
        pass

    def mark_sync_started(self):
        pass

    def upsert_ticket_fields(self, fields):
        pass

    def get_sync_cursor(self):
        return self.cursor

    def ticket_exists(self, freshdesk_id):
        return freshdesk_id in self.existing_ids

    def upsert_ticket_with_conversations(self, ticket, conversations):
        self.upserts.append((ticket, conversations))

    def mark_sync_success(self, last_updated_at, ticket_count, conversation_count):
        self.success = (last_updated_at, ticket_count, conversation_count)

    def mark_sync_error(self, error):
        self.error = error


class SyncServiceTest(unittest.TestCase):
    def test_skips_existing_ticket_at_saved_cursor_boundary(self):
        ticket = {
            "id": 42,
            "updated_at": "2026-06-07T09:32:19Z",
            "subject": "Already archived",
            "custom_fields": {},
            "tags": [],
        }
        client = FakeClient([ticket])
        database = FakeDatabase(
            cursor="2026-06-07T09:32:19Z",
            existing_ids={42},
        )
        service = SyncService(client, database, Redactor("secret"), "2015-01-01T00:00:00Z", 100)

        result = service.run(max_tickets=1)

        self.assertEqual(result.tickets, 0)
        self.assertEqual(database.upserts, [])
        self.assertEqual(client.conversation_calls, 0)

    def test_explicit_since_reimports_existing_ticket(self):
        ticket = {
            "id": 42,
            "updated_at": "2026-06-07T09:32:19Z",
            "subject": "Reimport requested",
            "description_text": "",
            "description": "",
            "custom_fields": {},
            "requester": {},
            "stats": {},
            "tags": [],
        }
        client = FakeClient([ticket])
        database = FakeDatabase(
            cursor="2026-06-07T09:32:19Z",
            existing_ids={42},
        )
        service = SyncService(client, database, Redactor("secret"), "2015-01-01T00:00:00Z", 100)

        result = service.run(since="2026-06-07T09:32:19Z", max_tickets=1)

        self.assertEqual(result.tickets, 1)
        self.assertEqual(len(database.upserts), 1)
        self.assertEqual(client.conversation_calls, 1)


if __name__ == "__main__":
    unittest.main()
