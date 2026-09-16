"""Smoke test: valida o pipeline completo (banco + storage + rostos + busca).

    python tools/smoke_test.py

O script:
1. inicializa banco e diretórios;
2. gera fotos sintéticas (se ainda não existirem) com ``create_sample_photos.py``;
3. carrega o InsightFace;
4. detecta rostos e grava embeddings no banco;
5. roda uma busca usando a selfie e mostra as similaridades;
6. apaga o evento de teste criado.

Serve tanto para conferir a instalação quanto para depurar problemas de
reconhecimento sem subir o servidor web.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Config, ensure_directories  # noqa: E402
from services.database_service import DatabaseService  # noqa: E402
from services.event_service import EventService  # noqa: E402
from services.face_service import get_face_service  # noqa: E402
from services.photo_service import STATUS_PROCESSED, PhotoService  # noqa: E402
from services.search_service import FaceSearchService  # noqa: E402

SAMPLE_DIR = ROOT / "tools" / "sample"


def main() -> int:
    ensure_directories()
    db = DatabaseService(Config.DATABASE_PATH)
    db.init_db()
    events = EventService(db)
    photos = PhotoService(db)
    face_service = get_face_service()
    search = FaceSearchService(photos, threshold=Config.FACE_SIMILARITY_THRESHOLD)

    if not (SAMPLE_DIR / "selfie.jpg").exists():
        print("[1/6] Gerando fotos de exemplo…")
        sys.path.insert(0, str(ROOT / "tools"))
        import create_sample_photos  # type: ignore

        sys.argv = ["create_sample_photos.py", "--count", "6"]
        create_sample_photos.main()
    else:
        print("[1/6] Fotos de exemplo já existem.")

    print("[2/6] Carregando InsightFace (pode baixar ~280 MB na primeira vez)…")
    if not face_service.load():
        print(f"  FALHOU: {face_service.load_error}")
        return 1
    print(f"  OK: modelo={face_service.model_name} provider={face_service.active_provider}")

    event = events.create("Smoke Test", "Evento temporário criado pelo smoke test")
    event_id = int(event["id"])
    print(f"[3/6] Evento de teste #{event_id} criado.")

    try:
        files = sorted((SAMPLE_DIR / "photos").glob("*.jpg"))
        if not files:
            print("  Nenhuma foto de exemplo encontrada.")
            return 1

        print(f"[4/6] Processando {len(files)} foto(s)…")
        total_faces = 0
        for path in files:
            faces = face_service.detect_from_path(path, max_side=Config.MAX_DETECTION_SIDE)
            photo_id = photos.create(event_id, path.name, "", None)
            if faces:
                photos.replace_faces(photo_id, event_id, faces)
                photos.set_status(photo_id, STATUS_PROCESSED, faces_count=len(faces))
                total_faces += len(faces)
            print(f"  - {path.name}: {len(faces)} rosto(s)")
        print(f"  Total de rostos: {total_faces}")
        if not total_faces:
            print("  AVISO: nenhum rosto detectado — verifique as fotos de exemplo.")
            return 1

        print("[5/6] Analisando a selfie…")
        analysis = face_service.analyze_selfie(SAMPLE_DIR / "selfie.jpg")
        print(f"  status={analysis.status} mensagem={analysis.message or '-'}")
        if analysis.status != "ok":
            return 1

        results, elapsed = search.search(event_id, analysis.embedding)
        print(f"[6/6] Busca concluída em {elapsed:.3f}s — {len(results)} resultado(s):")
        for item in results:
            print(f"  * {item.filename}: similaridade {item.similarity:.4f}")

        if not results:
            print("  AVISO: nenhuma correspondência acima do threshold. "
                  f"Tente baixar FACE_SIMILARITY_THRESHOLD (atual {Config.FACE_SIMILARITY_THRESHOLD}).")
            return 1

        print("\nPipeline de reconhecimento facial OK.")
        return 0
    finally:
        for photo in photos.delete_by_event(event_id):
            pass
        events.delete(event_id)
        print("Evento de teste removido.")


if __name__ == "__main__":
    raise SystemExit(main())
