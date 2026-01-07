import argparse
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, VectorParams


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or recreate a Qdrant collection for storing exported iMessage metadata."
        )
    )
    parser.add_argument("--host", default="127.0.0.1", help="Qdrant server host.")
    parser.add_argument("--port", type=int, default=6333, help="Qdrant server port.")
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key for Qdrant (omit if authentication is disabled).",
    )
    parser.add_argument(
        "--https",
        action="store_true",
        help="Use HTTPS when connecting to Qdrant (requires server support).",
    )
    parser.add_argument(
        "--collection-name",
        default="messages",
        help="Collection name to create if it does not exist (default: messages).",
    )
    parser.add_argument(
        "--vector-size",
        type=int,
        default=384,
        help="Vector size to enable collection creation (default: 384).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Drop and recreate the collection if it already exists.",
    )
    return parser.parse_args()


def connect_to_qdrant(
    host: str, port: int, api_key: Optional[str], https: bool
) -> QdrantClient:
    client = QdrantClient(host=host, port=port, api_key=api_key, https=https)
    print(f"Connected to Qdrant at {host}:{port} (https={https}).")
    return client


def ensure_collection(client: QdrantClient, name: str, vector_size: int) -> None:
    if client.collection_exists(name):
        print(f"Collection '{name}' already exists.")
        return

    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    print(f"Created collection '{name}' with vector size {vector_size}.")

    # Schema definition
    schema = {
        "text": PayloadSchemaType.TEXT,
        "timestamp": PayloadSchemaType.DATETIME,
        "chat_id": PayloadSchemaType.INTEGER,
        "chat_display_name": PayloadSchemaType.KEYWORD,
        "sender_id": PayloadSchemaType.KEYWORD,
        "sender_name": PayloadSchemaType.KEYWORD,
        "participants": PayloadSchemaType.KEYWORD,
    }

    for field_name, field_schema in schema.items():
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=field_schema,
        )

    print(f"Added payload indexes for fields: {', '.join(schema.keys())}.")


def drop_collection(client: QdrantClient, name: str) -> None:
    if client.collection_exists(name):
        client.delete_collection(name)
        print(f"Dropped existing collection '{name}'.")


def main() -> None:
    args = parse_arguments()
    client = connect_to_qdrant(
        host=args.host, port=args.port, api_key=args.api_key, https=args.https
    )

    if args.overwrite:
        drop_collection(client, args.collection_name)

    ensure_collection(client, args.collection_name, args.vector_size)


if __name__ == "__main__":
    main()
