"""Leitura de arquivos RAW de câmera (NEF da Nikon e equivalentes).

Um `.NEF` **não é uma imagem**: é um container TIFF com os dados brutos do
sensor. Nem o OpenCV nem o Pillow conseguem abri-lo. Existem duas estratégias:

1. **rawpy / LibRaw** (preferida): decodifica o RAW de verdade, aplica o
   balanço de branco da câmera e devolve a imagem completa.
2. **JPEG embutido** (*fallback*): toda câmera grava dentro do RAW um preview
   JPEG em tamanho grande. Se o rawpy não estiver instalado (ou falhar), o
   preview é extraído diretamente dos bytes do arquivo — funciona sem
   nenhuma dependência extra.

O resultado dos dois caminhos é um JPEG normal que segue o fluxo padrão do
sistema (thumbnail, detecção de rostos, embeddings).
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Extensões RAW conhecidas (o `config.py` controla quais são aceitas no upload).
RAW_EXTENSIONS = {
    "nef", "nrw",      # Nikon
    "cr2", "cr3",      # Canon
    "arw", "srf", "sr2",  # Sony
    "dng",             # Adobe / Apple / Pentax
    "orf",             # Olympus
    "raf",             # Fujifilm
    "rw2", "raw",      # Panasonic / Leica
    "pef",             # Pentax
    "srw",             # Samsung
    "3fr",             # Hasselblad
    "erf",             # Epson
    "mrw",             # Minolta
    "x3f",             # Sigma
    "kdc", "dcr", "mef",  # Kodak
    "iiq",             # Phase One
    "rwl",             # Leica
    "mos", "mfw", "cs1",
}

# Assinaturas de JPEG dentro do arquivo (SOI + EOI).
_JPEG_SOI = b"\xff\xd8\xff"
_JPEG_EOI = b"\xff\xd9"

# Tamanho mínimo aceitável para um preview (evita pegar mini-thumbnails de 160px).
_MIN_PREVIEW_SIDE = 320


class RawConversionError(Exception):
    """Erro amigável ao ler/converter um arquivo RAW."""


class RawImageService:
    # Parâmetros usados no ``rawpy.postprocess`` (validados em testes).
    POSTPROCESS_KWARGS = {
        "use_camera_wb": True,
        "no_auto_bright": False,
        "output_bps": 8,
        "gamma": (2.222, 4.5),
    }

    def __init__(
        self,
        extensions: set[str] | None = None,
        jpeg_quality: int = 92,
        max_side: int = 4000,
        allow_preview_fallback: bool = True,
    ) -> None:
        self.extensions = {e.lower().lstrip(".") for e in (extensions or RAW_EXTENSIONS)}
        self.jpeg_quality = int(jpeg_quality)
        self.max_side = int(max_side or 0)
        self.allow_preview_fallback = bool(allow_preview_fallback)
        self._rawpy = None
        self._rawpy_checked = False

    # ------------------------------------------------------------------
    # Capacidades
    # ------------------------------------------------------------------
    def is_raw_extension(self, extension: str) -> bool:
        return (extension or "").lower().lstrip(".") in self.extensions

    def is_raw(self, filename: str | Path) -> bool:
        return self.is_raw_extension(Path(str(filename)).suffix)

    @property
    def rawpy(self):
        """Importa o rawpy na primeira utilização (é uma dependência pesada)."""
        if not self._rawpy_checked:
            self._rawpy_checked = True
            try:
                import rawpy  # type: ignore

                self._rawpy = rawpy
                logger.info("rawpy disponível (LibRaw %s)", getattr(rawpy, "libraw_version", "?"))
            except Exception as exc:  # pragma: no cover - ambiente sem rawpy
                self._rawpy = None
                logger.warning("rawpy indisponível (%s); usando preview JPEG embutido", exc)
        return self._rawpy

    @property
    def available(self) -> bool:
        return self.rawpy is not None

    def describe(self) -> dict:
        return {
            "rawpy": self.available,
            "libraw_version": str(getattr(self.rawpy, "libraw_version", "")) if self.available else None,
            "preview_fallback": self.allow_preview_fallback,
            "extensions": sorted(self.extensions),
        }

    # ------------------------------------------------------------------
    # Conversão
    # ------------------------------------------------------------------
    def convert_to_jpeg(self, source: str | Path, destination: str | Path) -> str:
        """Gera um JPEG legível a partir do RAW.

        Devolve o método usado: ``"rawpy"`` ou ``"preview"``.
        """
        source = Path(source)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        problems: list[str] = []

        if self.available:
            try:
                rgb = self._decode_with_rawpy(source)
                self._save_jpeg(rgb, destination)
                logger.info("RAW convertido com rawpy: %s", source.name)
                return "rawpy"
            except Exception as exc:
                problems.append(str(exc))
                logger.warning("rawpy falhou em %s: %s", source.name, exc)

        if self.allow_preview_fallback:
            try:
                if self._save_embedded_preview(source, destination):
                    logger.info("RAW convertido pelo preview JPEG embutido: %s", source.name)
                    return "preview"
                problems.append("nenhum preview JPEG encontrado no arquivo")
            except Exception as exc:
                problems.append(str(exc))
                logger.warning("Falha ao extrair preview de %s: %s", source.name, exc)

        detail = problems[-1] if problems else "formato não suportado"
        raise RawConversionError(
            "Não foi possível ler este arquivo RAW. "
            f"Verifique se o arquivo está íntegro e se o formato é suportado ({detail})."
        )

    # ------------------------------------------------------------------
    # Caminho 1: rawpy
    # ------------------------------------------------------------------
    def _decode_with_rawpy(self, source: Path) -> np.ndarray:
        rawpy = self.rawpy
        if rawpy is None:
            raise RawConversionError("rawpy indisponível")
        with rawpy.imread(str(source)) as raw:
            rgb = raw.postprocess(**self.POSTPROCESS_KWARGS)
        if rgb is None or rgb.size == 0:
            raise RawConversionError("imagem vazia após a decodificação")
        return rgb

    # ------------------------------------------------------------------
    # Caminho 2: JPEG embutido no RAW
    # ------------------------------------------------------------------
    @staticmethod
    def find_embedded_jpegs(data: bytes) -> list[tuple[int, int]]:
        """Localiza os blocos JPEG (offset inicial, offset final) do arquivo."""
        blocks: list[tuple[int, int]] = []
        position = 0
        while True:
            start = data.find(_JPEG_SOI, position)
            if start < 0:
                break
            end = data.find(_JPEG_EOI, start + len(_JPEG_SOI))
            if end < 0:
                break
            end += len(_JPEG_EOI)
            blocks.append((start, end))
            position = end
        return blocks

    def embedded_preview(self, source: str | Path) -> tuple[Image.Image, int] | None:
        """Devolve o maior JPEG embutido que o Pillow consegue abrir."""
        data = Path(source).read_bytes()
        blocks = self.find_embedded_jpegs(data)
        if not blocks:
            return None

        # Do maior para o menor; o primeiro que decodificar de verdade é o preview.
        for start, end in sorted(blocks, key=lambda item: item[1] - item[0], reverse=True):
            blob = data[start:end]
            if len(blob) < 4096:
                continue
            try:
                image = Image.open(BytesIO(blob))
                image.load()
            except Exception:
                continue
            width, height = image.size
            if max(width, height) < _MIN_PREVIEW_SIDE:
                continue
            return image, len(blob)

        # Nenhum preview grande: tenta qualquer JPEG decodificável.
        for start, end in sorted(blocks, key=lambda item: item[1] - item[0], reverse=True):
            try:
                image = Image.open(BytesIO(data[start:end]))
                image.load()
                return image, end - start
            except Exception:
                continue
        return None

    def _save_embedded_preview(self, source: Path, destination: Path) -> bool:
        result = self.embedded_preview(source)
        if result is None:
            return False
        image, _size = result
        self._save_jpeg(np.asarray(self._orient_and_flatten(image)), destination)
        return True

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @staticmethod
    def _orient_and_flatten(image: Image.Image) -> Image.Image:
        """Aplica a rotação EXIF e garante modo RGB."""
        image = ImageOps.exif_transpose(image)
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image

    def _save_jpeg(self, rgb: np.ndarray, destination: Path) -> None:
        image = Image.fromarray(np.asarray(rgb, dtype=np.uint8))
        image = self._orient_and_flatten(image)
        if self.max_side:
            width, height = image.size
            largest = max(width, height)
            if largest > self.max_side:
                scale = self.max_side / float(largest)
                image = image.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    Image.LANCZOS,
                )
        image.save(
            destination,
            format="JPEG",
            quality=self.jpeg_quality,
            optimize=True,
            progressive=True,
        )


_default_service: Optional[RawImageService] = None


def get_raw_service() -> RawImageService:
    """Instância configurada a partir do ``config.py``."""
    global _default_service
    if _default_service is None:
        from config import Config

        _default_service = RawImageService(
            extensions=Config.RAW_EXTENSIONS,
            jpeg_quality=Config.RAW_JPEG_QUALITY,
            max_side=Config.RAW_MAX_SIDE,
            allow_preview_fallback=Config.RAW_PREVIEW_FALLBACK,
        )
    return _default_service
