"""Persistência de fotos e rostos (tabelas ``photos`` e ``faces``)."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Iterable, Sequence

import numpy as np

from .database_service import DatabaseService

logger = logging.getLogger(__name__)

# Status possíveis de uma foto.
STATUS_PENDING = "pending"
STATUS_PROCESSED = "processed"
STATUS_NO_FACES = "no_faces"
STATUS_ERROR = "error"

STATUS_LABELS = {
    STATUS_PENDING: "Aguardando",
    STATUS_PROCESSED: "Processada",
    STATUS_NO_FACES: "Sem rostos",
    STATUS_ERROR: "Erro",
}


def encode_embedding(embedding: np.ndarray) -> bytes:
    """Converte um embedding NumPy em BLOB (float32)."""
    array = np.ascontiguousarray(np.asarray(embedding, dtype=np.float32).ravel())
    return array.tobytes()


def decode_embedding(blob: bytes, dim: int | None = None) -> np.ndarray:
    """Converte um BLOB em array NumPy float32."""
    array = np.frombuffer(blob, dtype=np.float32)
    if dim is not None and array.size != dim:
        # Embedding de dimensão diferente: devolve vazio e deixa o chamador filtrar.
        logger.warning("Embedding com dimensão inesperada: %s != %s", array.size, dim)
        return np.empty(0, dtype=np.float32)
    return array


class PhotoService:
    def __init__(self, db: DatabaseService) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Fotos
    # ------------------------------------------------------------------
    def create(
        self,
        event_id: int,
        filename: str,
        original_path: str,
        thumbnail_path: str | None,
        status: str = STATUS_PENDING,
        raw_path: str | None = None,
    ) -> int:
        photo_id = self.db.execute(
            """
            INSERT INTO photos (event_id, filename, original_path, thumbnail_path, raw_path, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, filename, original_path, thumbnail_path, raw_path, status),
        )
        logger.info("Foto criada id=%s event=%s arquivo=%s", photo_id, event_id, filename)
        return int(photo_id)

    def get(self, photo_id: int) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM photos WHERE id = ?", (photo_id,))

    def list_by_event(self, event_id: int, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM photos WHERE event_id = ? ORDER BY id DESC"
        params: list[Any] = [event_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.db.query(sql, params)

    def count_by_event(self, event_id: int) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM photos WHERE event_id = ?", (event_id,)))

    def set_status(
        self,
        photo_id: int,
        status: str,
        *,
        error_message: str | None = None,
        faces_count: int | None = None,
    ) -> None:
        fields = ["status = ?", "error_message = ?"]
        params: list[Any] = [status, error_message]
        if faces_count is not None:
            fields.append("faces_count = ?")
            params.append(int(faces_count))
        params.append(photo_id)
        self.db.execute(f"UPDATE photos SET {', '.join(fields)} WHERE id = ?", params)

    def delete(self, photo_id: int) -> sqlite3.Row | None:
        photo = self.get(photo_id)
        if photo is None:
            return None
        self.db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
        self.db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        logger.info("Foto removida id=%s event=%s", photo_id, photo["event_id"])
        return photo

    def delete_by_event(self, event_id: int) -> list[sqlite3.Row]:
        photos = self.list_by_event(event_id)
        self.db.execute("DELETE FROM faces WHERE event_id = ?", (event_id,))
        self.db.execute("DELETE FROM photos WHERE event_id = ?", (event_id,))
        return photos

    def cover_path(self, event_id: int) -> str | None:
        return self.db.scalar(
            "SELECT thumbnail_path FROM photos WHERE event_id = ? AND thumbnail_path IS NOT NULL "
            "AND status != ? ORDER BY id ASC LIMIT 1",
            (event_id, STATUS_ERROR),
            default=None,
        )

    def cover_paths(self) -> dict[int, str]:
        rows = self.db.query(
            """
            SELECT p.event_id AS event_id, p.thumbnail_path AS thumbnail_path
            FROM photos p
            JOIN (
                SELECT event_id, MIN(id) AS first_id
                FROM photos
                WHERE thumbnail_path IS NOT NULL
                GROUP BY event_id
            ) f ON f.first_id = p.id
            """
        )
        return {int(row["event_id"]): row["thumbnail_path"] for row in rows}

    # ------------------------------------------------------------------
    # Rostos / embeddings
    # ------------------------------------------------------------------
    def replace_faces(self, photo_id: int, event_id: int, faces: Iterable[Any]) -> int:
        """Regrava os rostos de uma foto. ``faces`` = iterável de objetos com
        ``embedding``, ``bbox`` e ``confidence``."""
        self.db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
        payload = []
        for face in faces:
            embedding = encode_embedding(face.embedding)
            payload.append(
                (
                    photo_id,
                    event_id,
                    embedding,
                    len(np.frombuffer(embedding, dtype=np.float32)),
                    json.dumps(list(face.bbox)),
                    float(face.confidence),
                )
            )
        if payload:
            self.db.execute_many(
                """
                INSERT INTO faces (photo_id, event_id, embedding, dim, bbox, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(payload)

    def count_faces(self, photo_id: int) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM faces WHERE photo_id = ?", (photo_id,)))

    def face_rows_for_event(self, event_id: int, only_processed: bool = True) -> list[sqlite3.Row]:
        sql = """
            SELECT f.id AS face_id,
                   f.photo_id AS photo_id,
                   f.embedding AS embedding,
                   f.dim AS dim,
                   f.confidence AS confidence,
                   p.filename AS filename,
                   p.thumbnail_path AS thumbnail_path,
                   p.original_path AS original_path,
                   p.created_at AS created_at,
                   p.status AS status
            FROM faces f
            JOIN photos p ON p.id = f.photo_id
            WHERE f.event_id = ?
        """
        params: Sequence[Any] = (event_id,)
        if only_processed:
            sql += " AND p.status = ?"
            params = (event_id, STATUS_PROCESSED)
        sql += " ORDER BY p.id DESC"
        return self.db.query(sql, params)
