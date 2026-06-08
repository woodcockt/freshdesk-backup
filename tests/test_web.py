import unittest

from archive_search.web import _excerpt, _file_size, _limit, _search_params


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

    def test_file_size_formatting(self):
        self.assertEqual(_file_size(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main()
