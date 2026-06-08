import unittest

from archive_search.chunking import chunk_text


class ChunkingTest(unittest.TestCase):
    def test_chunk_text_uses_overlap_and_preserves_content(self):
        text = " ".join(f"word{i}" for i in range(120))

        chunks = chunk_text(text, chunk_chars=300, overlap_chars=40)

        self.assertGreater(len(chunks), 1)
        self.assertIn("word0", chunks[0])
        self.assertTrue(any("word119" in chunk for chunk in chunks))

    def test_chunk_text_returns_no_blank_chunks(self):
        self.assertEqual(chunk_text(" \n\n "), [])


if __name__ == "__main__":
    unittest.main()
