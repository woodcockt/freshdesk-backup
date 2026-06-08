import unittest

from archive_search.web import (
    _excerpt,
    _file_size,
    _limit,
    _search_params,
    render_attachments,
    render_body_text,
)


class WebRenderingTest(unittest.TestCase):
    def test_excerpt_escapes_html_and_preserves_highlight_markers(self):
        rendered = _excerpt("<script>alert(1)</script> <<match>>")

        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("<mark>match</mark>", rendered)

    def test_search_params_clamp_limit(self):
        params = _search_params("q=hello&limit=999&status=2")

        self.assertEqual(params["query"], "hello")
        self.assertEqual(params["limit"], 100)
        self.assertEqual(params["status"], 2)

    def test_limit_defaults_for_bad_input(self):
        self.assertEqual(_limit("nope"), 25)

    def test_search_params_accepts_hybrid_backend(self):
        params = _search_params("q=hello&backend=hybrid")

        self.assertEqual(params["backend"], "hybrid")

    def test_file_size_formatting(self):
        self.assertEqual(_file_size(1536), "1.5 KB")

    def test_render_attachments_links_downloaded_files(self):
        html = render_attachments(
            [
                {
                    "id": 10,
                    "filename": "report.pdf",
                    "content_type": "application/pdf",
                    "local_size_bytes": 1024,
                    "local_path": "ticket-1/report.pdf",
                }
            ],
            [],
        )

        self.assertIn('href="/attachments/10"', html)
        self.assertIn("Downloaded", html)

    def test_render_attachments_shows_image_preview(self):
        html = render_attachments(
            [
                {
                    "id": 10,
                    "filename": "image001.png",
                    "content_type": "image/png",
                    "local_size_bytes": 1024,
                    "local_path": "ticket-1/image001.png",
                }
            ],
            [],
        )

        self.assertIn('src="/attachments/10?inline=1"', html)

    def test_render_body_text_replaces_matching_cid_image(self):
        html = render_body_text(
            "Before [cid:image001.png@abc] after",
            [
                {
                    "id": 10,
                    "filename": "image001.png",
                    "content_type": "image/png",
                    "local_path": "ticket-1/image001.png",
                }
            ],
        )

        self.assertIn('src="/attachments/10?inline=1"', html)
        self.assertNotIn("image not available", html)

    def test_render_body_text_replaces_removed_sender_placeholder_by_order(self):
        html = render_body_text(
            "Before Image removed by sender after",
            [
                {
                    "id": 10,
                    "filename": "Image removed by sender",
                    "content_type": "image/png",
                    "source": "inline_image",
                    "local_path": "ticket-1/inline-image",
                }
            ],
        )

        self.assertIn('src="/attachments/10?inline=1"', html)
        self.assertNotIn("image removed by sender</span>", html.lower())
        self.assertIn("<figcaption>inline image</figcaption>", html)

    def test_render_body_text_marks_missing_cid_image(self):
        html = render_body_text("Before [cid:image001.png@abc] after", [])

        self.assertIn("image not available", html)


if __name__ == "__main__":
    unittest.main()
