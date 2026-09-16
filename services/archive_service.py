"""Extração segura de ZIPs com várias fotos.

Regras de segurança (o arquivo vem de fora, então nada é confiado):

* só entram arquivos com extensão permitida (imagens e RAW);
* o nome usado é **apenas o basename** — `../../etc/x.jpg` vira `x.jpg`
  (path traversal neutralizado);
* ZIP dentro de ZIP é ignorado (evita bomba de recursão);
* limite de quantidade de arquivos, tamanho por arquivo, tamanho total
  descompactado e razão de compressão (proteção contra *zip bomb*);
* a leitura é feita em blocos, abortando na hora se o limite for estourado;
* pastas de metadados (`__MACOSX`, `.DS_Store`, `Thumbs.db`) são descartadas.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4

from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024  # 1 MB

_IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_IGNORED_PREFIXES = ("__macosx", ".")


@dataclass(slots=True)
class ExtractedEntry:
    filename: str      # nome exibido para o usuário (basename)
    path: Path         # arquivo temporário no disco
    size: int
    origin: str        # nome do ZIP de origem


@dataclass(slots=True)
class SkippedEntry:
    filename: str
    reason: str


@dataclass(slots=True)
class ExtractionResult:
    folder: Path | None = None
    entries: list[ExtractedEntry] = field(default_factory=list)
    skipped: list[SkippedEntry] = field(default_factory=list)
    error: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.entries)

    def skipped_as_dicts(self) -> list[dict]:
        return [{"filename": item.filename, "reason": item.reason} for item in self.skipped]


class ArchiveService:
    def __init__(
        self,
        temp_root: str | Path,
        allowed_extensions: set[str],
        archive_extensions: set[str] | None = None,
        max_entries: int = 500,
        max_entry_size: int = 60 * 1024 * 1024,
        max_total_size: int = 1024 * 1024 * 1024,
        max_compression_ratio: int = 200,
    ) -> None:
        self.temp_root = Path(temp_root)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.allowed_extensions = {e.lower().lstrip(".") for e in allowed_extensions}
        self.archive_extensions = {
            e.lower().lstrip(".") for e in (archive_extensions or {"zip"})
        }
        self.max_entries = int(max_entries)
        self.max_entry_size = int(max_entry_size)
        self.max_total_size = int(max_total_size)
        self.max_compression_ratio = int(max_compression_ratio)

    # ------------------------------------------------------------------
    # Detecção
    # ------------------------------------------------------------------
    def is_archive_extension(self, filename: str | None) -> bool:
        if not filename:
            return False
        return Path(filename).suffix.lower().lstrip(".") in self.archive_extensions

    def is_archive(self, file: FileStorage) -> bool:
        """True para ZIP pela extensão **ou** pela assinatura do arquivo."""
        if self.is_archive_extension(file.filename):
            return True
        try:
            stream = file.stream
            position = stream.tell()
            stream.seek(0)
            head = stream.read(4)
            stream.seek(position)
        except Exception:  # pragma: no cover - defensivo
            return False
        return head[:2] == b"PK"

    # ------------------------------------------------------------------
    # Extração
    # ------------------------------------------------------------------
    def extract(self, file: FileStorage, label: str | None = None) -> ExtractionResult:
        label = label or file.filename or "arquivo.zip"
        result = ExtractionResult()
        folder = self.temp_root / f"zip-{uuid4().hex}"
        folder.mkdir(parents=True, exist_ok=True)
        result.folder = folder

        try:
            stream = file.stream
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                total_size = 0
                for info in archive.infolist():
                    if len(result.entries) >= self.max_entries:
                        result.skipped.append(
                            SkippedEntry(
                                filename=f"({len(archive.infolist()) - len(result.entries)} arquivos restantes)",
                                reason=f"limite de {self.max_entries} fotos por ZIP atingido",
                            )
                        )
                        break

                    if info.is_dir():
                        continue

                    name = self._safe_name(info.filename)
                    if name is None:
                        continue

                    extension = Path(name).suffix.lower().lstrip(".")

                    if extension in self.archive_extensions:
                        result.skipped.append(
                            SkippedEntry(filename=name, reason="ZIP dentro de ZIP não é suportado")
                        )
                        continue

                    if extension not in self.allowed_extensions:
                        result.skipped.append(
                            SkippedEntry(filename=name, reason=f"formato .{extension or '?'} não suportado")
                        )
                        continue

                    if info.flag_bits & 0x1:  # criptografado
                        result.skipped.append(
                            SkippedEntry(filename=name, reason="arquivo protegido por senha")
                        )
                        continue

                    if info.file_size > self.max_entry_size:
                        result.skipped.append(
                            SkippedEntry(
                                filename=name,
                                reason=f"maior que o limite de {self.max_entry_size // (1024 * 1024)} MB",
                            )
                        )
                        continue

                    if total_size + info.file_size > self.max_total_size:
                        result.skipped.append(
                            SkippedEntry(
                                filename=name,
                                reason=f"limite total de {self.max_total_size // (1024 * 1024)} MB descompactados atingido",
                            )
                        )
                        continue

                    if info.compress_size > 0 and info.file_size > 0:
                        ratio = info.file_size / float(info.compress_size)
                        if ratio > self.max_compression_ratio:
                            result.skipped.append(
                                SkippedEntry(
                                    filename=name,
                                    reason=f"compressão suspeita (razão {ratio:.0f}:1)",
                                )
                            )
                            continue

                    destination = folder / f"{len(result.entries):04d}-{name}"
                    try:
                        written = self._extract_entry(archive, info, destination)
                    except _EntryTooLarge:
                        result.skipped.append(
                            SkippedEntry(filename=name, reason="arquivo maior que o esperado")
                        )
                        continue
                    except Exception as exc:
                        logger.warning("Falha ao extrair %s de %s: %s", name, label, exc)
                        result.skipped.append(
                            SkippedEntry(filename=name, reason="não foi possível ler o arquivo")
                        )
                        continue

                    total_size += written
                    result.entries.append(
                        ExtractedEntry(
                            filename=name,
                            path=destination,
                            size=written,
                            origin=label,
                        )
                    )
        except zipfile.BadZipFile:
            result.error = "O arquivo ZIP está corrompido ou não é um ZIP válido."
        except RuntimeError as exc:  # zip criptografado / método não suportado
            result.error = f"Não foi possível abrir o ZIP ({exc})."
        except Exception:  # pragma: no cover - defensivo
            logger.exception("Erro inesperado ao processar o ZIP %s", label)
            result.error = "Não foi possível processar o arquivo ZIP enviado."

        if result.error and result.folder:
            self.cleanup(result.folder)
            result.folder = None

        logger.info(
            "ZIP %s: %s foto(s) extraída(s), %s ignorada(s)%s",
            label,
            len(result.entries),
            len(result.skipped),
            f" (erro: {result.error})" if result.error else "",
        )
        return result

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_name(raw_name: str) -> str | None:
        """Reduz o caminho interno do ZIP a um basename confiável."""
        if not raw_name:
            return None
        normalized = raw_name.replace("\\", "/")
        name = PurePosixPath(normalized).name.strip()
        if not name:
            return None
        lowered = name.lower()
        if lowered in _IGNORED_NAMES or lowered.startswith(_IGNORED_PREFIXES):
            return None
        if "/" in name or "\\" in name or name in {".", ".."}:
            return None
        return name

    def _extract_entry(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        destination: Path,
    ) -> int:
        """Escreve a entrada em disco respeitando o limite de tamanho real."""
        written = 0
        with archive.open(info) as source, open(destination, "wb") as target:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > self.max_entry_size:
                    target.close()
                    destination.unlink(missing_ok=True)
                    raise _EntryTooLarge(info.filename)
                target.write(chunk)
        return written

    def cleanup(self, folder: Path | None) -> None:
        if not folder:
            return
        shutil.rmtree(folder, ignore_errors=True)


class _EntryTooLarge(Exception):
    pass
