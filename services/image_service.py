"""Manipulação de imagens: validação, gravação, thumbnails e leitura para CV.

Regras importantes:
* o nome físico do arquivo é sempre um UUID (nunca confiamos no nome enviado);
* a validação é feita pelo **conteúdo** (Pillow), nunca pela extensão: um arquivo
  `.png` que na verdade é HEIC, TIFF ou lixo é identificado e o usuário recebe uma
  mensagem dizendo o que o arquivo realmente é;
* a orientação EXIF é aplicada antes de gerar thumbnail / detectar rostos;
* arquivos RAW (ex.: `.nef`) são convertidos para JPEG — o original é preservado
  em disco e o caminho fica em ``SavedImage.raw_path``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .raw_service import RawConversionError, RawImageService

logger = logging.getLogger(__name__)

# Suporte a HEIC/HEIF (fotos de iPhone e de vários Android). Se o plugin não
# estiver instalado, o app continua funcionando e explica ao usuário como enviar.
try:  # pragma: no cover - depende do ambiente
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORT = True
    logger.info("Suporte a HEIC/HEIF habilitado (pillow-heif)")
except Exception:  # pragma: no cover - plugin ausente
    HEIF_SUPPORT = False


# Formatos de imagem que o Pillow aceita e conseguimos processar.
_PILLOW_FORMATS = {
    "JPEG",
    "PNG",
    "WEBP",
    "BMP",
    "TIFF",
    "GIF",
    "MPO",
    "JPEG2000",
    "PPM",
    "SPIDER",
}
if HEIF_SUPPORT:
    _PILLOW_FORMATS |= {"HEIF", "AVIF"}

# Assinaturas de arquivo (magic bytes) usadas para dizer o que o arquivo É.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"BM", "BMP"),
    (b"II*\x00", "RAW ou TIFF"),
    (b"MM\x00*", "RAW ou TIFF"),
    (b"PK\x03\x04", "ZIP"),
    (b"PK\x05\x06", "ZIP vazio"),
    (b"%PDF", "PDF"),
    (b"\x00\x00\x00\x0cjP  \r\n\x87\n", "JPEG 2000"),
    (b"8BPS", "Photoshop (PSD)"),
)
_HEIF_BRANDS = {"heic", "heix", "hevc", "hevx", "heim", "heis", "hevm", "hevs", "mif1", "msf1"}
_AVIF_BRANDS = {"avif", "avis"}

# Explicação amigável por formato detectado.
_FORMAT_HINTS = {
    "HEIC/HEIF": (
        "Este arquivo está em HEIC/HEIF (o formato padrão das fotos de iPhone). "
        "Ele não pôde ser convertido aqui. Envie a foto em JPG ou PNG "
        "(ou instale o suporte com: pip install pillow-heif)."
    ),
    "AVIF": "Este arquivo está em AVIF. Envie a foto em JPG ou PNG.",
    "RAW ou TIFF": (
        "Este arquivo é um RAW de câmera (ou TIFF) e não pode ser usado como selfie. "
        "Envie uma foto JPG, PNG ou WEBP."
    ),
    "ZIP": "Este arquivo é um ZIP, não uma imagem. Envie uma foto JPG, PNG ou WEBP.",
    "ZIP vazio": "Este arquivo é um ZIP vazio, não uma imagem.",
    "PDF": "Este arquivo é um PDF, não uma imagem. Envie uma foto JPG, PNG ou WEBP.",
    "MP4/vídeo": "Este arquivo parece ser um vídeo. Envie uma foto JPG, PNG ou WEBP.",
    "RIFF (áudio/vídeo)": "Este arquivo parece ser um áudio/vídeo. Envie uma foto JPG, PNG ou WEBP.",
    "JPEG 2000": "O formato JPEG 2000 não é suportado. Envie uma foto JPG, PNG ou WEBP.",
    "Photoshop (PSD)": "Este arquivo é um PSD do Photoshop. Envie uma foto JPG, PNG ou WEBP.",
}
_DEFAULT_HINT = (
    "Não foi possível reconhecer o formato deste arquivo — ele pode estar corrompido "
    "ou incompleto. Envie uma foto JPG, PNG ou WEBP."
)


def sniff_image_format(path: str | Path) -> str | None:
    """Descobre o formato real do arquivo pelos primeiros bytes (ignora o nome)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(64)
    except OSError:
        return None
    if not head:
        return None

    for signature, name in _SIGNATURES:
        if head.startswith(signature):
            return name

    if head[4:8] == b"ftyp":
        brand = head[8:12].decode("latin-1", "ignore").strip().lower()
        if brand in _HEIF_BRANDS:
            return "HEIC/HEIF"
        if brand in _AVIF_BRANDS:
            return "AVIF"
        return "MP4/vídeo"

    if head.startswith(b"RIFF"):
        return "WEBP" if head[8:12] == b"WEBP" else "RIFF (áudio/vídeo)"
    return None


