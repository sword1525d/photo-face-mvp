"""Regras de negócio de eventos (tabela ``events``)."""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from typing import Any

from .database_service import DatabaseService
from .photo_service import (
    STATUS_ERROR,
    STATUS_NO_FACES,
    STATUS_PENDING,
    STATUS_PROCESSED,
)

logger = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """'Corrida Manaus 2026' -> 'corrida-manaus-2026'."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_only = _SLUG_STRIP.sub("-", ascii_only.lower())
    return ascii_only.strip("-")[:80]


class EventService:
    def __init__(self, db: DatabaseService) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    def list_events(self) -> list[sqlite3.Row]:
        return self.db.query(
            """
            SELECT e.*,
                   (SELECT COUNT(*) FROM photos p WHERE p.event_id = e.id) AS photo_count,
                   (SELECT COUNT(*) FROM faces f WHERE f.event_id = e.id) AS face_count
            FROM events e
            ORDER BY e.id DESC
            """
        )

    def get(self, event_id: int) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))

    def get_by_slug(self, slug: str) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM events WHERE slug = ?", (slug,))

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------
    def create(self, name: str, description: str = "", event_date: str | None = None) -> sqlite3.Row:
        name = (name or "").strip()
        if not name:
            raise ValueError("O nome do evento é obrigatório.")

        base_slug = slugify(name) or "evento"
        slug = self._unique_slug(base_slug)
        event_id = self.db.execute(
            """
            INSERT INTO events (name, slug, description, event_date)
            VALUES (?, ?, ?, ?)
            """,
            (name, slug, (description or "").strip(), event_date or None),
        )
        logger.info("Evento criado id=%s slug=%s", event_id, slug)
        return self.get(int(event_id))  # type: ignore[return-value]

    def _unique_slug(self, base: str) -> str:
        slug = base
        suffix = 2
        while self.get_by_slug(slug) is not None:
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    def set_cover(self, event_id: int, cover_path: str | None) -> None:
        self.db.execute("UPDATE events SET cover_path = ? WHERE id = ?", (cover_path, event_id))

    def delete(self, event_id: int) -> bool:
        event = self.get(event_id)
        if event is None:
            return False
        self.db.execute("DELETE FROM faces WHERE event_id = ?", (event_id,))
        self.db.execute("DELETE FROM photos WHERE event_id = ?", (event_id,))
        self.db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        logger.info("Evento removido id=%s", event_id)
        return True

    # ------------------------------------------------------------------
    # Estatísticas
    # ------------------------------------------------------------------
    def stats(self, event_id: int) -> dict[str, int]:
        row = self.db.query_one(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS processed,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS no_faces,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS errors,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS pending,
                COALESCE(SUM(faces_count), 0) AS faces
            FROM photos
            WHERE event_id = ?
            """,
            (STATUS_PROCESSED, STATUS_NO_FACES, STATUS_ERROR, STATUS_PENDING, event_id),
        )
        stats: dict[str, Any] = {key: int(row[key] or 0) for key in row.keys()} if row else {}
        stats.setdefault("total", 0)
        stats.setdefault("processed", 0)
        stats.setdefault("no_faces", 0)
        stats.setdefault("errors", 0)
        stats.setdefault("pending", 0)
        stats.setdefault("faces", 0)
        return stats
