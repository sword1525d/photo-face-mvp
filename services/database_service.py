"""Acesso ao SQLite: conexão, schema e helpers de consulta.

Optamos por abrir uma conexão nova a cada operação (e fechá-la em seguida)
porque é simples, é seguro com o servidor multi-thread do Flask e evita o
clássico problema de conexão fechada por garbage collector.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

logger = logging.getLogger(__name__)

# Timestamps em UTC no formato ISO-8601 (ordenáveis lexicograficamente).
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    event_date  TEXT,
    cover_path  TEXT,
    created_at  TEXT    NOT NULL DEFAULT ({NOW})
);

CREATE TABLE IF NOT EXISTS photos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    original_path  TEXT    NOT NULL,
    thumbnail_path TEXT,
    raw_path       TEXT,
    status         TEXT    NOT NULL DEFAULT 'pending',
    error_message  TEXT,
    faces_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT ({NOW})
);

CREATE INDEX IF NOT EXISTS idx_photos_event  ON photos(event_id);
CREATE INDEX IF NOT EXISTS idx_photos_status ON photos(event_id, status);

CREATE TABLE IF NOT EXISTS faces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    embedding  BLOB    NOT NULL,
    dim        INTEGER NOT NULL,
    bbox       TEXT,
    confidence REAL,
    created_at TEXT    NOT NULL DEFAULT ({NOW})
);

CREATE INDEX IF NOT EXISTS idx_faces_event ON faces(event_id);
CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id);
"""


class DatabaseService:
    """Wrapper fino em volta do módulo ``sqlite3``."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = str(Path(database_path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
        logger.info("Banco de dados inicializado em %s", self.path)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Adiciona colunas novas em bancos criados por versões antigas."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(photos)")}
        additions = {
            "error_message": "ALTER TABLE photos ADD COLUMN error_message TEXT",
            "faces_count": "ALTER TABLE photos ADD COLUMN faces_count INTEGER NOT NULL DEFAULT 0",
            "thumbnail_path": "ALTER TABLE photos ADD COLUMN thumbnail_path TEXT",
            "raw_path": "ALTER TABLE photos ADD COLUMN raw_path TEXT",
        }
        for column, statement in additions.items():
            if column not in existing:
                conn.execute(statement)
        event_columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        if "cover_path" not in event_columns:
            conn.execute("ALTER TABLE events ADD COLUMN cover_path TEXT")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Executa um comando e devolve ``lastrowid`` (ou rowcount quando 0)."""
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.lastrowid or cursor.rowcount

    def execute_many(self, sql: str, params: Iterable[Sequence[Any]]) -> int:
        with self.connect() as conn:
            cursor = conn.executemany(sql, params)
            return cursor.rowcount

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def healthcheck(self) -> bool:
        try:
            return bool(self.scalar("SELECT 1", default=0))
        except sqlite3.Error:  # pragma: no cover - apenas defensivo
            logger.exception("Falha ao consultar o banco de dados")
            return False
