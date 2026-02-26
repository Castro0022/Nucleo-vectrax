import sqlite3
from datetime import datetime
import os

DB_PATH = "vault/vectrax_ledger.db"

def init_db():
    os.makedirs("vault", exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            source TEXT,
            content TEXT,
            tags TEXT,
            sentiment REAL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            key TEXT PRIMARY KEY,
            value TEXT,
            confidence REAL,
            updated_at DATETIME
        )
    """)

    conn.commit()
    conn.close()

def add_event(source, content, tags=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO events (timestamp, source, content, tags) VALUES (?, ?, ?, ?)",
        (datetime.now(), source, content, tags)
    )

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Ledger inicializado")
