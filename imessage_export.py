import sqlite3
import argparse
import datetime
import pathlib
import json
import csv
import os
import re
import sys
import gzip
import plistlib
from typing import List, Dict, Optional, Tuple, Any, Union

from vcard_index import VCardIndex

# Try to import tqdm for progress bar, fallback if not available
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        return iterable

# Apple Epoch: Jan 1 2001 00:00:00 UTC
APPLE_EPOCH = 978307200

def convert_apple_time(ts: Optional[int]) -> datetime.datetime:
    """
    Converts Apple Absolute Time (seconds since 2001-01-01) to a datetime object.

    Args:
        ts (int): Timestamp in seconds (or nanoseconds) since Apple Epoch.

    Returns:
        datetime.datetime: Aware UTC datetime object.
    """
    if ts is None or ts == 0:
        return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    # Handle nanoseconds (18 digits usually) vs seconds (9 digits)
    # 2001 + 30 years is approx 1 billion seconds.
    # If ts > 10^11, it's likely nanoseconds.
    if ts > 100000000000:
        ts = ts / 1000000000

    # Apple Epoch in Unix Timestamp is 978307200
    unix_ts = ts + APPLE_EPOCH
    try:
        return datetime.datetime.fromtimestamp(unix_ts, datetime.timezone.utc)
    except (ValueError, OSError):
        # Handle out of range timestamps
        return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

