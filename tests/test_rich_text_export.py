import gzip
import plistlib
import unittest

from imessage_export import Exporter, decode_rich_text


class TestRichTextDecoding(unittest.TestCase):
    def test_decode_rich_text_gzip_plist(self):
        payload = gzip.compress(plistlib.dumps("Hello Rich Text"))
        self.assertEqual(decode_rich_text(payload), "Hello Rich Text")

    def test_process_messages_uses_rich_text_fallback(self):
        rich_text = gzip.compress(plistlib.dumps("Styled Message"))
        exporter = Exporter(output_dir=".", output_format="json")
        messages = [
            (None, 0, 0, "+1234567890", 0, rich_text, None)
        ]

        organized = exporter.process_messages(messages, date_range=None)
        self.assertIn("1970-01-01", organized)
        self.assertEqual(organized["1970-01-01"][0]["text"], "Styled Message")


if __name__ == "__main__":
    unittest.main()
