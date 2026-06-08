import unittest
from datetime import datetime, timezone

from archive_search.typesense_search import (
    _build_filter_by,
    _fuse_ticket_rows,
    _hit_to_row,
    row_to_chunk_documents,
    row_to_document,
)


class TypesenseSearchTest(unittest.TestCase):
    def test_row_to_document_converts_archive_row(self):
        row = {
            "freshdesk_id": 123,
            "subject": "API issue",
            "description_text": "Description",
            "product_label": "CENtree",
            "tags": ["API"],
            "status": 2,
            "priority": 3,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
            "attachment_count": 2,
            "search_text": "API issue Description",
        }

        document = row_to_document(row)

        self.assertEqual(document["id"], "123")
        self.assertEqual(document["freshdesk_id"], 123)
        self.assertEqual(document["product_label"], "CENtree")
        self.assertEqual(document["created_at_ts"], 1704067200)

    def test_filter_by_uses_exact_literals_and_ranges(self):
        filters = _build_filter_by(
            product="Running Shoes, Men",
            tags=["API"],
            status=2,
            priority=3,
            created_from="2024-01-01",
            created_to="2024-01-31",
        )

        self.assertIn("product_label:=`Running Shoes, Men`", filters)
        self.assertIn("tags:=[`API`]", filters)
        self.assertIn("status:=2", filters)
        self.assertIn("priority:=3", filters)
        self.assertIn("created_at_ts:[1704067200..1706745599]", filters)

    def test_hit_to_row_uses_best_highlight(self):
        row = _hit_to_row(
            {
                "text_match": 100,
                "document": {
                    "freshdesk_id": 123,
                    "subject": "API issue",
                    "product_label": "CENtree",
                    "tags": ["API"],
                    "status": 2,
                    "priority": 3,
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-02T00:00:00+00:00",
                    "search_text": "fallback",
                },
                "highlights": [
                    {"field": "search_text", "snippet": "matched <<api>> text"},
                ],
            }
        )

        self.assertEqual(row["freshdesk_id"], 123)
        self.assertEqual(row["excerpt"], "matched <<api>> text")

    def test_row_to_chunk_documents_prefixes_metadata_and_uses_stable_ids(self):
        row = {
            "freshdesk_id": 123,
            "subject": "API issue",
            "description_text": "Description",
            "product_label": "CENtree",
            "tags": ["API"],
            "status": 2,
            "priority": 3,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
            "attachment_count": 2,
            "search_text": "API issue\n\nCENtree\n\n" + " ".join(f"word{i}" for i in range(80)),
        }

        documents = row_to_chunk_documents(row, chunk_chars=300, chunk_overlap=40)

        self.assertGreaterEqual(len(documents), 2)
        self.assertEqual(documents[0]["id"], "123-0")
        self.assertEqual(documents[0]["freshdesk_id"], 123)
        self.assertIn("API issue", documents[0]["chunk_text"])
        self.assertIn("CENtree", documents[0]["chunk_text"])

    def test_fuse_ticket_rows_combines_keyword_and_semantic_ranks(self):
        rows = _fuse_ticket_rows(
            keyword_rows=[
                {"freshdesk_id": 1, "subject": "first", "excerpt": "keyword"},
                {"freshdesk_id": 2, "subject": "second", "excerpt": "keyword"},
            ],
            semantic_rows=[
                {"freshdesk_id": 2, "subject": "second", "excerpt": "semantic"},
                {"freshdesk_id": 3, "subject": "third", "excerpt": "semantic"},
            ],
            limit=3,
        )

        self.assertEqual(rows[0]["freshdesk_id"], 2)
        self.assertEqual(rows[0]["match_source"], "keyword + semantic")
        self.assertEqual({row["freshdesk_id"] for row in rows}, {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
