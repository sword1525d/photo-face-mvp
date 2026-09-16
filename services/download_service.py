"""Download dos arquivos originais das fotos (individual ou em ZIP).

Regra de qualidade: o que o usuário baixa é o **arquivo original** gravado no
storage (``photos.original_path``) — byte a byte, sem redimensionar, recortar
ou recomprimir. O thumbnail de 500px existe só para a prévia na tela.

Para fotos que vieram de RAW (``.nef``, ``.cr2`` …) o "original legível" é o
JPEG gerado na importação (``original_path``); o arquivo RAW de verdade fica em
``photos.raw_path`` e só é baixado quando alguém pede isso explicitamente
(``prefer_raw=True``), porque são dezenas de MB por foto.

Este módulo não conhece banco de dados nem Flask: recebe as linhas de
``photos`` já filtradas e devolve caminhos prontos para envio.
"""

from __future__ import annotations

import logging
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from uuid import uuid4

logger = logging.getLogger(__name__)

# Limites padrão (sobrescritos pelo Config).
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

# ZIPs de download que passaram disso são considerados sobras (ver `purge_stale`).
STALE_ARCHIVE_SECONDS = 15 * 60


class DownloadError(Exception):
    """Erro amigável de download — a mensagem é mostrada para o usuário."""


def human_bytes(size: int) -> str:
    """Formata bytes de forma curta (ex.: ``1.8 GB``)."""
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


@dataclass(slots=True)
class DownloadFile:
    """Um arquivo pronto para ser enviado ao navegador."""

    path: Path
    name: str      # nome sugerido no download
    size: int


class DownloadService:
    """Resolve o arquivo original de cada foto e monta ZIPs de download."""

    def __init__(
        self,
        storage_root: str | Path,
        temp_root: str | Path,
        max_files: int = DEFAULT_MAX_FILES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        stale_seconds: int = STALE_ARCHIVE_SECONDS,
    ) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.temp_root = Path(temp_root)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.max_files = int(max_files)
        self.max_total_bytes = int(max_total_bytes)
        self.stale_seconds = int(stale_seconds)

    # ------------------------------------------------------------------
    # Resolução dos arquivos
    # ------------------------------------------------------------------
    def resolve(self, photo_row, prefer_raw: bool = False) -> DownloadFile:
        """Devolve o arquivo original de uma foto (ou o RAW, se pedido)."""
        relative = None
        if prefer_raw and "raw_path" in photo_row.keys():
            relative = photo_row["raw_path"]
        if not relative:
            relative = photo_row["original_path"]
        if not relative:
            raise DownloadError("Esta foto não tem arquivo disponível para download.")

        path = (self.storage_root / relative).resolve()
        if not path.is_file():
            logger.warning("Arquivo de download ausente no disco: %s", path)
            raise DownloadError(
                "O arquivo original desta foto não está mais disponível no servidor."
            )
        return DownloadFile(path=path, name=self._download_name(photo_row, path), size=path.stat().st_size)

    def collect(self, photo_rows: Iterable, prefer_raw: bool = False) -> list[DownloadFile]:
        """Resolve vários arquivos aplicando os limites de quantidade/tamanho."""
        rows = list(photo_rows)
        if not rows:
            raise DownloadError("Selecione pelo menos uma foto para baixar.")
        if len(rows) > self.max_files:
            raise DownloadError(
                f"Selecione no máximo {self.max_files} fotos por download "
                f"(você marcou {len(rows)})."
            )

        files: list[DownloadFile] = []
        total = 0
        for row in rows:
            item = self.resolve(row, prefer_raw=prefer_raw)
            total += item.size
            if total > self.max_total_bytes:
                raise DownloadError(
                    f"As fotos selecionadas somam mais de {human_bytes(self.max_total_bytes)}. "
                    "Baixe em partes menores."
                )
            files.append(item)
        return files

    @staticmethod
    def _download_name(photo_row, path: Path) -> str:
        """Nome amigável para o arquivo baixado.

        Usa o nome que o fotógrafo deu ao arquivo (``photos.filename``), mas com
        a extensão do arquivo **real**: o RAW vira ``.jpg`` depois da conversão,
        então ``DSC_1.nef`` baixado como original chama-se ``DSC_1.jpg``.
        """
        reference = str(photo_row["filename"] or "").replace("\\", "/")
        stem = PurePosixPath(reference).stem.strip() or path.stem
        return f"{stem}{path.suffix.lower()}"

    # ------------------------------------------------------------------
    # ZIP
    # ------------------------------------------------------------------
    def build_zip(self, files: Sequence[DownloadFile]) -> Path:
        """Monta um ZIP temporário com os arquivos originais.

        Usa ``ZIP_STORED`` de propósito: as fotos já são JPEG/HEIC/RAW
        comprimidos, então recomprimir só gastaria CPU — e o arquivo dentro do
        ZIP continua **idêntico** ao original.
        """
        if not files:
            raise DownloadError("Nenhuma foto selecionada para baixar.")

        self.purge_stale()
        target = self.temp_root / f"download-{uuid4().hex}.zip"
        used: set[str] = set()
        try:
            with zipfile.ZipFile(
                target, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as archive:
                for item in files:
                    archive.write(item.path, arcname=_unique_name(item.name, used))
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            self.cleanup(target)
            logger.exception("Falha ao montar o ZIP de download")
            raise DownloadError(
                "Não foi possível preparar o arquivo ZIP. Tente novamente."
            ) from exc
        return target

    @staticmethod
    def cleanup(path: str | Path) -> None:
        """Apaga um ZIP temporário (silencioso: é só faxina)."""
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - no Windows o arquivo pode estar em uso
            logger.warning("Não foi possível remover o arquivo temporário %s", path)

    def purge_stale(self) -> int:
        """Remove ZIPs de download que ficaram para trás.

        Em alguns sistemas (Windows) o arquivo continua travado enquanto está
        sendo enviado e o `unlink` do fim da requisição falha — sem esta faxina
        esses ZIPs se acumulariam em ``storage/tmp`` até o container reiniciar.
        """
        limit = time.time() - self.stale_seconds
        removed = 0
        for leftover in self.temp_root.glob("download-*.zip"):
            try:
                if leftover.stat().st_mtime < limit:
                    leftover.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - defensivo
                continue
        if removed:
            logger.info("ZIPs de download antigos removidos: %s", removed)
        return removed


def _unique_name(name: str, used: set[str]) -> str:
    """Garante nomes distintos dentro do ZIP (``foto.jpg``, ``foto-1.jpg``…)."""
    candidate = PurePosixPath(name).name or "foto"
    stem = PurePosixPath(candidate).stem
    suffix = PurePosixPath(candidate).suffix
    index = 1
    while candidate.lower() in used:
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    used.add(candidate.lower())
    return candidate
