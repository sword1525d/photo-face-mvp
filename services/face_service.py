"""Reconhecimento facial com InsightFace (detecção + embeddings).

O modelo é carregado **uma única vez** na inicialização da aplicação
(singleton). Nenhuma rota recarrega o modelo.

O InsightFace é importado de forma tardia e protegida: se a dependência não
estiver instalada (ou falhar no primeiro download do modelo), a aplicação
continua subindo e as rotas devolvem uma mensagem amigável.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

PREFERRED_PROVIDERS = ("CUDAExecutionProvider", "CPUExecutionProvider")

STATUS_OK = "ok"
STATUS_NO_FACE = "no_face"
STATUS_MULTIPLE_FACES = "multiple_faces"
STATUS_ERROR = "error"


@dataclass
class DetectedFace:
    bbox: tuple[int, int, int, int]
    confidence: float
    embedding: np.ndarray


@dataclass
class FaceAnalysisResult:
    status: str
    message: str = ""
    faces: list[DetectedFace] = field(default_factory=list)

    @property
    def embedding(self) -> np.ndarray | None:
        return self.faces[0].embedding if self.faces else None


def resolve_providers(preferred: tuple[str, ...] | list[str] = PREFERRED_PROVIDERS) -> list[str]:
    """Mantém apenas os providers realmente disponíveis no onnxruntime."""
    try:
        import onnxruntime  # type: ignore

        available = set(onnxruntime.get_available_providers())
    except Exception:  # pragma: no cover - só acontece se onnxruntime faltar
        logger.warning("onnxruntime indisponível; usando CPUExecutionProvider")
        return ["CPUExecutionProvider"]

    resolved = [provider for provider in preferred if provider in available]
    if not resolved:
        resolved = ["CPUExecutionProvider"]
    return resolved


class FaceRecognitionService:
    """Wrapper do ``insightface.app.FaceAnalysis``."""

    def __init__(
        self,
        model_name: str = "buffalo_l",
        det_size: int = 640,
        min_detection_score: float = 0.55,
        providers: list[str] | None = None,
        ctx_id: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.det_size = int(det_size)
        self.min_detection_score = float(min_detection_score)
        self.providers = list(providers) if providers else resolve_providers()
        self._ctx_id = ctx_id
        self._app = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._app is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def active_provider(self) -> str:
        return self.providers[0] if self.providers else "CPUExecutionProvider"

    def load(self) -> bool:
        """Carrega o modelo. Idempotente e thread-safe."""
        with self._lock:
            if self._app is not None:
                return True
            try:
                from insightface.app import FaceAnalysis  # import tardio (pesado)
            except ModuleNotFoundError as exc:
                self._load_error = (
                    "O InsightFace não está instalado neste ambiente. "
                    "Rode: pip install -r requirements.txt"
                )
                logger.error("InsightFace ausente: %s", exc)
                return False
            except Exception as exc:
                # Caso clássico em container Linux: `opencv-python` (com GUI) precisa
                # de libGL.so.1, que não existe em imagens "slim".
                detail = f"{type(exc).__name__}: {exc}"
                hint = ""
                if "libgl" in detail.lower():
                    hint = (
                        " Falta uma biblioteca do sistema: use o pacote "
                        "'opencv-python-headless' (padrão do requirements.txt) ou instale "
                        "'libgl1' e 'libglib2.0-0' na imagem (apt-get install -y libgl1 libglib2.0-0)."
                    )
                self._load_error = f"Falha ao carregar o InsightFace ({detail}).{hint}"
                logger.error("Falha ao importar insightface: %s", detail)
                return False

            ctx_id = self._ctx_id
            if ctx_id is None:
                ctx_id = 0 if "CUDAExecutionProvider" in self.providers else -1

            try:
                logger.info(
                    "Carregando InsightFace model=%s providers=%s (primeira execução baixa ~280MB)",
                    self.model_name,
                    self.providers,
                )
                app = FaceAnalysis(
                    name=self.model_name,
                    providers=self.providers,
                    allowed_modules=["detection", "recognition"],
                )
                app.prepare(ctx_id=ctx_id, det_size=(self.det_size, self.det_size))
            except Exception as exc:
                self._load_error = f"Não foi possível carregar o modelo de reconhecimento facial: {exc}"
                logger.exception("Falha ao preparar o InsightFace")
                return False

            self._app = app
            self._load_error = None
            logger.info("InsightFace carregado (provider=%s)", self.active_provider)
            return True

    # ------------------------------------------------------------------
    # Detecção
    # ------------------------------------------------------------------
    def detect(
        self,
        image_bgr: np.ndarray,
        max_side: int | None = None,
        min_score: float | None = None,
    ) -> list[DetectedFace]:
        if self._app is None and not self.load():
            raise RuntimeError(self._load_error or "Modelo de reconhecimento indisponível.")

        image = image_bgr
        if max_side:
            height, width = image.shape[:2]
            largest = max(height, width)
            if largest > max_side:
                import cv2

                scale = max_side / float(largest)
                image = cv2.resize(
                    image,
                    (int(width * scale), int(height * scale)),
                    interpolation=cv2.INTER_AREA,
                )

        threshold = self.min_detection_score if min_score is None else min_score
        with self._lock:
            detected = self._app.get(image)

        faces: list[DetectedFace] = []
        for face in detected:
            score = float(getattr(face, "det_score", 0.0))
            if score < threshold:
                continue
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = getattr(face, "embedding", None)
            if embedding is None:
                continue
            x1, y1, x2, y2 = (int(value) for value in face.bbox[:4])
            faces.append(
                DetectedFace(
                    bbox=(x1, y1, x2, y2),
                    confidence=score,
                    embedding=np.asarray(embedding, dtype=np.float32).ravel(),
                )
            )
        return faces

    def detect_from_path(self, path: str | Path, max_side: int | None = None) -> list[DetectedFace]:
        from .image_service import ImageService

        image = ImageService.load_bgr(path)
        return self.detect(image, max_side=max_side)

    # ------------------------------------------------------------------
    # Selfie
    # ------------------------------------------------------------------
    def analyze_selfie(self, path: str | Path, max_side: int | None = 1600) -> FaceAnalysisResult:
        """Detecta rostos em uma selfie e devolve um resultado pronto para a UI."""
        if self._app is None and not self.load():
            return FaceAnalysisResult(
                status=STATUS_ERROR,
                message=self._load_error or "Reconhecimento facial indisponível no momento.",
            )
        try:
            faces = self.detect_from_path(path, max_side=max_side)
        except Exception:  # pragma: no cover - defensivo
            logger.exception("Erro ao analisar a selfie")
            return FaceAnalysisResult(
                status=STATUS_ERROR,
                message="Não foi possível analisar sua selfie. Tente novamente.",
            )

        if not faces:
            return FaceAnalysisResult(
                status=STATUS_NO_FACE,
                message="Nenhum rosto foi identificado. Tente uma foto frontal e bem iluminada.",
            )
        if len(faces) > 1:
            return FaceAnalysisResult(
                status=STATUS_MULTIPLE_FACES,
                message="Envie uma selfie contendo apenas uma pessoa.",
            )
        return FaceAnalysisResult(status=STATUS_OK, faces=faces, message="")


_service: FaceRecognitionService | None = None
_service_lock = threading.Lock()


def get_face_service() -> FaceRecognitionService:
    """Singleton usado pela aplicação inteira."""
    global _service
    with _service_lock:
        if _service is None:
            from config import Config

            providers = [
                provider.strip()
                for provider in Config.INSIGHTFACE_PROVIDERS.split(",")
                if provider.strip()
            ]
            _service = FaceRecognitionService(
                model_name=Config.INSIGHTFACE_MODEL,
                det_size=Config.INSIGHTFACE_DET_SIZE,
                min_detection_score=Config.FACE_MIN_DETECTION_SCORE,
                providers=resolve_providers(providers),
            )
    return _service
