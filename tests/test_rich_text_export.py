import gzip
import json
import plistlib
import tempfile
import unittest
from pathlib import Path

from imessage_export import Exporter, decode_rich_text
from vcard_index import Contact, VCardIndex


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

    def test_phone_and_contact_name_chats_share_merge_key(self):
        contact_index = VCardIndex([
            Contact(
                full_name="Lizzie Awesomest Friend Ever 🥭🍌",
                phones=["+1 (325) 271-9971"],
            )
        ])
        exporter = Exporter(output_dir=".", output_format="json", contact_index=contact_index)

        phone_chat = (1, "+13252719971", "")
        name_chat = (434, "chat434", "Lizzie Awesomest Friend Ever 🥭🍌")

        self.assertEqual(
            exporter.get_chat_merge_key(phone_chat, ["+13252719971"], ["Lizzie Awesomest Friend Ever 🥭🍌"]),
            exporter.get_chat_merge_key(name_chat, [], []),
        )

    def test_export_merges_and_deduplicates_messages(self):
        contact_index = VCardIndex([
            Contact(
                full_name="Lizzie Awesomest Friend Ever 🥭🍌",
                phones=["+1 (325) 271-9971"],
            )
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = Exporter(output_dir=tmpdir, output_format="json", contact_index=contact_index)
            merge_key = "direct:contact:lizzie awesomest friend ever 🥭🍌"
            organized = {
                "2024-01-15": [
                    {
                        "_message_id": 10,
                        "_guid": "msg-10",
                        "timestamp": "2024-01-15 10:00:00",
                        "sender": "Lizzie Awesomest Friend Ever 🥭🍌",
                        "text": "first copy",
                        "is_from_me": False,
                        "has_attachments": False,
                    },
                    {
                        "_message_id": 10,
                        "_guid": "msg-10",
                        "timestamp": "2024-01-15 10:00:00",
                        "sender": "Lizzie Awesomest Friend Ever 🥭🍌",
                        "text": "first copy",
                        "is_from_me": False,
                        "has_attachments": False,
                    },
                    {
                        "_message_id": 11,
                        "_guid": "msg-11",
                        "timestamp": "2024-01-15 10:01:00",
                        "sender": "Me",
                        "text": "reply",
                        "is_from_me": True,
                        "has_attachments": False,
                    },
                ]
            }

            exporter.export("Lizzie Awesomest Friend Ever 🥭🍌", merge_key, organized)

            chat_dirs = [path for path in Path(tmpdir).iterdir() if path.is_dir()]
            self.assertEqual(len(chat_dirs), 1)

            output_file = chat_dirs[0] / "2024-01-15" / "messages.json"
            payload = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual([message["text"] for message in payload], ["first copy", "reply"])
            self.assertNotIn("_message_id", payload[0])
            self.assertNotIn("_guid", payload[0])


if __name__ == "__main__":
    unittest.main()
