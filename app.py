"""Photo Face MVP - aplicação Flask.

O ``app.py`` cuida essencialmente de: configuração do Flask, rotas e
tratamento de erros. Toda a lógica de negócio mora em ``services/``.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path

# Evita que o albumentations (dependência do insightface) faça checagem de
# atualização e polua o log a cada inicialização.
os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

from flask import (
    Flask,
    abort,
    after_this_request,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from config import Config, ensure_directories
from services.archive_service import ArchiveService
from services.database_service import DatabaseService
from services.download_service import DownloadError, DownloadService
from services.event_service import EventService, slugify
from services.face_service import (
    STATUS_ERROR as FACE_STATUS_ERROR,
    STATUS_MULTIPLE_FACES as FACE_STATUS_MULTIPLE_FACES,
    STATUS_NO_FACE as FACE_STATUS_NO_FACE,
    get_face_service,
)
from services.image_service import ImageService, ImageValidationError
from services.photo_service import (
    STATUS_ERROR,
    STATUS_LABELS,
    STATUS_NO_FACES,
    STATUS_PENDING,
    STATUS_PROCESSED,
    PhotoService,
)
from services.raw_service import get_raw_service
from services.search_service import FaceSearchService

logger = logging.getLogger("photo_face")


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
def configure_logging() -> None:
    level = logging.DEBUG if Config.DEBUG else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------
def create_app(load_model: bool | None = None) -> Flask:
    configure_logging()
    ensure_directories()

    db = DatabaseService(Config.DATABASE_PATH)
    db.init_db()
    logger.info("Database initialized -> %s", Config.DATABASE_PATH)

    event_service = EventService(db)
    photo_service = PhotoService(db)
    raw_service = get_raw_service()
    image_service = ImageService(
        storage_root=Config.STORAGE_FOLDER,
        thumbnail_width=Config.THUMBNAIL_WIDTH,
        thumbnail_quality=Config.THUMBNAIL_QUALITY,
        allowed_extensions=Config.ALLOWED_EXTENSIONS,
        allowed_mime_types=Config.ALLOWED_MIME_TYPES,
        raw_extensions=Config.RAW_EXTENSIONS,
        raw_service=raw_service,
    )
    archive_service = (
        ArchiveService(
            temp_root=Config.TEMP_FOLDER,
            allowed_extensions=Config.UPLOAD_EXTENSIONS - Config.ARCHIVE_EXTENSIONS,
            archive_extensions=Config.ARCHIVE_EXTENSIONS,
            max_entries=Config.MAX_ZIP_ENTRIES,
            max_entry_size=Config.MAX_ZIP_ENTRY_SIZE,
            max_total_size=Config.MAX_ZIP_TOTAL_SIZE,
            max_compression_ratio=Config.MAX_ZIP_COMPRESSION_RATIO,
        )
        if Config.ALLOW_ARCHIVE_UPLOAD
        else None
    )
    # Download dos originais: o visitante leva o arquivo original, e pode
    # marcar várias fotos para receber tudo em um ZIP só.
    download_service = DownloadService(
        storage_root=Config.STORAGE_FOLDER,
        temp_root=Config.TEMP_FOLDER,
        max_files=Config.MAX_DOWNLOAD_PHOTOS,
        max_total_bytes=Config.MAX_DOWNLOAD_BYTES,
    )
    face_service = get_face_service()
    search_service = FaceSearchService(
        photo_service,
        threshold=Config.FACE_SIMILARITY_THRESHOLD,
        max_results=Config.MAX_SEARCH_RESULTS,
    )
    logger.info("Storage initialized -> %s", Config.STORAGE_FOLDER)

    if load_model is None:
        load_model = Config.LOAD_MODEL_ON_STARTUP
    if load_model:
        face_service.load()
        if face_service.available:
            logger.info("InsightFace loaded (provider=%s)", face_service.active_provider)

    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH
    # Em desenvolvimento os arquivos estáticos devem ser revalidados a cada
    # requisição; em produção podem ficar em cache por 1 hora.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0 if Config.DEBUG else 3600
    app.extensions["services"] = {
        "db": db,
        "events": event_service,
        "photos": photo_service,
        "images": image_service,
        "archives": archive_service,
        "downloads": download_service,
        "raw": raw_service,
        "face": face_service,
        "search": search_service,
    }

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    def wants_json() -> bool:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return True
        if request.form.get("format") == "json":
            return True
        return request.accept_mimetypes.best == "application/json"

    def fail(message: str, status: int = 400, code: str = "error"):
        """Resposta de erro amigável (JSON ou flash + redirect)."""
        if wants_json():
            return jsonify({"ok": False, "error": message, "error_code": code}), status
        flash(message, "error")
        return redirect(request.referrer or url_for("index"))

    def get_event_or_404(event_id: int):
        event = event_service.get(event_id)
        if event is None:
            abort(404)
        return event

    def get_event_by_slug_or_404(slug: str):
        event = event_service.get_by_slug(slug)
        if event is None:
            abort(404)
        return event

    def photo_payload(
        photo_row, saved=None, warning: str | None = None, origin: str | None = None
    ) -> dict:
        raw_path = photo_row["raw_path"] if photo_row and "raw_path" in photo_row.keys() else None
        return {
            "ok": True,
            "photo_id": int(photo_row["id"]) if photo_row else None,
            "filename": photo_row["filename"] if photo_row else None,
            "status": photo_row["status"] if photo_row else STATUS_ERROR,
            "status_label": STATUS_LABELS.get(photo_row["status"], "Erro") if photo_row else "Erro",
            "faces_count": int(photo_row["faces_count"] or 0) if photo_row else 0,
            "thumbnail_url": image_service.storage_url(photo_row["thumbnail_path"]) if photo_row else None,
            "original_url": image_service.storage_url(photo_row["original_path"]) if photo_row else None,
            "raw_url": image_service.storage_url(raw_path),
            "raw_name": Path(raw_path).name if raw_path else None,
            "raw_method": saved.raw_method if saved else None,
            "error": photo_row["error_message"] if photo_row else None,
            "warning": warning,
            "dimension": f"{saved.width}x{saved.height}" if saved else None,
            "origin": origin,
        }

    # ------------------------------------------------------------------
    # Processamento de uma foto (usado pelo upload)
    # ------------------------------------------------------------------
    def process_upload(event_row, file_storage, origin: str | None = None) -> tuple[dict, int]:
        """Grava 1 arquivo, detecta rostos e persiste embeddings."""
        event_id = int(event_row["id"])
        started = time.perf_counter()

        try:
            saved = image_service.save_upload(file_storage, event_id)
        except ImageValidationError as exc:
            logger.warning("Upload rejeitado event=%s motivo=%s", event_id, exc)
            return {
                "ok": False,
                "filename": file_storage.filename,
                "error": str(exc),
                "status": STATUS_ERROR,
                "origin": origin,
            }, 400

        photo_id = photo_service.create(
            event_id=event_id,
            filename=saved.filename,
            original_path=saved.original_path,
            thumbnail_path=saved.thumbnail_path,
            status=STATUS_PENDING,
            raw_path=saved.raw_path,
        )
        logger.info(
            "Upload event=%s foto=%s arquivo=%s %sx%s %.1fKB%s",
            event_id,
            photo_id,
            saved.filename,
            saved.width,
            saved.height,
            saved.size_bytes / 1024.0,
            f" (RAW via {saved.raw_method})" if saved.raw_method else "",
        )

        absolute_original = image_service.absolute(saved.original_path)
        try:
            faces = face_service.detect_from_path(
                absolute_original, max_side=Config.MAX_DETECTION_SIDE
            )
        except Exception as exc:
            logger.exception("Falha ao detectar rostos photo=%s", photo_id)
            # Guarda o motivo real (ex.: libGL faltando no container) para o admin
            # entender por que a foto ficou aguardando.
            reason = (str(exc) or face_service.load_error or "Não foi possível processar esta imagem.")[:300]
            photo_service.set_status(
                photo_id,
                STATUS_PENDING,
                error_message=reason,
                faces_count=0,
            )
            photo = photo_service.get(photo_id)
            return (
                photo_payload(
                    photo,
                    saved,
                    warning="Não foi possível processar esta imagem.",
                    origin=origin,
                ),
                200,
            )

        if not faces:
            photo_service.set_status(photo_id, STATUS_NO_FACES, faces_count=0)
            photo = photo_service.get(photo_id)
            logger.info("Foto %s sem rostos detectáveis", photo_id)
            return (
                photo_payload(
                    photo,
                    saved,
                    warning="Esta imagem não possui rostos detectáveis.",
                    origin=origin,
                ),
                200,
            )

        faces_count = photo_service.replace_faces(photo_id, event_id, faces)
        photo_service.set_status(photo_id, STATUS_PROCESSED, faces_count=faces_count)
        if not event_row["cover_path"]:
            event_service.set_cover(event_id, saved.thumbnail_path)

        photo = photo_service.get(photo_id)
        logger.info(
            "Foto %s processada: %s rosto(s) em %.2fs",
            photo_id,
            faces_count,
            time.perf_counter() - started,
        )
        return photo_payload(photo, saved, origin=origin), 200

    def reprocess_photo(photo_row) -> dict:
        """Roda a detecção novamente em uma foto já gravada.

        Usado pelo botão de reprocessar de um card e pelo botão "reprocessar
        pendentes" (útil quando o motor facial estava indisponível no upload).
        """
        photo_id = int(photo_row["id"])
        event_id = int(photo_row["event_id"])
        try:
            faces = face_service.detect_from_path(
                image_service.absolute(photo_row["original_path"]),
                max_side=Config.MAX_DETECTION_SIDE,
            )
        except Exception as exc:
            reason = (str(exc) or type(exc).__name__)[:300]
            logger.exception("Falha ao reprocessar photo=%s", photo_id)
            # Volta para `pending` (e não `error`) para continuar elegível a nova tentativa.
            photo_service.set_status(photo_id, STATUS_PENDING, error_message=reason)
            return {
                "ok": False,
                "photo_id": photo_id,
                "filename": photo_row["filename"],
                "status": STATUS_PENDING,
                "status_label": STATUS_LABELS.get(STATUS_PENDING, "Aguardando"),
                "error": "Não foi possível processar esta imagem.",
                "detail": reason,
            }

        if not faces:
            photo_service.set_status(photo_id, STATUS_NO_FACES, faces_count=0, error_message=None)
            return photo_payload(
                photo_service.get(photo_id),
                warning="Esta imagem não possui rostos detectáveis.",
            )

        faces_count = photo_service.replace_faces(photo_id, event_id, faces)
        photo_service.set_status(photo_id, STATUS_PROCESSED, faces_count=faces_count, error_message=None)
        event_row = event_service.get(event_id)
        if event_row and not event_row["cover_path"]:
            event_service.set_cover(event_id, photo_row["thumbnail_path"])
        return photo_payload(photo_service.get(photo_id))

    # ------------------------------------------------------------------
    # Rotas públicas
    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        events = event_service.list_events()
        covers = photo_service.cover_paths()
        return render_template(
            "index.html",
            events=events,
            covers=covers,
            storage_url=image_service.storage_url,
        )

    @app.route("/evento/<slug>")
    def public_event(slug: str):
        event = get_event_by_slug_or_404(slug)
        stats = event_service.stats(int(event["id"]))
        return render_template(
            "event.html",
            event=event,
            stats=stats,
            cover_url=image_service.storage_url(event["cover_path"]),
            show_similarity=bool(app.config["DEBUG"]),
        )

    @app.post("/evento/<slug>/search")
    def public_event_search(slug: str):
        event = get_event_by_slug_or_404(slug)
        event_id = int(event["id"])

        if not request.form.get("consent"):
            return fail(
                "Para buscar suas fotos é necessário concordar com o processamento da sua imagem.",
                400,
                "no_consent",
            )

        selfie = request.files.get("selfie")
        if selfie is None or not selfie.filename:
            return fail("Envie uma selfie para iniciar a busca.", 400, "no_file")

        selfie.stream.seek(0, os.SEEK_END)
        size = selfie.stream.tell()
        selfie.stream.seek(0)
        if size > Config.MAX_SELFIE_LENGTH:
            return fail(
                f"A selfie é muito grande (máximo {Config.MAX_SELFIE_LENGTH // (1024 * 1024)} MB).",
                400,
                "too_large",
            )

        if not face_service.available and not face_service.load():
            return fail(
                face_service.load_error or "Reconhecimento facial indisponível no momento.",
                503,
                "engine_unavailable",
            )

        extension = image_service.extension_of(selfie.filename) or "img"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=f".{extension}", dir=str(Config.TEMP_FOLDER), delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                selfie.save(str(temp_path))

            # A extensão enviada não é confiável (celular manda .heic, às vezes
            # renomeado): validamos o CONTEÚDO antes de gastar tempo no modelo.
            try:
                detected_format = image_service.inspect_image(temp_path)
            except ImageValidationError as exc:
                logger.warning("Selfie rejeitada (%s): %s", selfie.filename, exc)
                return fail(str(exc), 400, "invalid_image")

            logger.info(
                "Selfie recebida: arquivo=%s formato=%s tamanho=%.1fKB",
                selfie.filename,
                detected_format,
                size / 1024.0,
            )

            analysis = face_service.analyze_selfie(temp_path)
            if analysis.status == FACE_STATUS_NO_FACE:
                return fail(analysis.message, 422, "no_face")
            if analysis.status == FACE_STATUS_MULTIPLE_FACES:
                return fail(analysis.message, 422, "multiple_faces")
            if analysis.status == FACE_STATUS_ERROR:
                return fail(analysis.message, 500, "analysis_error")

            embedding = analysis.embedding
            assert embedding is not None
            results, elapsed = search_service.search(event_id, embedding)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:  # pragma: no cover
                    logger.warning("Não foi possível apagar a selfie temporária %s", temp_path)

        if wants_json():
            return jsonify(
                {
                    "ok": True,
                    "count": len(results),
                    "event": {"id": event_id, "name": event["name"], "slug": event["slug"]},
                    "threshold": search_service.threshold,
                    "elapsed": round(elapsed, 3),
                    "results": [result.as_dict() for result in results],
                }
            )

        return render_template(
            "results.html",
            event=event,
            results=results,
            elapsed=elapsed,
            threshold=search_service.threshold,
            show_similarity=bool(app.config["DEBUG"]),
        )

    # ------------------------------------------------------------------
    # Arquivos (originais e thumbnails)
    # ------------------------------------------------------------------
    @app.route("/storage/<path:relpath>")
    def storage_file(relpath: str):
        # send_from_directory já protege contra path traversal.
        return send_from_directory(Config.STORAGE_FOLDER, relpath, max_age=86400)

    # ------------------------------------------------------------------
    # Download dos arquivos originais
    # ------------------------------------------------------------------
    def photos_of_event(event_id: int, raw_ids: list[str]) -> list:
        """Converte os ids recebidos e descarta o que não é deste evento.

        O download é público (como a busca), então o formulário nunca é
        confiável: um id de outro evento é simplesmente ignorado, o que também
        não confirma para quem tentou se aquele id existe.
        """
        ids: list[int] = []
        for value in raw_ids:
            for piece in str(value).split(","):
                piece = piece.strip()
                if not piece.isdigit():
                    continue
                photo_id = int(piece)
                if photo_id not in ids:
                    ids.append(photo_id)

        photos = []
        for photo_id in ids:
            photo = photo_service.get(photo_id)
            if photo is not None and int(photo["event_id"]) == event_id:
                photos.append(photo)
        return photos

    @app.get("/evento/<slug>/foto/<int:photo_id>/baixar")
    def public_download_photo(slug: str, photo_id: int):
        """Baixa o ARQUIVO ORIGINAL de uma foto, sem recompressão.

        Para foto vinda de RAW isso é o próprio ``.nef`` (o arquivo como saiu da
        câmera). ``?legivel=1`` pede o JPEG gerado a partir do RAW — útil para
        mandar a foto sem baixar dezenas de MB. ``?raw=1`` continua válido (o
        RAW já é o padrão).
        """
        event = get_event_by_slug_or_404(slug)
        event_id = int(event["id"])
        photo = photo_service.get(photo_id)
        if photo is None or int(photo["event_id"]) != event_id:
            abort(404)

        quer_legivel = request.args.get("legivel", "").strip().lower() in {"1", "true", "sim", "yes"}
        try:
            item = download_service.resolve(photo, prefer_raw=not quer_legivel)
        except DownloadError as exc:
            return fail(str(exc), 404, "file_missing")

        logger.info(
            "Download event=%s foto=%s arquivo=%s %.1fKB%s",
            event_id,
            photo_id,
            item.name,
            item.size / 1024.0,
            " (derivado)" if quer_legivel else "",
        )
        return send_file(item.path, as_attachment=True, download_name=item.name, max_age=0)

    @app.post("/evento/<slug>/baixar")
    def public_download_selection(slug: str):
        """Baixa as fotos marcadas: uma foto vira um arquivo, várias viram ZIP.

        O ZIP leva o mesmo arquivo que o link individual: o original de cada
        foto (o ``.nef`` inteiro, quando a foto veio de RAW) guardado **sem
        compressão** (``ZIP_STORED``), então nada é recompactado no caminho.
        """
        event = get_event_by_slug_or_404(slug)
        photos = photos_of_event(int(event["id"]), request.form.getlist("ids"))
        try:
            files = download_service.collect(photos)
        except DownloadError as exc:
            return fail(str(exc), 400, "download_failed")

        if len(files) == 1:
            item = files[0]
            logger.info(
                "Download event=%s 1 foto em %s (%.1f KB)",
                event["id"],
                item.name,
                item.size / 1024.0,
            )
            return send_file(item.path, as_attachment=True, download_name=item.name, max_age=0)

        try:
            archive_path = download_service.build_zip(files)
        except DownloadError as exc:
            return fail(str(exc), 500, "zip_failed")

        download_name = f"{event['slug']}-{len(files)}-fotos.zip"
        logger.info(
            "Download event=%s %s foto(s) em %s (%.1f MB)",
            event["id"],
            len(files),
            download_name,
            archive_path.stat().st_size / (1024 * 1024),
        )
        response = send_file(
            archive_path,
            # Explícito: o `mimetypes` do sistema (Windows, por exemplo) pode
            # devolver `application/x-zip-compressed` e o arquivo chegar com um
            # tipo estranho no navegador.
            mimetype="application/zip",
            as_attachment=True,
            download_name=download_name,
            max_age=0,
        )
        # O ZIP é temporário. A limpeza vai no `after_this_request` e NÃO no
        # `response.call_on_close`: o `send_file` usa `direct_passthrough`, e
        # nesse modo o Werkzeug não executa os callbacks de fechamento (o
        # arquivo ficaria no disco para sempre). No Linux o arquivo pode ser
        # apagado enquanto está aberto — o envio continua, porque o descritor
        # já está aberto; no Windows o `unlink` falha (e o serviço ignora).
        @after_this_request
        def _remove_temp_archive(current_response):
            download_service.cleanup(archive_path)
            return current_response

        return response

    # ------------------------------------------------------------------
    # Administração
    # ------------------------------------------------------------------
    @app.route("/admin")
    def admin_index():
        events = event_service.list_events()
        covers = photo_service.cover_paths()
        stats = {int(event["id"]): event_service.stats(int(event["id"])) for event in events}
        return render_template(
            "admin.html",
            events=events,
            covers=covers,
            stats=stats,
            storage_url=image_service.storage_url,
        )

    @app.post("/admin/event/create")
    def admin_create_event():
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        event_date = (request.form.get("event_date") or "").strip() or None

        if not name:
            return fail("Informe o nome do evento.", 400, "missing_name")

        try:
            event = event_service.create(name, description, event_date)
        except ValueError as exc:
            return fail(str(exc), 400, "invalid_event")

        logger.info("Evento criado: %s (%s)", event["name"], event["slug"])
        if wants_json():
            return jsonify(
                {
                    "ok": True,
                    "event": {
                        "id": int(event["id"]),
                        "name": event["name"],
                        "slug": event["slug"],
                        "admin_url": url_for("admin_event", event_id=event["id"]),
                        "public_url": url_for("public_event", slug=event["slug"]),
                    },
                }
            ), 201

        flash(f"Evento “{event['name']}” criado com sucesso.", "success")
        return redirect(url_for("admin_event", event_id=event["id"]))

    @app.route("/admin/event/<int:event_id>")
    def admin_event(event_id: int):
        event = get_event_or_404(event_id)
        photos = photo_service.list_by_event(event_id)
        return render_template(
            "admin_event.html",
            event=event,
            photos=photos,
            stats=event_service.stats(event_id),
            status_labels=STATUS_LABELS,
            storage_url=image_service.storage_url,
            face_available=face_service.available,
            face_error=face_service.load_error,
        )

    @app.post("/admin/event/<int:event_id>/upload")
    def admin_upload(event_id: int):
        event = get_event_or_404(event_id)
        files = [
            uploaded
            for uploaded in (request.files.getlist("files") or request.files.getlist("photos"))
            if uploaded and uploaded.filename
        ]
        if not files:
            return fail("Selecione pelo menos uma foto para enviar.", 400, "no_files")

        skipped: list[dict] = []
        work: list[tuple[str, str | None, FileStorage]] = []
        open_handles: list[object] = []
        temp_folders: list[Path] = []

        try:
            # 1) Desmembra ZIPs em fotos individuais (os demais arquivos seguem direto).
            for uploaded in files:
                if archive_service and archive_service.is_archive(uploaded):
                    extraction = archive_service.extract(uploaded, uploaded.filename)
                    if extraction.folder:
                        temp_folders.append(extraction.folder)
                    if extraction.error:
                        skipped.append({"filename": uploaded.filename, "reason": extraction.error})
                    skipped.extend(extraction.skipped_as_dicts())
                    logger.info(
                        "ZIP %s expandido em %s foto(s) para o evento %s",
                        uploaded.filename,
                        len(extraction.entries),
                        event_id,
                    )
                    for entry in extraction.entries:
                        handle = open(entry.path, "rb")
                        open_handles.append(handle)
                        content_type = (
                            mimetypes.guess_type(entry.filename)[0] or "application/octet-stream"
                        )
                        work.append(
                            (
                                entry.filename,
                                entry.origin,
                                FileStorage(
                                    stream=handle,
                                    filename=entry.filename,
                                    content_type=content_type,
                                ),
                            )
                        )
                else:
                    work.append((uploaded.filename, uploaded.filename, uploaded))

            # 2) Processa uma foto por vez (detecção + embedding + banco).
            results = []
            for _name, origin, storage in work:
                payload, _status = process_upload(event, storage, origin=origin)
                results.append(payload)

            stats = event_service.stats(event_id)
            ok_count = sum(1 for item in results if item.get("ok"))

            if not wants_json():
                summary = f"{ok_count} foto(s) processada(s) de {len(work)} enviada(s)."
                if skipped:
                    summary += f" {len(skipped)} arquivo(s) ignorado(s)."
                flash(summary, "success" if ok_count else "error")
                for item in results:
                    if not item.get("ok") and item.get("error"):
                        flash(f"{item.get('filename')}: {item['error']}", "error")
                return redirect(url_for("admin_event", event_id=event_id))

            return jsonify(
                {
                    "ok": ok_count > 0,
                    "files": len(files),
                    "count": len(results),
                    "results": results,
                    "skipped": skipped,
                    "stats": stats,
                }
            )
        finally:
            for handle in open_handles:
                try:
                    handle.close()  # type: ignore[attr-defined]
                except Exception:  # pragma: no cover - defensivo
                    pass
            if archive_service:
                for folder in temp_folders:
                    archive_service.cleanup(folder)

    @app.post("/admin/photo/<int:photo_id>/delete")
    def admin_delete_photo(photo_id: int):
        photo = photo_service.get(photo_id)
        if photo is None:
            return fail("Foto não encontrada.", 404, "not_found")

        event_id = int(photo["event_id"])
        photo_service.delete(photo_id)
        image_service.delete_photo_files(
            photo["original_path"], photo["thumbnail_path"], photo["raw_path"]
        )

        if event_service.get(event_id) and not event_service.get(event_id)["cover_path"]:
            event_service.set_cover(event_id, photo_service.cover_path(event_id))

        logger.info("Foto %s excluída (event=%s)", photo_id, event_id)
        if wants_json():
            return jsonify({"ok": True, "stats": event_service.stats(event_id)})
        flash("Foto excluída.", "success")
        return redirect(url_for("admin_event", event_id=event_id))

    @app.post("/admin/photo/<int:photo_id>/reprocess")
    def admin_reprocess_photo(photo_id: int):
        photo = photo_service.get(photo_id)
        if photo is None:
            return fail("Foto não encontrada.", 404, "not_found")

        if not face_service.available and not face_service.load():
            return fail(
                face_service.load_error or "Reconhecimento facial indisponível.",
                503,
                "engine_unavailable",
            )

        payload = reprocess_photo(photo)
        if wants_json():
            payload["stats"] = event_service.stats(int(photo["event_id"]))
            return jsonify(payload)

        if payload.get("ok"):
            flash(f"{photo['filename']} reprocessada com sucesso.", "success")
        else:
            flash(f"{photo['filename']}: {payload.get('error')}", "error")
        return redirect(url_for("admin_event", event_id=int(photo["event_id"])))

    @app.post("/admin/event/<int:event_id>/reprocess-pending")
    def admin_reprocess_pending(event_id: int):
        """Reprocessa todas as fotos que ficaram aguardando/erro.

        Cenário típico: as fotos foram enviadas quando o motor de reconhecimento
        ainda não estava funcionando (dependência faltando no container).
        """
        get_event_or_404(event_id)
        if not face_service.available and not face_service.load():
            return fail(
                face_service.load_error or "Reconhecimento facial indisponível no momento.",
                503,
                "engine_unavailable",
            )

        pending = [
            photo
            for photo in photo_service.list_by_event(event_id)
            if photo["status"] in (STATUS_PENDING, STATUS_ERROR)
        ]
        results = [reprocess_photo(photo) for photo in pending]
        processed = sum(1 for item in results if item.get("ok"))
        logger.info(
            "Reprocessamento em lote event=%s: %s/%s foto(s) processada(s)",
            event_id,
            processed,
            len(pending),
        )

        if not wants_json():
            flash(
                f"{processed} de {len(pending)} foto(s) reprocessada(s).",
                "success" if processed else "error",
            )
            return redirect(url_for("admin_event", event_id=event_id))

        return jsonify(
            {
                "ok": processed > 0,
                "count": len(results),
                "processed": processed,
                "results": results,
                "stats": event_service.stats(event_id),
            }
        )

    @app.post("/admin/event/<int:event_id>/delete")
    def admin_delete_event(event_id: int):
        event = get_event_or_404(event_id)
        for photo in photo_service.delete_by_event(event_id):
            image_service.delete_photo_files(
                photo["original_path"], photo["thumbnail_path"], photo["raw_path"]
            )
        event_service.delete(event_id)
        image_service.delete_event_folder(event_id)

        logger.info("Evento %s excluído", event_id)
        if wants_json():
            return jsonify({"ok": True})
        flash(f"Evento “{event['name']}” excluído.", "success")
        return redirect(url_for("admin_index"))

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return jsonify(
            {
                "ok": True,
                "database": db.healthcheck(),
                "face_engine": face_service.available,
                "face_provider": face_service.active_provider if face_service.available else None,
                "face_model": face_service.model_name,
                "similarity_threshold": search_service.threshold,
                "raw_support": raw_service.describe(),
                "zip_upload": archive_service is not None,
                "accepted_extensions": sorted(Config.UPLOAD_EXTENSIONS),
            }
        )

    # ------------------------------------------------------------------
    # Erros amigáveis (sem traceback para o usuário)
    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def handle_404(error):
        if wants_json():
            return jsonify({"ok": False, "error": "Página não encontrada.", "error_code": "not_found"}), 404
        return render_template("error.html", code=404, title="Página não encontrada", message="O endereço acessado não existe ou o evento foi removido."), 404

    @app.errorhandler(RequestEntityTooLarge)
    def handle_413(error):
        limit = Config.MAX_CONTENT_LENGTH // (1024 * 1024)
        message = (
            f"O envio é maior que o limite de {limit} MB por requisição. "
            "Envie em lotes menores — ou, se for um ZIP, divida o arquivo em partes "
            f"(o ZIP inteiro conta para esse limite). Dá para aumentar com MAX_CONTENT_LENGTH={limit * 2}."
        )
        if wants_json():
            return jsonify({"ok": False, "error": message, "error_code": "too_large"}), 413
        return render_template("error.html", code=413, title="Arquivo muito grande", message=message), 413

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        if isinstance(error, HTTPException):
            return error
        logger.exception("Erro inesperado: %s", error)
        message = "Ocorreu um erro inesperado. Tente novamente em instantes."
        if wants_json():
            return jsonify({"ok": False, "error": message, "error_code": "internal_error"}), 500
        return render_template("error.html", code=500, title="Erro inesperado", message=message), 500

    # Filtros de template -------------------------------------------------
    @app.template_filter("dt")
    def format_datetime(value: str | None) -> str:
        if not value:
            return "—"
        text = str(value).replace("Z", "").replace("T", " ")
        return text[:16]

    @app.context_processor
    def inject_globals():
        def asset_url(filename: str) -> str:
            """URL do arquivo estático com versão (evita cache velho no navegador)."""
            path = Path(app.static_folder or "") / filename
            try:
                version = int(path.stat().st_mtime)
            except OSError:  # pragma: no cover - defensivo
                version = 0
            return url_for("static", filename=filename, v=version)

        return {
            "app_name": "Photo Face",
            "storage_url": image_service.storage_url,
            "asset_url": asset_url,
        }

    return app


app = create_app()


def main() -> None:
    banner = [
        "",
        "  Photo Face MVP",
        "  ----------------",
        f"  Database initialized : {Config.DATABASE_PATH}",
        f"  Storage initialized  : {Config.STORAGE_FOLDER}",
    ]
    face = app.extensions["services"]["face"]
    raw = app.extensions["services"]["raw"]
    if face.available:
        banner.append(f"  InsightFace loaded   : {face.model_name} ({face.active_provider})")
    else:
        banner.append("  InsightFace          : indisponível (veja 'face_error' em /healthz)")
    banner.append(
        "  Upload aceito        : jpg, jpeg, png, webp, nef/raw ("
        + ("rawpy" if raw.describe()["rawpy"] else "preview") + "), zip"
    )
    host = Config.HOST
    display_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
    banner.append(f"  Server running at    : http://{display_host}:{Config.PORT}")
    banner.append(f"  Admin                : http://{display_host}:{Config.PORT}/admin")
    banner.append("")
    print("\n".join(banner))
    logger.info("Servidor iniciado em http://%s:%s", display_host, Config.PORT)

    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()


# Reexportado apenas por conveniência de testes/manutenção.
__all__ = ["app", "create_app", "main", "slugify"]
