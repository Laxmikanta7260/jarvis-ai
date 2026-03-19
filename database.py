import sqlite3
from datetime import datetime

DB_PATH = "jarvis.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        file_name TEXT,
        file_content TEXT DEFAULT '',
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT,
        role TEXT,
        content TEXT,
        created_at TEXT
    )
    """)

    # migration for older DBs that do not yet have file_content
    cursor.execute("PRAGMA table_info(conversations)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "file_content" not in columns:
        cursor.execute("""
        ALTER TABLE conversations
        ADD COLUMN file_content TEXT DEFAULT ''
        """)

    conn.commit()
    conn.close()


def save_conversation(conversation_id, file_name="New Chat", file_content=""):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT OR IGNORE INTO conversations (id, file_name, file_content, created_at)
    VALUES (?, ?, ?, ?)
    """, (conversation_id, file_name, file_content, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def update_conversation_file_name(conversation_id, file_name):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE conversations
    SET file_name = ?
    WHERE id = ?
    """, (file_name, conversation_id))

    conn.commit()
    conn.close()


def update_conversation_file_content(conversation_id, file_content):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE conversations
    SET file_content = ?
    WHERE id = ?
    """, (file_content, conversation_id))

    conn.commit()
    conn.close()


def get_conversation(conversation_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, file_name, file_content, created_at
    FROM conversations
    WHERE id = ?
    """, (conversation_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "file_name": row["file_name"] or "New Chat",
        "file_content": row["file_content"] or "",
        "created_at": row["created_at"]
    }


def save_message(conversation_id, role, content):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO messages (conversation_id, role, content, created_at)
    VALUES (?, ?, ?, ?)
    """, (conversation_id, role, content, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def get_messages(conversation_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, role, content, created_at
    FROM messages
    WHERE conversation_id = ?
    ORDER BY id ASC
    """, (conversation_id,))

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"]
        }
        for row in rows
    ]


def get_conversation_messages(conversation_id):
    return get_messages(conversation_id)


def get_all_conversations():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id, file_name, created_at
    FROM conversations
    ORDER BY created_at DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": row["id"],
            "file_name": row["file_name"] if row["file_name"] else "New Chat",
            "created_at": row["created_at"]
        }
        for row in rows
    ]