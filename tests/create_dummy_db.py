import sqlite3
import os
import time

DB_PATH = "messages/chat.db"

def create_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Create tables
    c.execute('''
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE,
            text TEXT,
            date INTEGER,
            is_from_me INTEGER,
            handle_id INTEGER,
            cache_has_attachments INTEGER DEFAULT 0,
            service TEXT DEFAULT 'iMessage'
        )
    ''')

    c.execute('''
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE,
            chat_identifier TEXT,
            display_name TEXT,
            style INTEGER DEFAULT 45
        )
    ''')

    c.execute('''
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE,
            uncanonicalized_id TEXT,
            service TEXT DEFAULT 'iMessage'
        )
    ''')

    c.execute('''
        CREATE TABLE chat_message_join (
            chat_id INTEGER,
            message_id INTEGER,
            PRIMARY KEY (chat_id, message_id)
        )
    ''')

    c.execute('''
        CREATE TABLE chat_handle_join (
            chat_id INTEGER,
            handle_id INTEGER,
            PRIMARY KEY (chat_id, handle_id)
        )
    ''')

    # Insert Data
    # Handles
    c.execute("INSERT INTO handle (id, uncanonicalized_id) VALUES ('+15551234567', '5551234567')") # Alice
    alice_id = c.lastrowid
    c.execute("INSERT INTO handle (id, uncanonicalized_id) VALUES ('bob@example.com', 'bob@example.com')") # Bob
    bob_id = c.lastrowid
    c.execute("INSERT INTO handle (id, uncanonicalized_id) VALUES ('+15559876543', '5559876543')") # Charlie
    charlie_id = c.lastrowid

    # Chats
    # 1. Individual chat with Alice
    c.execute("INSERT INTO chat (guid, chat_identifier, display_name) VALUES ('iMessage;-;+15551234567', '+15551234567', '')")
    chat_alice_id = c.lastrowid
    c.execute(f"INSERT INTO chat_handle_join (chat_id, handle_id) VALUES ({chat_alice_id}, {alice_id})")

    # 2. Group chat (Alice, Bob, Charlie)
    c.execute("INSERT INTO chat (guid, chat_identifier, display_name) VALUES ('iMessage;+;chat12345', 'chat12345', 'The Squad')")
    chat_group_id = c.lastrowid
    c.execute(f"INSERT INTO chat_handle_join (chat_id, handle_id) VALUES ({chat_group_id}, {alice_id})")
    c.execute(f"INSERT INTO chat_handle_join (chat_id, handle_id) VALUES ({chat_group_id}, {bob_id})")
    c.execute(f"INSERT INTO chat_handle_join (chat_id, handle_id) VALUES ({chat_group_id}, {charlie_id})")

    # Messages
    # Apple Epoch: Jan 1 2001 00:00:00 UTC
    # 2024-01-15 10:00:00 UTC -> 727005600 seconds after Apple Epoch
    # Calculation: (2024-01-15 - 2001-01-01).total_seconds()
    # Approx: 23 years + leap days

    # 2001-01-01 is 978307200 unix timestamp
    # 2024-01-15 10:00:00 is 1705312800 unix timestamp
    # Difference: 727005600

    base_time = 727005600

    # Chat with Alice
    # Msg 1: From Alice
    c.execute(f"INSERT INTO message (text, date, is_from_me, handle_id) VALUES ('Hey Jules!', {base_time}, 0, {alice_id})")
    msg1_id = c.lastrowid
    c.execute(f"INSERT INTO chat_message_join (chat_id, message_id) VALUES ({chat_alice_id}, {msg1_id})")

    # Msg 2: Reply
    c.execute(f"INSERT INTO message (text, date, is_from_me, handle_id) VALUES ('Hi Alice!', {base_time + 60}, 1, 0)") # handle_id 0 usually means me? or maybe just check is_from_me
    msg2_id = c.lastrowid
    c.execute(f"INSERT INTO chat_message_join (chat_id, message_id) VALUES ({chat_alice_id}, {msg2_id})")

    # Group Chat
    # Msg 3: Bob
    c.execute(f"INSERT INTO message (text, date, is_from_me, handle_id) VALUES ('Party tonight?', {base_time + 3600}, 0, {bob_id})")
    msg3_id = c.lastrowid
    c.execute(f"INSERT INTO chat_message_join (chat_id, message_id) VALUES ({chat_group_id}, {msg3_id})")

    # Msg 4: Charlie
    c.execute(f"INSERT INTO message (text, date, is_from_me, handle_id) VALUES ('I am in!', {base_time + 3700}, 0, {charlie_id})")
    msg4_id = c.lastrowid
    c.execute(f"INSERT INTO chat_message_join (chat_id, message_id) VALUES ({chat_group_id}, {msg4_id})")

    # Msg 5: Next day (2024-01-16) -> +86400 seconds
    c.execute(f"INSERT INTO message (text, date, is_from_me, handle_id) VALUES ('It was fun!', {base_time + 86400 + 3600}, 0, {alice_id})")
    msg5_id = c.lastrowid
    c.execute(f"INSERT INTO chat_message_join (chat_id, message_id) VALUES ({chat_group_id}, {msg5_id})")

    # 3. Collision Test Chat (Another "The Squad")
    # This simulates a different group chat but with the same display name
    c.execute("INSERT INTO chat (guid, chat_identifier, display_name) VALUES ('iMessage;+;chat67890', 'chat67890', 'The Squad')")
    chat_collision_id = c.lastrowid
    c.execute(f"INSERT INTO chat_handle_join (chat_id, handle_id) VALUES ({chat_collision_id}, {alice_id})") # Just Alice in this one

    # Msg 6: Collision Chat
    c.execute(f"INSERT INTO message (text, date, is_from_me, handle_id) VALUES ('This is the other squad', {base_time}, 0, {alice_id})")
    msg6_id = c.lastrowid
    c.execute(f"INSERT INTO chat_message_join (chat_id, message_id) VALUES ({chat_collision_id}, {msg6_id})")

    conn.commit()
    conn.close()
    print(f"Created dummy database at {DB_PATH}")

if __name__ == "__main__":
    create_db()
