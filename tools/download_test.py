"""Testa o download do arquivo original (de uma foto ou de várias em ZIP).

    python tools/download_test.py

Não precisa de servidor no ar nem do motor facial: usa o cliente de teste do
Flask com banco, storage e temporários **próprios** (o ``database.db`` e a pasta
``storage/`` do projeto não são tocados). Confere:

* a foto baixada é o ARQUIVO ORIGINAL, byte a byte — nunca o thumbnail;
* o nome sugerido no download é o nome que o fotógrafo deu ao arquivo;
* seleção com várias fotos vira um ZIP com os originais intactos e nomes
  repetidos viram ``foto.jpg`` / ``foto-1.jpg``;
* o RAW preservado (``.nef``) só sai quando pedido com ``?raw=1``;
* seleção vazia, id de outro evento e arquivo ausente devolvem erro amigável;
* os templates do resultado e do painel renderizam os botões de download.

Use ``--keep`` para não apagar a pasta temporária (útil para inspecionar os
arquivos gerados).
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = True

JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
WORK_DIR: Path | None = None


def check(label: str, condition: bool, extra: str = "") -> None:
    global ok
    ok = ok and bool(condition)
    print(("  OK   " if condition else "  FALHA") + f" | {label} {extra}".rstrip())


def make_jpeg(color: tuple[int, int, int], size: tuple[int, int] = (640, 480)) -> bytes:
    """JPEG sintético (as fotos do teste não precisam de rostos)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def configure_environment(work_dir: Path) -> None:
    """Fixa os caminhos ANTES de importar o app (o Config lê o ambiente no import)."""
    os.environ["DATABASE_PATH"] = str(work_dir / "database.db")
    os.environ["STORAGE_FOLDER"] = str(work_dir / "storage")
    os.environ["DEBUG"] = "true"
    os.environ["LOAD_MODEL_ON_STARTUP"] = "false"


