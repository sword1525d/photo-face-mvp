"""Testa o upload de ZIP com várias fotos e de arquivos RAW (.NEF).

    # numa janela:  python app.py
    # noutra:       python tools/zip_raw_test.py

O script monta um ZIP "sujo" (com subpasta, arquivo de texto, ZIP dentro de ZIP,
caminho malicioso, metadados do macOS e uma entrada com compressão absurda),
cria um NEF sintético (container TIFF com preview JPEG embutido) e confere:

* as fotos válidas do ZIP são extraídas e processadas;
* entradas inválidas são **ignoradas com motivo**, sem derrubar o upload;
* nomes com `../` não escapam do storage;
* o `.nef` é aceito, convertido e tem o original preservado em disco;
* um `.nef` corrompido devolve erro amigável (sem traceback).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402

SAMPLE_DIR = ROOT / "tools" / "sample"
WORK_DIR = SAMPLE_DIR / "zipraw"
EVENT_NAME = "Teste ZIP e NEF"
EVENT_SLUG = "teste-zip-e-nef"

ok = True


def check(label: str, condition: bool, extra: str = "") -> None:
    global ok
    ok = ok and condition
    print(("  OK   " if condition else "  FALHA") + f" | {label} {extra}".rstrip())


def build_zip(destination: Path, photos: list[Path], nef: Path) -> None:
    photo_bytes = [path.read_bytes() for path in photos]
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("corrida/evento-01.jpg", photo_bytes[0])
        archive.writestr("corrida/subpasta/evento-02.jpg", photo_bytes[1])
        archive.writestr("evento-03.jpg", photo_bytes[2])
        archive.writestr("nef/sintetico.nef", nef.read_bytes())
        # --- entradas que precisam ser ignoradas ---
        archive.writestr("../../../fora-do-storage.jpg", photo_bytes[3])
        archive.writestr("leia-me.txt", "isto não é uma foto")
        archive.writestr("__MACOSX/._evento-01.jpg", b"\x00\x00\x01\x00mac")
        archive.writestr(".DS_Store", b"\x00\x00\x01Bud1")
        archive.writestr("interno.zip", b"PK\x03\x04falso")
        archive.writestr("bomba.jpg", b"\x00" * (20 * 1024 * 1024))  # razão ~1000:1


def build_synthetic_nef(destination: Path, source: Path) -> None:
    """NEF falso = container TIFF com um JPEG completo embutido (como nas câmeras)."""
    with Image.open(source) as image:
        image = image.convert("RGB")
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=92)
        payload = buffer.getvalue()
    destination.write_bytes(b"MM\x00*" + b"\x00" * 65536 + payload + b"\x00" * 512)


def main() -> int:
    parser_base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
    base = parser_base.rstrip("/")
    session = requests.Session()
    json_headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

    work = WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    photos = sorted((SAMPLE_DIR / "photos").glob("*.jpg"))
    if len(photos) < 4:
        print("Fotos de exemplo insuficientes. Rode antes: python tools/create_sample_photos.py --count 6")
        return 1

    print("[1/6] Montando arquivos de teste…")
    nef = work / "sintetico.nef"
    build_synthetic_nef(nef, photos[0])
    broken_nef = work / "corrompido.nef"
    broken_nef.write_bytes(b"MM\x00*" + b"\x00" * 4096)
    archive_path = work / "lote-de-fotos.zip"
    build_zip(archive_path, photos, nef)
    print(f"       ZIP: {archive_path.name} ({archive_path.stat().st_size / 1024:.0f} KB)")
    print(f"       NEF: {nef.name} ({nef.stat().st_size / 1024:.0f} KB)")

    print("[2/6] GET /healthz")
    health = session.get(f"{base}/healthz", timeout=30).json()
    raw_support = health.get("raw_support", {})
    print(f"       rawpy={raw_support.get('rawpy')} zip_upload={health.get('zip_upload')}")
    check("rawpy disponível", bool(raw_support.get("rawpy")))
    check("upload de ZIP habilitado", bool(health.get("zip_upload")))
    check("nef entre as extensões aceitas", "nef" in (health.get("accepted_extensions") or []))

    # Os kwargs usados pelo serviço precisam ser aceitos pelo rawpy de verdade.
    import rawpy  # type: ignore

    from services.raw_service import RawImageService  # noqa: E402

    try:
        rawpy.Params(**RawImageService.POSTPROCESS_KWARGS)
        params_ok = True
    except Exception as exc:  # pragma: no cover - só se o rawpy mudar de API
        params_ok = False
        print(f"       erro nos parâmetros do rawpy: {exc}")
    check("parâmetros do rawpy aceitos", params_ok)

    print("[3/6] Criando evento de teste…")
    response = session.post(
        f"{base}/admin/event/create",
        data={"name": EVENT_NAME, "description": "Teste automatizado de ZIP e NEF."},
        timeout=60,
    )
    if "admin/event/" not in response.url:
        print(f"       não foi possível criar o evento (status {response.status_code})")
        return 1
    event_id = int(response.url.rstrip("/").split("/")[-1])
    print(f"       evento id={event_id}")

    print("[4/6] Enviando o ZIP (com subpastas, lixo e caminho malicioso)…")
    with archive_path.open("rb") as handle:
        response = session.post(
            f"{base}/admin/event/{event_id}/upload",
            files=[("files", (archive_path.name, handle, "application/zip"))],
            headers=json_headers,
            timeout=600,
        )
    payload = response.json()
    results = payload.get("results", [])
    skipped = payload.get("skipped", [])
    names = sorted(item["filename"] for item in results)
    print(f"       processadas: {len(results)} -> {names}")
    for entry in skipped:
        print(f"       ignorado: {entry['filename']} ({entry['reason']})")

    check("5 fotos válidas do ZIP processadas", len(results) == 5, f"({len(results)})")
    check("subpasta achatada no nome", "evento-01.jpg" in names and "evento-03.jpg" in names)
    check("nef dentro do ZIP processado", "sintetico.nef" in names)
    check("leia-me.txt ignorado", any("leia-me.txt" == e["filename"] for e in skipped))
    check("zip dentro de zip ignorado", any("interno.zip" == e["filename"] for e in skipped))
    check("metadados do macOS ignorados", all("__MACOSX" not in e["filename"] for e in results))
    check("bomba de compressão ignorada", any("bomba" in e["filename"] for e in skipped))
    traversal = next((item for item in results if item["filename"] == "fora-do-storage.jpg"), None)
    check("caminho malicioso virou basename", traversal is not None)
    if traversal and traversal.get("original_url"):
        check(
            "foto do caminho malicioso ficou dentro do storage",
            "/events/" in traversal["original_url"],
            traversal["original_url"],
        )

    print("[5/6] Verificando o RAW preservado e o preview")
    nef_result = next((item for item in results if item["filename"].lower().endswith(".nef")), None)
    check("nef convertido tem raw_url", bool(nef_result and nef_result.get("raw_url")))
    check(
        "metodo de conversão registrado",
        bool(nef_result and nef_result.get("raw_method")) if nef_result else False,
        f"({nef_result.get('raw_method') if nef_result else '-'})",
    )
    check(
        "rosto detectado no nef",
        bool(nef_result and (nef_result.get("faces_count") or 0) >= 1),
        f"({nef_result.get('faces_count') if nef_result else 0})",
    )
    if nef_result and nef_result.get("raw_url"):
        raw_response = session.get(f"{base}{nef_result['raw_url']}", timeout=60)
        check("RAW acessível em /storage", raw_response.status_code == 200, f"({raw_response.status_code})")
        check("RAW é o arquivo .nef original", raw_response.content[:4] == b"MM\x00*")

    traversal_file = Path(Config.STORAGE_FOLDER).parent / "fora-do-storage.jpg"
    check("nada foi escrito fora do storage", not traversal_file.exists())

    print("[6/6] Enviando um NEF corrompido (deve falhar com mensagem amigável)")
    with broken_nef.open("rb") as handle:
        response = session.post(
            f"{base}/admin/event/{event_id}/upload",
            files=[("files", (broken_nef.name, handle, "application/octet-stream"))],
            headers=json_headers,
            timeout=300,
        )
    broken_payload = response.json()
    broken_result = (broken_payload.get("results") or [{}])[0]
    print(f"       resposta: {broken_result.get('error')}")
    check("nef corrompido rejeitado", broken_result.get("ok") is False)
    check(
        "mensagem amigável (sem traceback)",
        "RAW" in (broken_result.get("error") or "") and "Traceback" not in response.text,
    )

    print(f"\nPágina pública: {base}/evento/{EVENT_SLUG}")
    print("RESULTADO:", "TODOS OS TESTES PASSARAM" if ok else "HOUVE FALHAS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
