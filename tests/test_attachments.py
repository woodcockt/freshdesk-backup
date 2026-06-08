import tempfile
import unittest
from pathlib import Path

from archive_search.attachments import (
    AttachmentDownloader,
    attachment_target_path,
    extract_inline_images,
    safe_filename,
)
from archive_search.freshdesk import DownloadedFile


class FakeClient:
    def iter_conversations(self, ticket_id):
        return iter(
            [
                {
                    "id": 456,
                    "attachments": [
                        {
                            "id": 99,
                            "attachment_url": "https://fresh.example/real-url",
                        }
                    ],
                }
            ]
        )

    def download_to_path(self, url, target_path):
        self.url = url
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"attachment bytes")
        return DownloadedFile(
            bytes_written=16,
            sha256="abc123",
            content_type="text/plain",
        )


class FakeDatabase:
    def __init__(self):
        self.downloaded = []
        self.errors = []
        self.rebuild_args = None
        self.iter_args = None

    def rebuild_attachment_metadata(self, ticket_id=None, max_tickets=None):
        self.rebuild_args = (ticket_id, max_tickets)
        return 1

    def iter_inline_image_candidate_ticket_ids(self, ticket_id=None, limit=None, max_tickets=None):
        return []

    def upsert_inline_image_metadata(self, ticket_id, images_by_conversation):
        return sum(len(images) for images in images_by_conversation.values())

    def iter_attachments_to_download(
        self,
        limit=None,
        ticket_id=None,
        max_tickets=None,
        force=False,
    ):
        self.iter_args = (limit, ticket_id, max_tickets, force)
        return [
            {
                "id": 10,
                "freshdesk_attachment_id": 99,
                "ticket_freshdesk_id": 123,
                "conversation_freshdesk_id": 456,
                "attachment_index": 0,
                "filename": "../report?.txt",
                "remote_url": "https://example.freshdesk.com/attachment/99",
            }
        ][:limit]

    def mark_attachment_downloaded(
        self,
        attachment_id,
        local_path,
        local_size_bytes,
        sha256,
        content_type,
    ):
        self.downloaded.append(
            (attachment_id, local_path, local_size_bytes, sha256, content_type)
        )

    def mark_attachment_error(self, attachment_id, error):
        self.errors.append((attachment_id, error))


class AttachmentTest(unittest.TestCase):
    def test_safe_filename_removes_path_separators(self):
        self.assertEqual(safe_filename("../bad/name?.txt"), "bad_name_.txt")

    def test_extract_inline_images_maps_cids_to_img_sources(self):
        images = extract_inline_images(
            {
                "body_text": "see [cid:image001.png@abc]",
                "body": '<p><img src="https://attachment.freshdesk.com/inline/attachment?token=abc"></p>',
            }
        )

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["name"], "image001.png")
        self.assertEqual(images[0]["content_type"], "image/png")
        self.assertEqual(
            images[0]["safe_remote_url"],
            "https://attachment.freshdesk.com/inline/attachment",
        )
        self.assertIn("token=abc", images[0]["attachment_url"])

    def test_extract_inline_images_ignores_removed_sender_alt_as_filename(self):
        images = extract_inline_images(
            {
                "body_text": "Image removed by sender",
                "body": (
                    '<p><img alt="Image removed by sender" '
                    'src="https://attachment.freshdesk.com/inline/attachment?token=abc"></p>'
                ),
            }
        )

        self.assertEqual(images[0]["name"], "inline-image-1.img")

    def test_attachment_target_path_is_under_root(self):
        root = Path("/tmp/archive")
        path = attachment_target_path(
            root,
            {
                "id": 10,
                "ticket_freshdesk_id": 123,
                "conversation_freshdesk_id": 456,
                "attachment_index": 0,
                "filename": "../report?.txt",
            },
        )

        self.assertEqual(path.parent.name, "attachment-10")
        self.assertEqual(path.name, "report_.txt")

    def test_downloader_writes_file_and_marks_database(self):
        database = FakeDatabase()
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = AttachmentDownloader(client, database, tmpdir).run(max_attachments=1)

        self.assertEqual(result.downloaded, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(client.url, "https://fresh.example/real-url")
        self.assertEqual(database.downloaded[0][0], 10)
        self.assertIn("ticket-123", database.downloaded[0][1])
        self.assertEqual(database.errors, [])

    def test_downloader_forwards_max_tickets(self):
        database = FakeDatabase()
        with tempfile.TemporaryDirectory() as tmpdir:
            AttachmentDownloader(FakeClient(), database, tmpdir).run(max_tickets=1000)

        self.assertEqual(database.rebuild_args, (None, 1000))
        self.assertEqual(database.iter_args[2], 1000)


if __name__ == "__main__":
    unittest.main()