def main() -> int:
    global WORK_DIR

    parser = argparse.ArgumentParser(description="Testa o download dos originais.")
    parser.add_argument("--keep", action="store_true", help="Não apaga a pasta temporária.")
    args = parser.parse_args()

    WORK_DIR = Path(tempfile.mkdtemp(prefix="photo-face-download-"))
    configure_environment(WORK_DIR)

    from flask import render_template

    from app import create_app
    from services.photo_service import STATUS_PROCESSED
    from services.search_service import SearchResult

    application = create_app(load_model=False)
    client = application.test_client()
    services = application.extensions["services"]
    events, photos, images = services["events"], services["photos"], services["images"]

    print(f"[1/7] Preparando evento sintético em {WORK_DIR}")
    event = events.create("Teste de download", "Evento sintético dos testes")
    event_id, slug = int(event["id"]), event["slug"]

    jpeg_a = make_jpeg((220, 60, 40))
    jpeg_b = make_jpeg((40, 90, 220))
    jpeg_from_raw = make_jpeg((60, 160, 90))
    nef_bytes = b"MM\x00*" + b"\x00" * 4096 + jpeg_from_raw

    def upload(filename: str, payload: bytes, target: int = event_id) -> int:
        response = client.post(
            f"/admin/event/{target}/upload",
            data={"files": (io.BytesIO(payload), filename)},
            content_type="multipart/form-data",
            headers=JSON_HEADERS,
        )
        body = response.get_json() or {}
        results = body.get("results") or [{}]
        check(f"upload de {filename}", results[0].get("ok") is True, f"-> id {results[0].get('photo_id')}")
        response.close()
        return int(results[0]["photo_id"])

    def call(method: str, path: str, **kwargs) -> dict:
        """Faz a requisição e FECHA a resposta.

        Fechar importa por dois motivos no Windows: o arquivo enviado fica
        travado até a resposta ser fechada, e é no fechamento que o ZIP
        temporário é apagado (``response.call_on_close``).
        """
        response = client.open(path, method=method, **kwargs)
        reply = {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": response.data,
            "json": response.get_json(silent=True),
        }
        response.close()
        return reply

    # Duas fotos com o MESMO nome (para testar nome repetido dentro do ZIP) e um
    # arquivo que ficou pendente de processamento facial.
    photo_a = upload("CORRIDA 01.JPG", jpeg_a)
    download_a = call("GET", f"/evento/{slug}/foto/{photo_a}/baixar")
    thumb_a = call("GET", f"/storage/{photos.get(photo_a)['thumbnail_path']}")
    photo_b = upload("CORRIDA 01.JPG", jpeg_b)
    download_b = call("GET", f"/evento/{slug}/foto/{photo_b}/baixar")

    # Caso RAW: gravado direto no storage (a conversão em si é testada no
    # tools/zip_raw_test.py, e aqui não dependemos do rawpy instalado).
    print("[2/7] Criando uma foto vinda de RAW…")
    originals = images.storage_root / "events" / str(event_id) / "originals"
    thumbnails = images.storage_root / "events" / str(event_id) / "thumbnails"
    (originals / "raw-foto.jpg").write_bytes(jpeg_from_raw)
    (originals / "raw-foto.nef").write_bytes(nef_bytes)
    (thumbnails / "raw-foto.jpg").write_bytes(jpeg_from_raw)
    photo_raw = photos.create(
        event_id,
        "DSC_0042.NEF",
        f"events/{event_id}/originals/raw-foto.jpg",
        f"events/{event_id}/thumbnails/raw-foto.jpg",
        status=STATUS_PROCESSED,
        raw_path=f"events/{event_id}/originals/raw-foto.nef",
    )

    print("[3/7] Download de UMA foto (arquivo original, byte a byte)")
    check("HTTP 200", download_a["status"] == 200, f"-> {download_a['status']}")
    check(
        "conteúdo idêntico ao enviado",
        download_a["body"] == jpeg_a,
        f"({len(download_a['body'])} bytes)",
    )
    check("mime de imagem", download_a["headers"].get("Content-Type", "").startswith("image/jpeg"))
    disposition = download_a["headers"].get("Content-Disposition", "")
    check("enviado como anexo", "attachment" in disposition, f"-> {disposition}")
    check("nome original preservado", "CORRIDA_01.jpg" in disposition, f"-> {disposition}")
    check(
        "não é o thumbnail (que é menor)",
        thumb_a["status"] == 200
        and thumb_a["body"] != download_a["body"]
        and len(thumb_a["body"]) < len(download_a["body"]),
        f"{len(thumb_a['body'])} < {len(download_a['body'])} bytes",
    )
    check("a segunda foto também veio inteira", download_b["body"] == jpeg_b)

    print("[4/7] RAW: o JPEG é o original legível, o .nef é opt-in")
    raw_default = call("GET", f"/evento/{slug}/foto/{photo_raw}/baixar")
    raw_explicit = call("GET", f"/evento/{slug}/foto/{photo_raw}/baixar?raw=1")
    check("sem ?raw=1 vem o JPEG (nome .jpg)", raw_default["body"] == jpeg_from_raw)
    check("nome do arquivo sem ?raw=1", "DSC_0042.jpg" in raw_default["headers"].get("Content-Disposition", ""))
    check("com ?raw=1 vem o .nef preservado", raw_explicit["body"] == nef_bytes)
    check(
        "nome do arquivo com ?raw=1",
        "DSC_0042.nef" in raw_explicit["headers"].get("Content-Disposition", ""),
    )

    print("[5/7] Download de VÁRIAS fotos (ZIP)")
    response_zip = call("POST", f"/evento/{slug}/baixar", data={"ids": [str(photo_a), str(photo_b), str(photo_raw)]})
    check("HTTP 200", response_zip["status"] == 200, f"-> {response_zip['status']}")
    check("mime de zip", response_zip["headers"].get("Content-Type") == "application/zip")
    check(
        "nome do zip",
        f"{slug}-3-fotos.zip" in response_zip["headers"].get("Content-Disposition", ""),
    )
    if response_zip["status"] == 200:
        with zipfile.ZipFile(io.BytesIO(response_zip["body"])) as archive:
            names = archive.namelist()
            check("3 arquivos no zip", len(names) == 3, f"-> {names}")
            check("nome repetido foi desambiguado", len(set(names)) == 3)
            check("ordem preservada (primeira foto)", archive.read(names[0]) == jpeg_a)
            check("ordem preservada (segunda foto)", archive.read(names[1]) == jpeg_b)
            check("a foto do RAW entrou como .jpg", archive.read(names[2]) == jpeg_from_raw)
            check(
                "dentro do zip nada foi recomprimido",
                {item.compress_type for item in archive.infolist()} == {zipfile.ZIP_STORED},
            )

    temp_dir = Path(os.environ["STORAGE_FOLDER"]) / "tmp"
    leftovers = sorted(item.name for item in temp_dir.glob("download-*.zip"))
    if os.name == "nt":
        print("  NOTA  | no Windows o arquivo fica travado enquanto é enviado, então o ZIP deste")
        print("        | envio continua no disco (no Linux, que é o caso do container, sai na hora).")
        check("nenhum ZIP de envios anteriores ficou para trás", len(leftovers) <= 1, f"-> {leftovers}")
    else:
        check("ZIP temporário apagado depois do envio", not leftovers, f"-> {leftovers}")

    # Faxina: uma sobra antiga (acima do limite) some no próximo download.
    stale = temp_dir / "download-esquecido.zip"
    stale.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    old = time.time() - 3600
    os.utime(stale, (old, old))
    check("sobra antiga existe antes do próximo download", stale.exists())
    call("POST", f"/evento/{slug}/baixar", data={"ids": [str(photo_a), str(photo_b)]})
    check("download novo apagou a sobra antiga", not stale.exists())

    single = call("POST", f"/evento/{slug}/baixar", data={"ids": [str(photo_a)]})
    check(
        "seleção com 1 foto não vira ZIP",
        single["status"] == 200
        and single["body"] == jpeg_a
        and single["headers"].get("Content-Type", "").startswith("image/jpeg"),
    )

    print("[6/7] Erros devem ser amigáveis (nada de traceback / 500)")
    empty = call("POST", f"/evento/{slug}/baixar", data={}, headers=JSON_HEADERS)
    check(
        "sem seleção -> 400 com mensagem",
        empty["status"] == 400 and "Selecione" in (empty["json"] or {}).get("error", ""),
        f"-> {empty['json']}",
    )

    other = events.create("Outro evento", "")
    other_photo = upload("OUTRO.JPG", make_jpeg((10, 10, 10)), target=int(other["id"]))
    foreign = call("POST", f"/evento/{slug}/baixar", data={"ids": [str(other_photo)]}, headers=JSON_HEADERS)
    check(
        "id de outro evento é ignorado",
        foreign["status"] == 400,
        f"-> {foreign['status']} {foreign['json']}",
    )
    foreign_single = call("GET", f"/evento/{slug}/foto/{other_photo}/baixar")
    check(
        "id de outro evento no link direto -> 404",
        foreign_single["status"] == 404,
        f"-> {foreign_single['status']}",
    )
    missing_id = call("GET", f"/evento/{slug}/foto/999999/baixar")
    check("id inexistente -> 404", missing_id["status"] == 404, f"-> {missing_id['status']}")

    original_file = originals / Path(photos.get(photo_a)["original_path"]).name
    original_file.unlink()
    gone = call("GET", f"/evento/{slug}/foto/{photo_a}/baixar", headers=JSON_HEADERS)
    check(
        "arquivo sumiu do disco -> mensagem, não 500",
        gone["status"] == 404 and "disponível" in (gone["json"] or {}).get("error", ""),
        f"-> {gone['json']}",
    )
    original_file.write_bytes(jpeg_a)

    print("[7/7] Templates renderizam os botões de download")
    admin_page = client.get(f"/admin/event/{event_id}")
    check("painel responde 200", admin_page.status_code == 200, f"-> {admin_page.status_code}")
    check("painel tem o botão Original", "⤓ Original" in admin_page.get_data(as_text=True))
    check("painel tem o botão RAW", "⤓ RAW" in admin_page.get_data(as_text=True))

    public_page = client.get(f"/evento/{slug}")
    check("página do evento responde 200", public_page.status_code == 200, f"-> {public_page.status_code}")
    check("página do evento expõe as URLs de download", "data-download-url" in public_page.get_data(as_text=True))

    with application.test_request_context():
        html = render_template(
            "results.html",
            event=event,
            results=[
                SearchResult(
                    photo_id=photo_a,
                    similarity=0.91,
                    filename="CORRIDA_01.JPG",
                    thumbnail_path=photos.get(photo_a)["thumbnail_path"],
                    original_path=photos.get(photo_a)["original_path"],
                )
            ],
            elapsed=0.42,
            threshold=0.45,
            show_similarity=True,
        )
    check("resultado (sem JS) tem a caixa de seleção", 'class="js-photo-check"' in html)
    check("resultado (sem JS) tem o formulário de download", 'class="download-form"' in html)
    check("resultado (sem JS) tem o link do original", f"/evento/{slug}/foto/{photo_a}/baixar" in html)

    print()
    print("RESULTADO:", "tudo certo" if ok else "houve falha")
    if args.keep:
        print(f"Pasta temporária mantida em {WORK_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        if WORK_DIR and "--keep" not in sys.argv:
            shutil.rmtree(WORK_DIR, ignore_errors=True)
    raise SystemExit(exit_code)
