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
from typing import List, Dict, Optional, Tuple, Any, Union, Hashable

from vcard_index import VCardIndex, Contact, normalize_email, normalize_name

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
    Falls back to extracting text from NSKeyedArchiver format if plist parsing fails.
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

    # Try standard plist parsing first
    try:
        parsed = plistlib.loads(data)
        
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

        result = _extract_text(parsed)
        if result:
            return result
    except Exception:
        # Plist parsing failed, try NSKeyedArchiver extraction
        pass

    # Fallback: Extract text from NSKeyedArchiver binary format
    # This handles cases where attributedBody uses NSKeyedArchiver instead of standard plist
    try:
        decoded = data.decode('utf-8', errors='replace')
        # Look for sequences that look like actual message text
        # Pattern: at least 3 chars, mix of letters, numbers, spaces, common punctuation
        # Exclude technical strings like "NSObject", "NSString", "kIMMessagePartAttributeName", UUIDs
        pattern = r'[a-zA-Z0-9\s\.,!?;:\'\"\-\(\)\u2019\u2018\u201C\u201D\u2026]{3,}'
        matches = re.findall(pattern, decoded)
        if matches:
            # Filter out technical strings and return the longest meaningful match
            filtered = []
            for m in matches:
                m_stripped = m.strip()
                # Skip if too short
                if len(m_stripped) < 2:
                    continue
                # Skip technical strings
                if (m_stripped.startswith('NS') or 
                    m_stripped.startswith('kIM') or
                    m_stripped.startswith('streamtype') or
                    'AttributeName' in m_stripped or
                    'Object' in m_stripped or
                    # Skip UUIDs (8-4-4-4-12 hex pattern)
                    re.match(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$', m_stripped, re.I) or
                    # Skip if it's all numbers
                    all(c in '0123456789' for c in m_stripped)):
                    continue
                # Prefer strings with actual letters (not just numbers/punctuation)
                if any(c.isalpha() for c in m_stripped):
                    filtered.append(m_stripped)
            
            if filtered:
                # Return the longest match, but prefer ones that look more like messages
                # (have spaces, punctuation, etc.)
                def score(s):
                    score_val = len(s)
                    if ' ' in s:
                        score_val += 10  # Prefer strings with spaces
                    if any(c in s for c in '.,!?;:'):
                        score_val += 5  # Prefer strings with punctuation
                    return score_val
                
                result = max(filtered, key=score)
                # Clean up: remove any trailing control characters
                result = re.sub(r'[\x00-\x1F]+$', '', result)
                if len(result.strip()) >= 1:
                    return result.strip()
    except Exception:
        pass

    return None

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

    def get_messages_for_chat(self, chat_id: int) -> List[Tuple[Any, ...]]:
        """
        Retrieves messages for a specific chat.
        Returns list of (ROWID, guid, text, date, is_from_me, handle_id,
        cache_has_attachments, attributedBody, message_summary_info)
        """
        query = """
            SELECT
                message.ROWID,
                message.guid,
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
        self.created_chat_dirs: Dict[str, str] = {} # Map path to merge key to detect true name collisions
        self.contact_index = contact_index

    def contact_identity(self, contact: Contact) -> str:
        name_key = normalize_name(contact.full_name)
        if name_key:
            return f"contact:{name_key}"
        for email in contact.emails:
            email_key = normalize_email(email)
            if email_key:
                return f"contact-email:{email_key}"
        for phone in contact.phones:
            phone_key = self.phone_identity(phone)
            if phone_key != "unknown":
                return f"contact-{phone_key}"
        return f"contact-object:{id(contact)}"

    def phone_identity(self, value: str) -> str:
        stripped = value.strip()
        phoneish = bool(re.fullmatch(r"\+?[0-9 ()\-.]+", stripped))
        digits = re.sub(r"[^0-9]", "", stripped)
        if not phoneish or not digits:
            return "unknown"
        # NANP numbers commonly appear both as +1XXXXXXXXXX and XXXXXXXXXX.
        if len(digits) == 11 and digits.startswith("1"):
            return f"phone:{digits[1:]}"
        if len(digits) == 10:
            return f"phone:{digits}"
        if stripped.startswith("+"):
            return f"phone:+{digits}"
        return f"phone:{digits}"

    def resolve_contact(self, value: Optional[str]) -> Optional[Contact]:
        if not value or not self.contact_index:
            return None
        return (
            self.contact_index.get_by_phone(value)
            or self.contact_index.get_by_email(value)
            or self.contact_index.get_by_name(value)
        )

    def resolve_handle(self, handle: Optional[str]) -> str:
        """
        Resolve a handle (phone/email) to a contact name when possible.
        """
        if not handle:
            return "Unknown"

        contact = self.resolve_contact(handle)
        if contact:
            return contact.full_name

        return handle

    def handle_identity(self, handle: Optional[str]) -> str:
        """
        Return a stable identity key for a handle or contact display name.

        Contact-backed handles intentionally use the contact name as the
        identity so phone-number chats and display-name-only chats merge.
        """
        if not handle:
            return "unknown"

        contact = self.resolve_contact(handle)
        if contact:
            return self.contact_identity(contact)

        stripped = handle.strip()
        email_key = normalize_email(stripped)
        if "@" in email_key:
            return f"email:{email_key}"

        phone_key = self.phone_identity(stripped)
        if phone_key != "unknown":
            return phone_key

        name_key = normalize_name(stripped)
        return f"name:{name_key}" if name_key else "unknown"

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

    def get_chat_merge_key(
        self,
        chat_row: Tuple[int, str, str],
        participants: List[str],
        resolved_participants: List[str]
    ) -> str:
        """
        Compute the identity bucket used for exporting.

        iMessage can create multiple chat rows for the same person, for example
        one row identified by a phone number and another by the contact display
        name. Direct chats are keyed by the resolved participant/contact
        identity; group chats are keyed by their participant identity set.
        """
        _chat_id, chat_identifier, display_name = chat_row
        participant_identities = sorted(
            identity for identity in (self.handle_identity(p) for p in participants)
            if identity != "unknown"
        )

        if len(participant_identities) == 1:
            return f"direct:{participant_identities[0]}"
        if len(participant_identities) > 1:
            return "group:" + "|".join(participant_identities)

        display_identity = self.handle_identity(display_name)
        if display_identity != "unknown":
            return f"direct:{display_identity}"

        identifier_identity = self.handle_identity(chat_identifier)
        if identifier_identity != "unknown":
            return f"direct:{identifier_identity}"

        normalized_participants = sorted(normalize_name(p) for p in resolved_participants if normalize_name(p))
        if normalized_participants:
            return "group:" + "|".join(normalized_participants)

        return f"chat:{chat_row[0]}"

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

    def process_messages(self, messages: List[Tuple[Any, ...]], date_range: Optional[List[str]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Organizes messages by date.
        """
        organized: Dict[str, List[Dict[str, Any]]] = {}
        for msg in messages:
            message_id: Optional[int] = None
            guid: Optional[str] = None
            if len(msg) == 9:
                message_id, guid, text, date_ts, is_from_me, sender_handle, has_attachments, attributed_body, summary_info = msg
            else:
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
                "_message_id": message_id,
                "_guid": guid,
                "timestamp": msg_date.strftime("%Y-%m-%d %H:%M:%S"),
                "sender": sender_name,
                "text": text,
                "is_from_me": bool(is_from_me),
                "has_attachments": bool(has_attachments)
            })

        return organized

    def message_identity(self, message: Dict[str, Any]) -> Hashable:
        if message.get("_guid"):
            return ("guid", message["_guid"])
        if message.get("_message_id") is not None:
            return ("rowid", message["_message_id"])
        return (
            "content",
            message["timestamp"],
            message["sender"],
            message["text"],
            message["is_from_me"],
            message["has_attachments"],
        )

    def merge_organized_messages(
        self,
        target: Dict[str, List[Dict[str, Any]]],
        incoming: Dict[str, List[Dict[str, Any]]]
    ) -> None:
        for date_key, messages in incoming.items():
            target.setdefault(date_key, []).extend(messages)

    def finalize_organized_messages(
        self,
        organized_messages: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        finalized: Dict[str, List[Dict[str, Any]]] = {}
        seen: set[Hashable] = set()
        for date_key in sorted(organized_messages):
            deduped: List[Dict[str, Any]] = []
            for message in sorted(organized_messages[date_key], key=lambda m: (m["timestamp"], str(self.message_identity(m)))):
                identity = self.message_identity(message)
                if identity in seen:
                    continue
                seen.add(identity)
                deduped.append(message)
            if deduped:
                finalized[date_key] = deduped
        return finalized

    def prefer_chat_name(self, current: str, candidate: str) -> str:
        if not current:
            return candidate
        if not candidate:
            return current

        def is_raw_handle(value: str) -> bool:
            return "@" in value or bool(re.fullmatch(r"\+?[0-9 ()\-.]+", value.strip()))

        if is_raw_handle(current) and not is_raw_handle(candidate):
            return candidate
        return current

    def get_unique_chat_dir(self, base_dir: str, chat_name: str, merge_key: str) -> str:
        """
        Returns a unique directory path for the merged chat, handling only true
        name collisions between different identities.
        """
        safe_name = sanitize_filename(chat_name)
        full_path = os.path.join(base_dir, safe_name)

        if full_path in self.created_chat_dirs and self.created_chat_dirs[full_path] != merge_key:
            suffix = 2
            while True:
                candidate_path = os.path.join(base_dir, f"{safe_name}_{suffix}")
                if candidate_path not in self.created_chat_dirs or self.created_chat_dirs[candidate_path] == merge_key:
                    full_path = candidate_path
                    break
                suffix += 1

        self.created_chat_dirs[full_path] = merge_key
        return full_path

    def export(self, chat_name: str, merge_key: str, organized_messages: Dict[str, List[Dict[str, Any]]]) -> None:
        chat_dir = self.get_unique_chat_dir(self.output_dir, chat_name, merge_key)
        organized_messages = self.finalize_organized_messages(organized_messages)

        for date_key, messages in organized_messages.items():
            date_dir = os.path.join(chat_dir, date_key)
            pathlib.Path(date_dir).mkdir(parents=True, exist_ok=True)

            filename = f"messages.{self.output_format}"
            filepath = os.path.join(date_dir, filename)

            if self.output_format == 'json':
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump([self.export_message(m) for m in messages], f, indent=2, ensure_ascii=False)

            elif self.output_format == 'csv':
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "Sender", "Message", "From Me", "Has Attachments"])
                    for m in messages:
                        m = self.export_message(m)
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
                        m = self.export_message(m)
                        f.write(f"[{m['timestamp']}] {m['sender']}: {m['text']}\n")

    def export_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in message.items() if not key.startswith("_")}

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
            "exported_chats": 0,
            "exported_messages": 0
        }
        aggregated_exports: Dict[str, Dict[str, Any]] = {}

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
                merge_key = exporter.get_chat_merge_key(chat, participants, resolved_participants)
                if merge_key not in aggregated_exports:
                    aggregated_exports[merge_key] = {
                        "chat_name": chat_name,
                        "messages": {}
                    }
                else:
                    aggregated_exports[merge_key]["chat_name"] = exporter.prefer_chat_name(
                        aggregated_exports[merge_key]["chat_name"],
                        chat_name,
                    )
                exporter.merge_organized_messages(aggregated_exports[merge_key]["messages"], organized_messages)
                stats["processed_chats"] += 1

        for merge_key, export_data in aggregated_exports.items():
            finalized_messages = exporter.finalize_organized_messages(export_data["messages"])
            if not finalized_messages:
                continue
            exporter.export(export_data["chat_name"], merge_key, finalized_messages)
            stats["exported_chats"] += 1
            stats["exported_messages"] += sum(len(msgs) for msgs in finalized_messages.values())

        print("\nExport Complete!")
        print(f"Source Chats Processed: {stats['processed_chats']}")
        print(f"Merged Chats Exported: {stats['exported_chats']}")
        print(f"Messages Exported: {stats['exported_messages']}")
        print(f"Output Directory: {os.path.abspath(args.output_dir)}")

    finally:
        db.close()

if __name__ == "__main__":
    main()
