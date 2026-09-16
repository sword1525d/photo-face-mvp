"""Busca facial: comparação de embeddings por similaridade de cosseno.

Esta é a **única** parte do sistema que sabe "como" os embeddings são
comparados. Para trocar NumPy por pgvector/FAISS no futuro basta criar uma
outra classe com o mesmo método ``search(event_id, embedding)`` e injetá-la
em ``FaceSearchService``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

from .photo_service import PhotoService, decode_embedding

logger = logging.getLogger(__name__)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Similaridade de cosseno entre dois vetores."""
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(a, b) / denominator)


@dataclass
class SearchResult:
    photo_id: int
    similarity: float
    filename: str
    thumbnail_path: str | None
    original_path: str
    created_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "photo_id": self.photo_id,
            "similarity": round(self.similarity, 4),
            "filename": self.filename,
            "thumbnail_url": f"/storage/{self.thumbnail_path}" if self.thumbnail_path else None,
            "original_url": f"/storage/{self.original_path}" if self.original_path else None,
        }


class SearchBackend(Protocol):
    """Contrato mínimo de um backend de busca (NumPy hoje, pgvector/FAISS depois)."""

    def search(
        self,
        event_id: int,
        embedding: np.ndarray,
        threshold: float,
        limit: int,
    ) -> list[SearchResult]: ...


class NumpySearchBackend:
    """Carrega todos os embeddings do evento em memória e compara com NumPy.

    Aceitável para o MVP (milhares de rostos por evento).
    """

    def __init__(self, photo_service: PhotoService) -> None:
        self.photo_service = photo_service

    def search(
        self,
        event_id: int,
        embedding: np.ndarray,
        threshold: float,
        limit: int,
    ) -> list[SearchResult]:
        query = np.asarray(embedding, dtype=np.float32).ravel()
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0.0:
            return []

        rows = self.photo_service.face_rows_for_event(event_id)
        if not rows:
            return []

        vectors: list[np.ndarray] = []
        metadata: list[tuple[int, str, str | None, str, str | None]] = []
        for row in rows:
            vector = decode_embedding(row["embedding"], row["dim"])
            if vector.size != query.size:
                continue
            vectors.append(vector)
            metadata.append(
                (
                    int(row["photo_id"]),
                    row["filename"],
                    row["thumbnail_path"],
                    row["original_path"],
                    row["created_at"],
                )
            )

        if not vectors:
            return []

        matrix = np.vstack(vectors)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        similarities = (matrix / norms) @ (query / query_norm)

        # Uma foto pode ter vários rostos compatíveis: guardamos apenas o melhor.
        best: dict[int, int] = {}
        for index, (photo_id, *_rest) in enumerate(metadata):
            current = best.get(photo_id)
            if current is None or similarities[index] > similarities[current]:
                best[photo_id] = index

        results = [
            SearchResult(
                photo_id=metadata[index][0],
                similarity=float(similarities[index]),
                filename=metadata[index][1],
                thumbnail_path=metadata[index][2],
                original_path=metadata[index][3],
                created_at=metadata[index][4],
            )
            for index in best.values()
            if float(similarities[index]) >= threshold
        ]
        results.sort(key=lambda item: item.similarity, reverse=True)
        return results[:limit] if limit else results


class FaceSearchService:
    """Fachada de busca facial usada pelas rotas."""

    def __init__(
        self,
        photo_service: PhotoService,
        backend: SearchBackend | None = None,
        threshold: float = 0.45,
        max_results: int = 300,
    ) -> None:
        self.backend: SearchBackend = backend or NumpySearchBackend(photo_service)
        self.threshold = float(threshold)
        self.max_results = int(max_results)

    def search(
        self,
        event_id: int,
        embedding: np.ndarray,
        threshold: float | None = None,
        limit: int | None = None,
    ) -> tuple[list[SearchResult], float]:
        """Devolve ``(resultados, tempo_em_segundos)``."""
        threshold = self.threshold if threshold is None else float(threshold)
        limit = self.max_results if limit is None else int(limit)

        started = time.perf_counter()
        results = self.backend.search(event_id, embedding, threshold, limit)
        elapsed = time.perf_counter() - started

        logger.info(
            "Busca facial event=%s resultados=%s threshold=%.2f tempo=%.3fs",
            event_id,
            len(results),
            threshold,
            elapsed,
        )
        return results, elapsed

    @staticmethod
    def top_similarity(results: Sequence[SearchResult]) -> float:
        return float(results[0].similarity) if results else 0.0
