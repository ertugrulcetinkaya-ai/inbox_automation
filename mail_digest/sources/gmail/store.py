"""SQLite cache for normalized Gmail messages and atomic checkpoints."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


MESSAGE_COLUMNS = (
    "gmail_message_id", "thread_id", "internal_date_ms", "sender", "subject",
    "date", "received_local_date", "content", "source_message_id", "raw_source",
    "updated_at_ms",
)


class GmailStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self.connection = sqlite3.connect(str(self.path))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._initialize()

    def close(self):
        self.connection.close()

    def _initialize(self):
        columns = """
            gmail_message_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            internal_date_ms INTEGER NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            date TEXT NOT NULL,
            received_local_date TEXT NOT NULL,
            content TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            raw_source TEXT NOT NULL,
            updated_at_ms INTEGER NOT NULL
        """
        with self.connection:
            self.connection.execute(f"CREATE TABLE IF NOT EXISTS messages ({columns})")
            self.connection.execute(f"CREATE TABLE IF NOT EXISTS staging_messages ({columns})")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', '1')"
            )

    def checkpoint(self):
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key='history_id'"
        ).fetchone()
        return row[0] if row else None

    def clear_staging(self):
        with self.connection:
            self.connection.execute("DELETE FROM staging_messages")

    @staticmethod
    def _db_values(record):
        return (
            record["gmail_message_id"], record.get("thread_id", ""),
            record["internal_date_ms"], record.get("sender", ""),
            record.get("subject", ""), record.get("date", ""),
            record.get("received_local_date", ""), record.get("content", ""),
            record.get("source_message_id", ""), record.get("raw_source", ""),
            int(time.time() * 1000),
        )

    def _upsert(self, table, record):
        placeholders = ",".join("?" for _ in MESSAGE_COLUMNS)
        assignments = ",".join(
            f"{column}=excluded.{column}" for column in MESSAGE_COLUMNS[1:]
        )
        self.connection.execute(
            f"INSERT INTO {table} ({','.join(MESSAGE_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(gmail_message_id) DO UPDATE SET {assignments}",
            self._db_values(record),
        )

    def stage_upsert(self, record):
        with self.connection:
            self._upsert("staging_messages", record)

    def stage_delete(self, message_id):
        with self.connection:
            self.connection.execute(
                "DELETE FROM staging_messages WHERE gmail_message_id=?", (message_id,)
            )

    def activate_staging(self, history_id, cutoff_ms):
        with self.connection:
            self.connection.execute("DELETE FROM messages")
            self.connection.execute(
                f"INSERT INTO messages ({','.join(MESSAGE_COLUMNS)}) "
                f"SELECT {','.join(MESSAGE_COLUMNS)} FROM staging_messages"
            )
            self.connection.execute(
                "DELETE FROM messages WHERE internal_date_ms < ?", (cutoff_ms,)
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('history_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(history_id),),
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('updated_at_ms', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(int(time.time() * 1000)),),
            )
            self.connection.execute("DELETE FROM staging_messages")

    def apply_incremental(self, operations, history_id, cutoff_ms):
        with self.connection:
            for operation, payload in operations:
                if operation == "upsert":
                    self._upsert("messages", payload)
                else:
                    self.connection.execute(
                        "DELETE FROM messages WHERE gmail_message_id=?", (payload,)
                    )
            self.connection.execute(
                "DELETE FROM messages WHERE internal_date_ms < ?", (cutoff_ms,)
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('history_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(history_id),),
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('updated_at_ms', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(int(time.time() * 1000)),),
            )

    def records(self):
        rows = self.connection.execute(
            "SELECT * FROM messages ORDER BY internal_date_ms DESC, gmail_message_id DESC"
        ).fetchall()
        return [
            {
                "account": "ertugrul@cetinkayalar.com",
                "mailbox": "INBOX",
                "sender": row["sender"],
                "subject": row["subject"],
                "date": row["date"],
                "received_date": row["received_local_date"],
                "content": row["content"],
                "source_message_id": row["source_message_id"],
                "raw_source": row["raw_source"],
            }
            for row in rows
        ]
