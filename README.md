# iMessage Export Utility

A Python utility to export iMessage chats from your local `chat.db` database into organized, human-readable files (TXT, JSON, or CSV).

## Features

- **Organized Export**: Groups messages by Contact/Group and then by Date.
- **Multiple Formats**: Supports `.txt`, `.json`, and `.csv`.
- **Smart Naming**: Uses contact names, group names, or phone numbers to name folders.
- **Filtering**:
  - Filter by specific contacts.
  - Filter by date range.
- **Progress Tracking**: Shows a progress bar for large databases.

## Setup
### Install dependencies

```bash
pip install -r requirements.txt
```

> The sync service uses PyYAML for YAML config files. If you prefer JSON configs, PyYAML is still harmless to install.

### Locate your `chat.db`

On macOS, your iMessage database is located at: `~/Library/Messages/chat.db`.

> **Important**: For privacy and safety, copy this file to the `messages/` folder in this project rather than running the exporter directly against the live database.

```bash
mkdir -p messages
cp ~/Library/Messages/chat.db messages/
```

*Note: You might need to grant Full Disk Access to your terminal or copy operation if macOS restricts access.*

## Usage

### Background sync service

`message_sync.py` runs as a long-lived process that copies your Messages database into `./messages/` every six hours by default, handling optional WAL/SHM files to keep a consistent snapshot.

```bash
python message_sync.py start          # Start in the foreground
python message_sync.py start --daemon # Start detached
python message_sync.py stop           # Stop the running service
python message_sync.py status         # Check if the service is running
python message_sync.py sync-now       # Run an immediate one-off sync
```

> If you start the service with a custom configuration file, use the same `--config` flag when calling `stop` or `status` so the PID file can be located.

Key behaviors:
- Syncs immediately on startup, then every `sync_interval` hours.
- Handles SIGINT/SIGTERM for graceful shutdown and cleans up its PID file.
- Prevents multiple instances by tracking `message_sync.pid`.
- Uses exponential backoff when files are locked and verifies file sizes after copy.
- Creates optional timestamped backups and prunes old ones.
- Logs to both console and `./logs/sync.log` with rotation (5 MB, 3 files by default).

#### Configuration

You can configure the service with `config.yaml` or `config.json` (auto-discovered), or via CLI flags that override file values.

Default `config.yaml` (created in the repo):

```yaml
sync_interval: 6        # hours
source_path: ~/Library/Messages/chat.db
destination_dir: ./messages
keep_backups: 3         # number of backup copies to retain (0 to disable)
log_level: INFO
log_dir: ./logs
pid_file: ./message_sync.pid
state_file: ./message_sync_state.json
log_max_bytes: 5242880  # 5 MB
log_backup_count: 3
```

Examples:

```bash
python message_sync.py start --interval 3
python message_sync.py start --source-path "/Volumes/Backup/chat.db"
python message_sync.py sync-now --destination-dir ./messages --keep-backups 5
```

The last successful sync timestamp is recorded in `message_sync_state.json`.

#### LaunchAgent (macOS auto-start)

Use the provided sample plist to run the service when you log in:

```bash
./install.sh   # writes ~/Library/LaunchAgents/com.user.messagesync.plist and loads it
```

`install.sh` assumes macOS and defaults to `/usr/bin/python3`; override with `PYTHON_BIN=/path/to/python3 ./install.sh`. The generated LaunchAgent:
- Starts the service at login in daemon mode
- Keeps it alive if it exits

You can also manually copy `com.user.messagesync.plist` to `~/Library/LaunchAgents/` and run:

```bash
launchctl load ~/Library/LaunchAgents/com.user.messagesync.plist
```

### Export utility

Run the script from the command line:

```bash
python imessage_export.py
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--db-path` | Path to the `chat.db` file. | `messages/chat.db` |
| `--output-dir` | Directory to save exported chats. | `imessage_export` |
| `--format` | Output format: `txt`, `json`, or `csv`. | `txt` |
| `--contacts` | Filter by specific contact names or numbers (space separated). | (All contacts) |
| `--date-range` | Filter by date range (`YYYY-MM-DD YYYY-MM-DD`). | (All dates) |
| `--contacts-file` | Path to a `.vcf` vCard file for resolving phone numbers to contact names. | `contacts/contacts.vcf` |

### Examples

**Export all chats to JSON:**
```bash
python imessage_export.py --format json
```

**Export chats with specific people:**
```bash
python imessage_export.py --contacts "Alice" "Bob"
```

**Export chats from January 2024:**
```bash
python imessage_export.py --date-range 2024-01-01 2024-01-31
```

**Custom paths:**
```bash
python imessage_export.py --db-path ./my_backup/chat.db --output-dir ./my_export
```

**Resolve phone numbers to contact names with vCards:**
Place your exported contacts at `contacts/contacts.vcf` (or provide a custom path with `--contacts-file`) to have chat folders and message senders labeled with the matching contact names whenever available.

## Qdrant collection setup

If you want to store exported messages in Qdrant, use `qdrant_setup.py` to create a collection with columns for the message text, message date, sender name, and chat name stored as payload fields.

```bash
python qdrant_setup.py --host 127.0.0.1 --port 6333 \
  --collection-name messages
```

The script:
- Connects to Qdrant (optionally with `--api-key` and `--https` for TLS).
- Creates the collection (or replaces it with `--overwrite`) with a minimal vector stub.
- Adds payload indexes for:
  - `message` (text)
  - `sent_at` (keyword, e.g., ISO 8601 string)
  - `sender_name` (keyword)
  - `chat_name` (keyword)

Use `--vector-size` to adjust the placeholder vector size if you plan to add embeddings later.

## Output Structure

The exported data is organized as follows:

```
imessage_export/
├── Alice/
│   ├── 2024-01-15/
│   │   └── messages.txt
│   ├── 2024-01-16/
│   │   └── messages.txt
└── Family Group/
    ├── 2024-01-15/
    │   └── messages.txt
```

## Troubleshooting

- **Database not found**: Ensure you copied the `chat.db` to the correct path.
- **Permission errors**: If copying `chat.db` from `~/Library/Messages/` fails, make sure your terminal application has "Full Disk Access" in macOS System Settings > Privacy & Security.

## License

[MIT](LICENSE)
