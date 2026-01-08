import argparse
import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from sentence_transformers import SentenceTransformer

from imessage_export import DBHandler, Exporter, convert_apple_time, decode_rich_text
from vcard_index import VCardIndex


try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is optional
    def tqdm(iterable, *args, **kwargs):
        return iterable


@dataclass
class MessageRecord:
    message_id: str
    text: str
    timestamp: str
    chat_id: int
    chat_display_name: str
    sender_id: str
    sender_name: str
    participants: List[str]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Embed iMessage chat content with a sentence-transformers model and store it in Qdrant."
        )
    )
    parser.add_argument("--db-path", default="messages/chat.db", help="Path to chat.db file")
    parser.add_argument(
        "--contacts-file",
        default="contacts/contacts.vcf",
        help="Path to vCard file for contact name resolution",
    )
    parser.add_argument("--contacts", nargs="+", help="Filter by specific contact names or numbers")
    parser.add_argument(
        "--date-range",
        nargs=2,
        metavar=("START", "END"),
        help="Date range (YYYY-MM-DD YYYY-MM-DD)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Qdrant server host")
    parser.add_argument("--port", type=int, default=6333, help="Qdrant server port")
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key for Qdrant (omit if authentication is disabled)",
    )
    parser.add_argument(
        "--https",
        action="store_true",
        help="Use HTTPS when connecting to Qdrant (requires server support)",
    )
    parser.add_argument(
        "--collection-name",
        default="messages",
        help="Collection name to store embeddings (default: messages)",
    )
    parser.add_argument(
        "--model-name",
        default="all-MiniLM-L6-v2",
        help="Sentence-transformers model name (default: all-MiniLM-L6-v2)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Number of messages to embed per batch (default: 64)",
    )
    return parser.parse_args()


def load_contact_index(path: str) -> Optional[VCardIndex]:
    if not path:
        return None
    try:
        return VCardIndex.from_vcard(path)
    except FileNotFoundError:
        print(f"Contacts file not found at {path}. Proceeding without contact resolution.")
        return None


def build_message_id(
    chat_id: int, timestamp: str, sender_id: str, text: str, salt: str = ""
) -> str:
    digest = hashlib.sha1(
        f"{chat_id}|{timestamp}|{sender_id}|{text}|{salt}".encode("utf-8")
    ).hexdigest()
    return digest


def iter_messages(
    db: DBHandler,
    exporter: Exporter,
    date_range: Optional[List[str]],
    filter_contacts: Optional[List[str]],
) -> Iterable[MessageRecord]:
    for chat_row in db.get_chats():
        chat_id, _, _ = chat_row
        participants = db.get_chat_participants(chat_id)
        resolved_participants = exporter.resolve_participants(participants)
        chat_name = exporter.get_chat_name(chat_row, participants, resolved_participants)

        if not exporter.should_process_chat(
            chat_name, participants, resolved_participants, filter_contacts
        ):
            continue

        for msg in db.get_messages_for_chat(chat_id):
            (
                text,
                date_ts,
                is_from_me,
                sender_handle,
                has_attachments,
                attributed_body,
                summary_info,
            ) = msg

            if not text:
                text = decode_rich_text(attributed_body) or decode_rich_text(summary_info) or ""

            if has_attachments:
                text = f"{text} [Attachment]" if text else "[Attachment]"

            if not text:
                continue

            msg_date = convert_apple_time(date_ts)
            if not exporter.should_process_message(msg_date, date_range):
                continue

            sender_name = "Me" if is_from_me else exporter.resolve_handle(sender_handle or "Unknown")
            sender_id = "me" if is_from_me else (sender_handle or "unknown")
            timestamp = msg_date.isoformat()
            message_id = build_message_id(chat_id, timestamp, sender_id, text)

            yield MessageRecord(
                message_id=message_id,
                text=text,
                timestamp=timestamp,
                chat_id=chat_id,
                chat_display_name=chat_name,
                sender_id=sender_id,
                sender_name=sender_name,
                participants=resolved_participants,
            )


def embed_and_store(
    client: QdrantClient,
    collection_name: str,
    model: SentenceTransformer,
    messages: Iterable[MessageRecord],
    batch_size: int,
) -> int:
    batch: List[MessageRecord] = []
    total = 0

    for record in messages:
        batch.append(record)
        if len(batch) >= batch_size:
            total += upsert_batch(client, collection_name, model, batch)
            batch.clear()

    if batch:
        total += upsert_batch(client, collection_name, model, batch)

    return total


def upsert_batch(
    client: QdrantClient,
    collection_name: str,
    model: SentenceTransformer,
    batch: List[MessageRecord],
) -> int:
    texts = [record.text for record in batch]
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)

    points = []
    for record, vector in zip(batch, embeddings):
        payload = {
            "text": record.text,
            "timestamp": record.timestamp,
            "chat_id": record.chat_id,
            "chat_display_name": record.chat_display_name,
            "sender_id": record.sender_id,
            "sender_name": record.sender_name,
            "participants": record.participants,
        }
        points.append(
            PointStruct(
                id=record.message_id,
                vector=vector.tolist(),
                payload=payload,
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    return len(points)


def main() -> None:
    args = parse_arguments()

    contact_index = load_contact_index(args.contacts_file)
    db = DBHandler(args.db_path)
    db.connect()

    exporter = Exporter(output_dir=".", output_format="json", contact_index=contact_index)

    client = QdrantClient(
        host=args.host,
        port=args.port,
        api_key=args.api_key,
        https=args.https,
    )

    if not client.collection_exists(args.collection_name):
        raise SystemExit(
            f"Collection '{args.collection_name}' does not exist. Run qdrant_setup.py first."
        )

    model = SentenceTransformer(args.model_name)

    print(
        f"Embedding messages from {args.db_path} into collection '{args.collection_name}' "
        f"using model {args.model_name}."
    )

    messages = iter_messages(db, exporter, args.date_range, args.contacts)
    total = embed_and_store(
        client,
        args.collection_name,
        model,
        tqdm(messages, desc="Embedding messages"),
        args.batch_size,
    )

    db.close()
    print(f"Stored {total} message embeddings in Qdrant.")


if __name__ == "__main__":
    main()