def decode_rich_text(raw: Optional[Union[bytes, memoryview]]) -> Optional[str]:
    """
    Attempts to decode rich text payloads stored as plists, optionally gzip-compressed.
    Returns a string if it can be extracted, otherwise None.
    """
    if raw is None:
        return None

    data = bytes(raw)

    try:
        if data.startswith(b"\x1f\x8b"):
            data = gzip.decompress(data)
    except OSError:
        # If decompression fails, continue with original data
        pass

    try:
        parsed = plistlib.loads(data)
    except Exception:
        return None

    def _extract_text(obj: Any) -> Optional[str]:
        if isinstance(obj, str):
            return obj
        if isinstance(obj, bytes):
            try:
                return obj.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if isinstance(obj, dict):
            for value in obj.values():
                result = _extract_text(value)
                if result:
                    return result
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                result = _extract_text(item)
                if result:
                    return result
        return None

    return _extract_text(parsed)

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be safe for use as a filename.
    """
    if not name:
        return "Unknown"
    # Remove invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Strip whitespace
    name = name.strip()
    return name or "Unknown"

class DBHandler:
    def __init__(self, db_path: str):
        # Convert to absolute path for URI mode compatibility
        self.db_path = os.path.abspath(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self.cursor: Optional[sqlite3.Cursor] = None

    def connect(self) -> None:
        try:
            # Use regular connection with absolute path
            # SQLite will handle read-only access through file permissions
            if not os.path.exists(self.db_path):
                raise sqlite3.Error(f"Database file not found: {self.db_path}")
            if not os.access(self.db_path, os.R_OK):
                raise sqlite3.Error(f"Database file is not readable: {self.db_path}")
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
        except sqlite3.Error as e:
            print(f"Error connecting to database: {e}")
            print(f"Attempted path: {self.db_path}")
            sys.exit(1)

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def get_chats(self) -> List[Tuple[int, str, str]]:
        """
        Retrieves all chats with their details.
        Returns list of (ROWID, chat_identifier, display_name)
        """
        query = """
            SELECT
                chat.ROWID,
                chat.chat_identifier,
                chat.display_name
            FROM chat
        """
        if self.cursor:
            self.cursor.execute(query)
            return self.cursor.fetchall()
        return []

    def get_chat_participants(self, chat_id: int) -> List[str]:
        """
        Retrieves participant handles for a chat.
        """
        query = """
            SELECT handle.id
            FROM handle
            JOIN chat_handle_join ON handle.ROWID = chat_handle_join.handle_id
            WHERE chat_handle_join.chat_id = ?
        """
        if self.cursor:
            self.cursor.execute(query, (chat_id,))
            return [row[0] for row in self.cursor.fetchall()]
        return []

    def get_messages_for_chat(self, chat_id: int) -> List[Tuple[Optional[str], int, int, Optional[str], int, Optional[bytes], Optional[bytes]]]:
        """
        Retrieves messages for a specific chat.
        Returns list of (text, date, is_from_me, handle_id, cache_has_attachments, attributedBody, message_summary_info)
        """
        query = """
            SELECT
                message.text,
                message.date,
                message.is_from_me,
                handle.id,
                message.cache_has_attachments,
                message.attributedBody,
                message.message_summary_info
            FROM message
            LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
            LEFT JOIN handle ON message.handle_id = handle.ROWID
            WHERE chat_message_join.chat_id = ?
            ORDER BY message.date
        """
        if self.cursor:
            self.cursor.execute(query, (chat_id,))
            return self.cursor.fetchall()
        return []

class Exporter:
    def __init__(self, output_dir: str, output_format: str, contact_index: Optional[VCardIndex] = None):
        self.output_dir = output_dir
        self.output_format = output_format
        self.created_chat_dirs: Dict[str, int] = {} # Map path to chat_id to detect collisions
        self.contact_index = contact_index

    def resolve_handle(self, handle: Optional[str]) -> str:
        """
        Resolve a handle (phone/email) to a contact name when possible.
        """
        if not handle:
            return "Unknown"

        if self.contact_index:
            contact = self.contact_index.get_by_phone(handle) or self.contact_index.get_by_email(handle)
            if contact:
                return contact.full_name

        return handle

    def resolve_participants(self, participants: List[str]) -> List[str]:
        return [self.resolve_handle(p) for p in participants]

    def get_chat_name(
        self,
        chat_row: Tuple[int, str, str],
        participants: List[str],
        resolved_participants: List[str]
    ) -> str:
        chat_id, chat_identifier, display_name = chat_row

        if display_name:
            return display_name

        # If it's a group chat (multiple participants) but no name, join participant names
        if len(resolved_participants) > 1:
            return ", ".join(resolved_participants[:3]) + (f" (+{len(resolved_participants)-3})" if len(resolved_participants) > 3 else "")

        # If individual chat, use the other person's handle
        if len(resolved_participants) == 1:
            return resolved_participants[0]

        # Fallback to chat_identifier
        return self.resolve_handle(chat_identifier)

    def should_process_chat(
        self,
        chat_name: str,
        participants: List[str],
        resolved_participants: List[str],
        filter_contacts: Optional[List[str]]
    ) -> bool:
        if not filter_contacts:
            return True
        haystacks = [chat_name, *participants, *resolved_participants]
        for contact in filter_contacts:
            for target in haystacks:
                if contact.lower() in target.lower():
                    return True
        return False

    def should_process_message(self, message_date: datetime.datetime, date_range: Optional[List[str]]) -> bool:
        if not date_range:
            return True
        start_date, end_date = date_range
        # message_date is a datetime object
        msg_date_str = message_date.strftime("%Y-%m-%d")
        return start_date <= msg_date_str <= end_date

    def process_messages(self, messages: List[Tuple[Optional[str], int, int, Optional[str], int, Optional[bytes], Optional[bytes]]], date_range: Optional[List[str]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Organizes messages by date.
        """
        organized: Dict[str, List[Dict[str, Any]]] = {}
        for msg in messages:
            text, date_ts, is_from_me, sender_handle, has_attachments, attributed_body, summary_info = msg

            # Handle empty text (e.g., attachment only)
            if not text:
                text = decode_rich_text(attributed_body) or decode_rich_text(summary_info) or ""

            # If attachment indicator
            if has_attachments:
                text += " [Attachment]"

            msg_date = convert_apple_time(date_ts)

            if not self.should_process_message(msg_date, date_range):
                continue

            date_key = msg_date.strftime("%Y-%m-%d")

            if date_key not in organized:
                organized[date_key] = []

            sender_name: Union[str, None] = "Me" if is_from_me else self.resolve_handle(sender_handle or "Unknown")

            organized[date_key].append({
                "timestamp": msg_date.strftime("%Y-%m-%d %H:%M:%S"),
                "sender": sender_name,
                "text": text,
                "is_from_me": bool(is_from_me),
                "has_attachments": bool(has_attachments)
            })

        return organized

    def get_unique_chat_dir(self, base_dir: str, chat_name: str, chat_id: int) -> str:
        """
        Returns a unique directory path for the chat, handling collisions.
        """
        safe_name = sanitize_filename(chat_name)
        full_path = os.path.join(base_dir, safe_name)

        # Check if we already assigned this path to a DIFFERENT chat_id
        if full_path in self.created_chat_dirs:
            if self.created_chat_dirs[full_path] != chat_id:
                # Collision detected! Append chat_id to name
                safe_name = f"{safe_name}_{chat_id}"
                full_path = os.path.join(base_dir, safe_name)

        self.created_chat_dirs[full_path] = chat_id
        return full_path

    def export(self, chat_name: str, chat_id: int, organized_messages: Dict[str, List[Dict[str, Any]]]) -> None:
        chat_dir = self.get_unique_chat_dir(self.output_dir, chat_name, chat_id)

        for date_key, messages in organized_messages.items():
            date_dir = os.path.join(chat_dir, date_key)
            pathlib.Path(date_dir).mkdir(parents=True, exist_ok=True)

            filename = f"messages.{self.output_format}"
            filepath = os.path.join(date_dir, filename)

            if self.output_format == 'json':
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(messages, f, indent=2, ensure_ascii=False)

            elif self.output_format == 'csv':
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "Sender", "Message", "From Me", "Has Attachments"])
                    for m in messages:
                        writer.writerow([
                            m["timestamp"],
                            m["sender"],
                            m["text"],
                            m["is_from_me"],
                            m["has_attachments"]
                        ])

            else: # txt
                with open(filepath, 'w', encoding='utf-8') as f:
                    for m in messages:
                        f.write(f"[{m['timestamp']}] {m['sender']}: {m['text']}\n")

