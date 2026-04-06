import hashlib
import hmac
import os
import sqlite3
import threading
from typing import Dict, List, Optional


class ChatDatabase:
    def __init__(self, db_path: str = "data/pulse_chat.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_name TEXT NOT NULL,
                    username TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            connection.commit()

        self.ensure_room("lobby", "system")

    def _hash_password(self, password: str, salt: bytes) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            100000
        ).hex()

    def ensure_room(self, room_name: str, created_by: str):
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO rooms (name, created_by)
                VALUES (?, ?)
                """,
                (room_name, created_by)
            )
            connection.commit()

    def list_rooms(self) -> List[str]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT name FROM rooms ORDER BY name ASC"
            ).fetchall()
        return [row["name"] for row in rows]

    def register_or_login(self, username: str, password: str) -> Dict[str, str]:
        with self.lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, password_hash, password_salt
                FROM users
                WHERE username = ?
                """,
                (username,)
            ).fetchone()

            if row is None:
                salt = os.urandom(16)
                password_hash = self._hash_password(password, salt)
                connection.execute(
                    """
                    INSERT INTO users (username, password_hash, password_salt)
                    VALUES (?, ?, ?)
                    """,
                    (username, password_hash, salt.hex())
                )
                connection.commit()
                return {"status": "registered"}

            salt = bytes.fromhex(row["password_salt"])
            password_hash = self._hash_password(password, salt)
            if hmac.compare_digest(password_hash, row["password_hash"]):
                return {"status": "authenticated"}

        return {"status": "invalid_password"}

    def save_message(
        self,
        room_name: str,
        username: str,
        message_type: str,
        content: str,
        timestamp: str
    ):
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    room_name, username, message_type, content, timestamp
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (room_name, username, message_type, content, timestamp)
            )
            connection.commit()

    def get_recent_messages(
        self,
        room_name: str,
        limit: int = 20
    ) -> List[Dict[str, str]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT username, content, timestamp
                FROM messages
                WHERE room_name = ? AND message_type = 'chat'
                ORDER BY id DESC
                LIMIT ?
                """,
                (room_name, limit)
            ).fetchall()

        return [
            {
                "username": row["username"],
                "content": row["content"],
                "timestamp": row["timestamp"]
            }
            for row in reversed(rows)
        ]
