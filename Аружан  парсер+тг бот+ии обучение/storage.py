"""
storage.py
- SQLite: трекинг уже обработанных (channel_id, message_id), чтобы не
  анализировать повторно одно и то же сообщение при перезапуске скрипта.
- Локальный JSONL fallback: если сервер недоступен, отчёт не теряется.
"""

import sqlite3
import json
import os
import threading

DB_PATH = os.environ.get("DEDUP_DB_PATH", "processed.db")
FALLBACK_FILE = os.environ.get("OUTPUT_FILE", "reports_fallback.jsonl")

_lock = threading.Lock()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS processed (
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (channel_id, message_id)
        )"""
    )
    conn.commit()
    conn.close()


def is_processed(channel_id: int, message_id: int) -> bool:
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "SELECT 1 FROM processed WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        result = cur.fetchone() is not None
        conn.close()
        return result


def mark_processed(channel_id: int, message_id: int):
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO processed (channel_id, message_id) VALUES (?, ?)",
            (channel_id, message_id),
        )
        conn.commit()
        conn.close()


def save_fallback(report: dict):
    """Сохраняет отчёт локально в JSONL, если сервер недоступен."""
    with _lock:
        with open(FALLBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