def explain_format(detected: str | None) -> str:
    """Mensagem amigável para um arquivo que o Pillow não conseguiu abrir."""
    if detected and detected in _FORMAT_HINTS:
        return _FORMAT_HINTS[detected]
    if detected and detected in {"JPEG", "PNG", "WEBP", "GIF", "BMP"}:
        return (
            f"O arquivo parece ser um {detected} válido, mas está corrompido ou incompleto. "
            "Tente enviá-lo novamente."
        )
    return _DEFAULT_HINT


class ImageValidationError(Exception):
    """Erro amigável de validação de imagem."""


@dataclass
class SavedImage:
    filename: str          # nome original (sanitizado) - apenas para exibição
    stored_name: str       # nome físico (uuid)
    original_path: str     # caminho relativo ao storage (imagem legível)
    thumbnail_path: str    # caminho relativo ao storage
    width: int
    height: int
    size_bytes: int
    raw_path: str | None = None       # caminho relativo do RAW original, se houver
    raw_method: str | None = None     # "rawpy" ou "preview"


class ImageService:
    def __init__(
        self,
        storage_root: Path,
        thumbnail_width: int = 500,
        thumbnail_quality: int = 85,
        allowed_extensions: set[str] | None = None,
        allowed_mime_types: set[str] | None = None,
        raw_extensions: set[str] | None = None,
        raw_service: RawImageService | None = None,
    ) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.thumbnail_width = int(thumbnail_width)
        self.thumbnail_quality = int(thumbnail_quality)
        self.allowed_extensions = {e.lower().lstrip(".") for e in (allowed_extensions or set())}
        self.allowed_mime_types = {m.lower() for m in (allowed_mime_types or set())}
        self.raw_service = raw_service
        self.raw_extensions = {
            e.lower().lstrip(".")
            for e in (raw_extensions or (raw_service.extensions if raw_service else set()))
        }

    # ------------------------------------------------------------------
    # Caminhos
    # ------------------------------------------------------------------
    def event_dirs(self, event_id: int) -> tuple[Path, Path]:
        base = self.storage_root / "events" / str(event_id)
        originals = base / "originals"
        thumbnails = base / "thumbnails"
        originals.mkdir(parents=True, exist_ok=True)
        thumbnails.mkdir(parents=True, exist_ok=True)
        return originals, thumbnails

    def relative(self, path: str | Path) -> str:
        """Caminho relativo ao storage, sempre com barras '/' (para URLs)."""
        return Path(path).resolve().relative_to(self.storage_root).as_posix()

    def absolute(self, relative_path: str) -> Path:
        return (self.storage_root / relative_path).resolve()

    @staticmethod
    def storage_url(relative_path: str | None) -> str | None:
        if not relative_path:
            return None
        return f"/storage/{relative_path.lstrip('/')}"

    def is_raw_extension(self, extension: str) -> bool:
        return (extension or "").lower().lstrip(".") in self.raw_extensions

    @property
    def heif_supported(self) -> bool:
        return HEIF_SUPPORT

    # ------------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------------
    @staticmethod
    def extension_of(filename: str | None) -> str:
        if not filename:
            return ""
        return Path(secure_filename(filename) or filename).suffix.lower().lstrip(".")

    def check_upload(self, file: FileStorage) -> str:
        """Valida extensão e MIME. Devolve a extensão normalizada."""
        if file is None or not file.filename:
            raise ImageValidationError("Nenhum arquivo foi recebido.")

        extension = self.extension_of(file.filename)
        if extension in {"jpe", "jfif"}:
            extension = "jpeg"

        if extension in self.raw_extensions:
            # O RAW é validado convertendo-o (rawpy/preview), não pelo MIME.
            return extension

        if extension not in self.allowed_extensions:
            raise ImageValidationError(
                "Formato não suportado. Envie JPG, JPEG, PNG, WEBP, HEIC (iPhone), "
                "NEF/RAW ou ZIP."
            )

        mimetype = (file.mimetype or "").lower()
        if self.allowed_mime_types and mimetype and mimetype != "application/octet-stream":
            if mimetype not in self.allowed_mime_types:
                raise ImageValidationError(
                    "O tipo do arquivo enviado não parece ser uma imagem válida."
                )
        return extension

    # ------------------------------------------------------------------
    # Gravação
    # ------------------------------------------------------------------
    def save_upload(self, file: FileStorage, event_id: int) -> SavedImage:
        """Grava o original (+ RAW quando for o caso) e gera o thumbnail.

        Levanta ``ImageValidationError`` quando o arquivo não é uma imagem válida
        (nesse caso nada permanece no disco).
        """
        extension = self.check_upload(file)
        display_name = secure_filename(file.filename) or f"foto.{extension}"

        originals_dir, thumbnails_dir = self.event_dirs(event_id)
        stem = uuid4().hex
        created: list[Path] = []
        raw_relative: str | None = None
        raw_method: str | None = None

        if self.raw_service and self.is_raw_extension(extension):
            # O RAW não é uma imagem legível: guardamos o original e geramos um JPEG.
            raw_path = originals_dir / f"{stem}.{extension}"
            file.save(str(raw_path))
            created.append(raw_path)

            original_path = originals_dir / f"{stem}.jpg"
            created.append(original_path)
            try:
                raw_method = self.raw_service.convert_to_jpeg(raw_path, original_path)
            except RawConversionError as exc:
                self.delete_files(*created)
                raise ImageValidationError(str(exc)) from exc
            except Exception as exc:  # pragma: no cover - defensivo
                self.delete_files(*created)
                logger.exception("Falha ao converter o RAW %s", raw_path)
                raise ImageValidationError(
                    "Não foi possível processar este arquivo RAW."
                ) from exc
            raw_relative = self.relative(raw_path)
        else:
            original_path = originals_dir / f"{stem}.{extension}"
            file.save(str(original_path))
            created.append(original_path)

        thumbnail_path = thumbnails_dir / f"{stem}.jpg"
        try:
            width, height = self._validate_and_measure(original_path)
            self.make_thumbnail(original_path, thumbnail_path)
        except ImageValidationError:
            self.delete_files(*created, thumbnail_path)
            raise
        except Exception as exc:  # pragma: no cover - defensivo
            self.delete_files(*created, thumbnail_path)
            logger.exception("Falha ao processar a imagem %s", original_path)
            raise ImageValidationError("Não foi possível processar esta imagem.") from exc

        return SavedImage(
            filename=display_name,
            stored_name=original_path.name,
            original_path=self.relative(original_path),
            thumbnail_path=self.relative(thumbnail_path),
            width=width,
            height=height,
            size_bytes=original_path.stat().st_size,
            raw_path=raw_relative,
            raw_method=raw_method,
        )

    @staticmethod
    def _validate_and_measure(path: Path) -> tuple[int, int]:
        """Valida o arquivo pelo conteúdo e devolve as dimensões."""
        ImageService.inspect_image(path)
        with Image.open(path) as img:
            width, height = img.size
        return width, height

    @staticmethod
    def inspect_image(path: str | Path) -> str:
        """Confirma que o arquivo é uma imagem realmente legível.

        Devolve o formato detectado pelo Pillow (ex.: ``"JPEG"``, ``"HEIF"``) ou
        levanta ``ImageValidationError`` com uma mensagem que explica **o que o
        arquivo é de verdade** — a extensão enviada não é confiável.
        """
        path = Path(path)
        detected = sniff_image_format(path)
        try:
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                fmt = (img.format or "").upper()
                width, height = img.size
        except UnidentifiedImageError as exc:
            logger.warning(
                "Arquivo não é imagem (detectado: %s) - %s", detected or "desconhecido", path.name
            )
            raise ImageValidationError(explain_format(detected)) from exc
        except OSError as exc:
            logger.warning("Falha ao ler a imagem %s: %s", path.name, exc)
            raise ImageValidationError(
                "Não foi possível ler esta imagem (arquivo incompleto ou corrompido). "
                "Tente enviá-la novamente."
            ) from exc

        if fmt not in _PILLOW_FORMATS:
            raise ImageValidationError(explain_format(detected or fmt))
        if width < 1 or height < 1:
            raise ImageValidationError("A imagem está corrompida (dimensões inválidas).")
        return fmt

    def make_thumbnail(self, source: Path, destination: Path) -> Path:
        """Thumbnail com largura máxima configurável, preservando proporção."""
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            width, height = img.size
            if width > self.thumbnail_width:
                ratio = self.thumbnail_width / float(width)
                new_size = (self.thumbnail_width, max(1, round(height * ratio)))
                img = img.resize(new_size, Image.LANCZOS)
            destination.parent.mkdir(parents=True, exist_ok=True)
            img.save(
                destination,
                format="JPEG",
                quality=self.thumbnail_quality,
                optimize=True,
                progressive=True,
            )
        return destination

    # ------------------------------------------------------------------
    # Leitura para o OpenCV / InsightFace
    # ------------------------------------------------------------------
    @staticmethod
    def load_bgr(path: str | Path) -> np.ndarray:
        """Carrega a imagem como BGR (numpy) aplicando a rotação EXIF.

        Usa o Pillow (e não ``cv2.imread``) porque ``cv2.imread`` falha com
        acentos no caminho no Windows.
        """
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            rgb = np.asarray(img, dtype=np.uint8)
        return np.ascontiguousarray(rgb[:, :, ::-1])

    # ------------------------------------------------------------------
    # Limpeza
    # ------------------------------------------------------------------
    def delete_files(self, *paths: str | Path | None) -> None:
        for path in paths:
            if not path:
                continue
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensivo
                logger.warning("Não foi possível remover o arquivo %s", path)

    def delete_photo_files(
        self,
        original_path: str | None,
        thumbnail_path: str | None,
        raw_path: str | None = None,
    ) -> None:
        self.delete_files(
            self.absolute(original_path) if original_path else None,
            self.absolute(thumbnail_path) if thumbnail_path else None,
            self.absolute(raw_path) if raw_path else None,
        )

    def delete_event_folder(self, event_id: int) -> None:
        import shutil

        folder = self.storage_root / "events" / str(event_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
