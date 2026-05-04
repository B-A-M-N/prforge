"""
PRForge Database Backend.

Provides a unified interface for state logging, telemetry, and vector storage.
Defaults to local SQLite for the survival/fallback mode, with hooks ready to 
swap to PostgreSQL (e.g., pgvector) for the distributed mesh level.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger("prforge.db")

class PRForgeDB:
    def __init__(self, db_url: str = "sqlite://local"):
        self.db_url = db_url
        self.is_postgres = self.db_url.startswith("postgres")
        
        if self.is_postgres:
            # Postgres (pgvector) connection logic will go here
            self.pool = None 
        else:
            # Local SQLite fallback
            db_path = Path.home() / ".prforge-intel" / "index"
            db_path.mkdir(parents=True, exist_ok=True)
            self.sqlite_path = db_path / "metadata.sqlite"
            self.local = threading.local()
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        """Initialize SQLite schema if it doesn't exist."""
        with self.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS state_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS vector_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    text_content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            ''')
            # Create indexes
            conn.execute('CREATE INDEX IF NOT EXISTS idx_state_run ON state_logs(run_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_vector_run ON vector_chunks(run_id)')
            conn.commit()

    @contextmanager
    def get_connection(self) -> Generator[Any, None, None]:
        if self.is_postgres:
            # Yield postgres connection from pool
            yield None
        else:
            if not hasattr(self.local, "conn"):
                self.local.conn = sqlite3.connect(self.sqlite_path)
                self.local.conn.row_factory = sqlite3.Row
            yield self.local.conn

    def log_state(self, run_id: str, repo: str, phase: str, payload: dict) -> None:
        """Log state transitions for telemetry and audit trails."""
        ts = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            if self.is_postgres:
                pass # psycopg2/asyncpg insert
            else:
                conn.execute(
                    'INSERT INTO state_logs (run_id, repo, phase, payload, timestamp) VALUES (?, ?, ?, ?, ?)',
                    (run_id, repo, phase, json.dumps(payload), ts)
                )
                conn.commit()

    def store_vectors(self, run_id: str, chunks: list[dict]) -> None:
        """Store embedded chunks. Maps to pgvector or local JSON blobs in SQLite."""
        ts = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            if self.is_postgres:
                pass # pgvector bulk insert
            else:
                # SQLite fallback: Store embeddings as JSON strings
                # For local retrieval, we load into memory and use numpy/math (as done in intel_engine.py)
                data = [
                    (run_id, c.get("source", ""), c.get("id", ""), c.get("text", ""), json.dumps(c.get("embedding", [])), ts)
                    for c in chunks
                ]
                conn.executemany(
                    'INSERT INTO vector_chunks (run_id, source, chunk_id, text_content, embedding, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
                    data
                )
                conn.commit()

# Singleton instance
_db_instance: Optional[PRForgeDB] = None

def get_db(db_url: str = "sqlite://local") -> PRForgeDB:
    global _db_instance
    if _db_instance is None or _db_instance.db_url != db_url:
        _db_instance = PRForgeDB(db_url)
    return _db_instance