def main():
    parser = argparse.ArgumentParser(description="Export iMessage chats from chat.db")

    parser.add_argument("--db-path", default="messages/chat.db", help="Path to chat.db file")
    parser.add_argument("--output-dir", default="imessage_export", help="Directory to save exported chats")
    parser.add_argument("--date-range", nargs=2, metavar=('START', 'END'), help="Date range (YYYY-MM-DD YYYY-MM-DD)")
    parser.add_argument("--contacts", nargs='+', help="Filter by specific contact names or numbers")
    parser.add_argument("--format", choices=['txt', 'json', 'csv'], default='txt', help="Output format")
    parser.add_argument("--contacts-file", default="contacts/contacts.vcf", help="Path to vCard file for contact name resolution")

    args = parser.parse_args()

    # Check if DB exists
    if not os.path.exists(args.db_path):
        print(f"Error: Database file not found at {args.db_path}")
        print("Please ensure you have copied your chat.db to the 'messages/' directory or specified the correct path.")

        # Check if messages dir exists, if not, suggest creating it
        if args.db_path == "messages/chat.db" and not os.path.exists("messages"):
             print("The 'messages/' directory does not exist. Please create it and place your chat.db there.")

        sys.exit(1)

    print(f"Reading database: {args.db_path}")
    db = DBHandler(args.db_path)
    db.connect()

    contact_index: Optional[VCardIndex] = None
    contacts_path = os.path.expanduser(args.contacts_file)
    if contacts_path:
        if os.path.exists(contacts_path):
            try:
                contact_index = VCardIndex.from_file(contacts_path)
                print(f"Loaded {len(contact_index.contacts)} contacts from {contacts_path}")
            except Exception as e:
                print(f"Warning: Failed to load contacts file '{contacts_path}': {e}")
        else:
            print(f"Warning: Contacts file not found at {contacts_path}. Continuing without contact lookup.")

    exporter = Exporter(args.output_dir, args.format, contact_index)

    try:
        chats = db.get_chats()
        print(f"Found {len(chats)} chats. Processing...")

        stats = {
            "processed_chats": 0,
            "exported_messages": 0
        }

        for chat in tqdm(chats, desc="Exporting chats"):
            chat_id = chat[0]
            participants = db.get_chat_participants(chat_id)
            resolved_participants = exporter.resolve_participants(participants)
            chat_name = exporter.get_chat_name(chat, participants, resolved_participants)

            if not exporter.should_process_chat(chat_name, participants, resolved_participants, args.contacts):
                continue

            messages = db.get_messages_for_chat(chat_id)
            if not messages:
                continue

            organized_messages = exporter.process_messages(messages, args.date_range)

            if organized_messages:
                exporter.export(chat_name, chat_id, organized_messages)
                stats["processed_chats"] += 1
                stats["exported_messages"] += sum(len(msgs) for msgs in organized_messages.values())

        print("\nExport Complete!")
        print(f"Chats Processed: {stats['processed_chats']}")
        print(f"Messages Exported: {stats['exported_messages']}")
        print(f"Output Directory: {os.path.abspath(args.output_dir)}")

    finally:
        db.close()

if __name__ == "__main__":
    main()
