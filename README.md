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

1. **Prerequisites**: Python 3.6+.
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   *(Note: The only dependency is `tqdm` for the progress bar. The script will work without it.)*

3. **Locate your `chat.db`**:
   - On macOS, your iMessage database is located at: `~/Library/Messages/chat.db`.
   - **Important**: For privacy and safety, copy this file to the `messages/` folder in this project rather than running the script directly against the live database.

   ```bash
   mkdir messages
   cp ~/Library/Messages/chat.db messages/
   ```
   *Note: You might need to grant Full Disk Access to your terminal or copy operation if macOS restricts access.*

## Usage

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
