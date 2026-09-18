"""Configuração central da aplicação Photo Face MVP.

Todos os valores que podem variar (caminhos, limites, thresholds) ficam
concentrados aqui para que o restante do código nunca "chumbe" nada.
Os valores podem ser sobrescritos por variáveis de ambiente, o que facilita
a passagem posterior para produção (Docker, Railway, etc.).
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_csv(name: str, default: str) -> set[str]:
    raw = _env_str(name, default)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


class Config:
    """Configuração do Flask + do motor de reconhecimento facial."""

    # ------------------------------------------------------------------
    # Flask
    # ------------------------------------------------------------------
    SECRET_KEY = _env_str("SECRET_KEY", "photo-face-mvp-dev-secret-change-me")
    # Senha única do painel (o app não tem cadastro de usuários: é um
    # administrador só). Serve para criar eventos e enviar fotos — a página
    # pública do evento continua aberta para o visitante.
    # Em produção, troque pelas variáveis de ambiente: SECRET_KEY e ADMIN_PASSWORD.
    ADMIN_PASSWORD = _env_str("ADMIN_PASSWORD", "264079")
    DEBUG = _env_bool("DEBUG", True)
    HOST = _env_str("HOST", "0.0.0.0")
    PORT = _env_int("PORT", 5000)
    # Limite de payload por requisição. Precisa ser generoso porque um único
    # envio pode ser um ZIP com dezenas de fotos.
    MAX_CONTENT_LENGTH = _env_int("MAX_CONTENT_LENGTH", 256 * 1024 * 1024)  # 256 MB
    TEMPLATES_AUTO_RELOAD = DEBUG

    # ------------------------------------------------------------------
    # Armazenamento / banco
    # ------------------------------------------------------------------
    STORAGE_FOLDER = Path(_env_str("STORAGE_FOLDER", str(BASE_DIR / "storage"))).resolve()
    UPLOAD_FOLDER = STORAGE_FOLDER / "events"
    EVENTS_FOLDER = STORAGE_FOLDER / "events"
    TEMP_FOLDER = STORAGE_FOLDER / "tmp"
    DATABASE_PATH = Path(_env_str("DATABASE_PATH", str(BASE_DIR / "database.db"))).resolve()

    # ------------------------------------------------------------------
    # Upload de imagens
    # ------------------------------------------------------------------
    # Imagens comuns (a validação real é feita pelo conteúdo, não pela extensão).
    ALLOWED_EXTENSIONS = _env_csv("ALLOWED_EXTENSIONS", "jpg,jpeg,png,webp,heic,heif,avif")
    # RAW de câmeras (NEF da Nikon e equivalentes). São convertidos para JPEG
    # na hora do upload; o arquivo original é preservado em disco.
    RAW_EXTENSIONS = _env_csv(
        "RAW_EXTENSIONS",
        "nef,nrw,cr2,cr3,arw,dng,orf,raf,rw2,pef,srw,sr2,3fr,erf,mrw,x3f,kdc,dcr,mef,iiq,rwl",
    )
    # Arquivos compactados com várias fotos dentro.
    ARCHIVE_EXTENSIONS = _env_csv("ARCHIVE_EXTENSIONS", "zip")
    ALLOWED_MIME_TYPES = _env_csv(
        "ALLOWED_MIME_TYPES",
        "image/jpeg,image/jpg,image/pjpeg,image/png,image/webp,image/x-png,"
        "image/heic,image/heif,image/heic-sequence,image/avif,"
        "image/x-nikon-nef,image/x-canon-cr2,image/x-adobe-dng,image/tiff,"
        "application/zip,application/x-zip-compressed,multipart/x-zip",
    )
    # Extensões efetivamente aceitas na área de upload.
    UPLOAD_EXTENSIONS = set(ALLOWED_EXTENSIONS) | set(RAW_EXTENSIONS) | set(ARCHIVE_EXTENSIONS)
    THUMBNAIL_WIDTH = _env_int("THUMBNAIL_WIDTH", 500)
    THUMBNAIL_QUALITY = _env_int("THUMBNAIL_QUALITY", 85)
    # Redimensiona imagens muito grandes antes da detecção (ganho de velocidade).
    MAX_DETECTION_SIDE = _env_int("MAX_DETECTION_SIDE", 2000)

    # ------------------------------------------------------------------
    # RAW (NEF e afins)
    # ------------------------------------------------------------------
    RAW_JPEG_QUALITY = _env_int("RAW_JPEG_QUALITY", 92)
    # Largura máxima do JPEG gerado a partir do RAW (0 = resolução original).
    RAW_MAX_SIDE = _env_int("RAW_MAX_SIDE", 4000)
    # Se o rawpy não estiver disponível (ou falhar), usa o JPEG embutido no
    # próprio arquivo RAW (o preview que toda câmera grava junto).
    RAW_PREVIEW_FALLBACK = _env_bool("RAW_PREVIEW_FALLBACK", True)

    # ------------------------------------------------------------------
    # ZIP com várias fotos
    # ------------------------------------------------------------------
    ALLOW_ARCHIVE_UPLOAD = _env_bool("ALLOW_ARCHIVE_UPLOAD", True)
    MAX_ZIP_ENTRIES = _env_int("MAX_ZIP_ENTRIES", 500)
    MAX_ZIP_ENTRY_SIZE = _env_int("MAX_ZIP_ENTRY_SIZE", 60 * 1024 * 1024)  # 60 MB por foto
    MAX_ZIP_TOTAL_SIZE = _env_int("MAX_ZIP_TOTAL_SIZE", 1024 * 1024 * 1024)  # 1 GB descompactado
    # Proteção contra "zip bomb": razão máxima entre descompactado e compactado.
    MAX_ZIP_COMPRESSION_RATIO = _env_int("MAX_ZIP_COMPRESSION_RATIO", 200)

    # ------------------------------------------------------------------
    # Download dos originais (o que o visitante leva do site)
    # ------------------------------------------------------------------
    # A busca e o download são públicos (sem login): os limites abaixo evitam
    # que um único pedido encha o disco do container montando um ZIP gigante.
    # Quantidade máxima de fotos em um download.
    MAX_DOWNLOAD_PHOTOS = _env_int("MAX_DOWNLOAD_PHOTOS", 200)
    # Tamanho total máximo (em bytes) dos arquivos de um download.
    MAX_DOWNLOAD_BYTES = _env_int("MAX_DOWNLOAD_BYTES", 2 * 1024 * 1024 * 1024)  # 2 GB

    # ------------------------------------------------------------------
    # Reconhecimento facial
    # ------------------------------------------------------------------
    INSIGHTFACE_MODEL = _env_str("INSIGHTFACE_MODEL", "buffalo_l")
    INSIGHTFACE_DET_SIZE = _env_int("INSIGHTFACE_DET_SIZE", 640)
    # Carrega o modelo no start da aplicação (e não na primeira requisição).
    LOAD_MODEL_ON_STARTUP = _env_bool("LOAD_MODEL_ON_STARTUP", True)
    # Providers do onnxruntime na ordem de preferência. Se CUDA não existir
    # na máquina, o serviço cai automaticamente para CPU.
    INSIGHTFACE_PROVIDERS = _env_str("INSIGHTFACE_PROVIDERS", "CUDAExecutionProvider,CPUExecutionProvider")
    # Confiança mínima do detector para considerar que é um rosto.
    FACE_MIN_DETECTION_SCORE = _env_float("FACE_MIN_DETECTION_SCORE", 0.55)

    # Threshold de similaridade de cosseno para considerar "a mesma pessoa".
    # 0.45 costuma ser conservador para buffalo_l; 0.35 é mais permissivo.
    FACE_SIMILARITY_THRESHOLD = _env_float("FACE_SIMILARITY_THRESHOLD", 0.45)

    # Quantidade máxima de fotos devolvidas em uma busca.
    MAX_SEARCH_RESULTS = _env_int("MAX_SEARCH_RESULTS", 300)
    # Tamanho máximo da selfie enviada (bytes).
    MAX_SELFIE_LENGTH = _env_int("MAX_SELFIE_LENGTH", 12 * 1024 * 1024)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def as_dict(cls) -> dict:
        return {key: value for key, value in vars(cls).items() if key.isupper()}


def ensure_directories() -> list[Path]:
    """Cria a árvore de diretórios de storage necessária na inicialização."""
    created = []
    for folder in (Config.STORAGE_FOLDER, Config.EVENTS_FOLDER, Config.TEMP_FOLDER):
        folder.mkdir(parents=True, exist_ok=True)
        created.append(folder)
    return created


def event_directories(event_id: int) -> tuple[Path, Path]:
    """Diretórios de originais e thumbnails de um evento."""
    base = Config.EVENTS_FOLDER / str(event_id)
    originals = base / "originals"
    thumbnails = base / "thumbnails"
    originals.mkdir(parents=True, exist_ok=True)
    thumbnails.mkdir(parents=True, exist_ok=True)
    return originals, thumbnails
