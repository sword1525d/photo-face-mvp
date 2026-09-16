"""Valida a detecção de formato da SELFIE pelo conteúdo (não pela extensão).

    # numa janela:  python app.py
    # noutra:       python tools/selfie_formats_test.py

Motivação: o erro real "PIL.UnidentifiedImageError: cannot identify image file
...tmpxxxx.png" acontece quando o arquivo enviado não é o que a extensão diz
(ou está corrompido/incompleto). A busca deve:

* **funcionar** com o conteúdo real, mesmo se a extensão estiver errada
  (HEIC chamado de `.png`, JPEG sem extensão, etc.);
* **explicar** o que o arquivo é quando não dá para usar (ZIP, RAW, PDF, vídeo,
  arquivo corrompido) — sempre com HTTP 400 e sem traceback.

Este script sobe um evento de teste com fotos sintéticas, envia uma selfie real
em vários "disfarces" e confere as respostas.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# O app registra o leitor de HEIC em `services.image_service`; aqui fazemos o
# mesmo para conseguir *gerar* um HEIC de teste (o pillow-heif também escreve).
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_AVAILABLE = True
except Exception:  # pragma: no cover - ambiente sem pillow-heif
    HEIC_AVAILABLE = False

SAMPLE_DIR = ROOT / "tools" / "sample"
WORK_DIR = SAMPLE_DIR / "formats"
EVENT_NAME = "Teste Formatos"
SLUG = "teste-formatos"

ok = True


def check(label: str, condition: bool, extra: str = "") -> None:
    global ok
    ok = ok and condition
    print(("  OK   " if condition else "  FALHA") + f" | {label} {extra}".rstrip())


def build_files(work: Path) -> dict[str, Path]:
    """Cria a mesma selfie em vários formatos/extensões."""
    work.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    source = Image.open(SAMPLE_DIR / "selfie.jpg").convert("RGB")

    # HEIC de verdade (foto de iPhone) — precisa do pillow-heif, que vem no
    # requirements. Se não estiver instalado, o teste avisa e segue.
    if HEIC_AVAILABLE:
        try:
            heic = work / "IMG_0001.HEIC"
            source.save(heic, format="HEIF", quality=90)
            files["heic"] = heic

            disguised = work / "selfie-heic-com-nome-png.png"
            disguised.write_bytes(heic.read_bytes())
            files["heic_com_extensao_errada"] = disguised
        except Exception as exc:  # pragma: no cover - codificador HEIC ausente
            print(f"  (aviso) não foi possível gerar HEIC: {exc}")
    else:
        print("  (aviso) pillow-heif não instalado: os casos HEIC serão pulados")

    # JPEG válido com nome/formatos "suspeitos".
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=90)
    jpeg_bytes = buffer.getvalue()

    files["jpeg_ok"] = work / "selfie.jpg"
    files["jpeg_ok"].write_bytes(jpeg_bytes)

    sem_extensao = work / "selfie"
    sem_extensao.write_bytes(jpeg_bytes)
    files["sem_extensao"] = sem_extensao

    txt = work / "selfie.txt"
    txt.write_bytes(jpeg_bytes)
    files["extensao_txt_conteudo_jpeg"] = txt

    # Arquivos que NÃO são imagem.
    corrompido = work / "corrompido.png"
    corrompido.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 400)  # assinatura + lixo
    files["png_corrompido"] = corrompido

    vazio = work / "vazio.png"
    vazio.write_bytes(b"")
    files["arquivo_vazio"] = vazio

    zipado = work / "selfie.jpg.zip"
    with zipfile.ZipFile(zipado, "w") as archive:
        archive.writestr("foto.jpg", jpeg_bytes)
    files["zip"] = zipado

    nef = work / "selfie.nef"
    nef.write_bytes(b"MM\x00*" + b"\x00" * 2048)
    files["nef"] = nef

    pdf = work / "selfie.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"\x00" * 512)
    files["pdf"] = pdf

    video = work / "selfie.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
    files["video"] = video

    return files


def search(base: str, slug: str, path: Path, session: requests.Session) -> tuple[int, dict]:
    headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    with path.open("rb") as handle:
        response = session.post(
            f"{base}/evento/{slug}/search",
            files={"selfie": (path.name, handle, "application/octet-stream")},
            data={"consent": "1"},
            headers=headers,
            timeout=300,
        )
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, {"raw": response.text[:200]}


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
    session = requests.Session()
    json_headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

    photos = sorted((SAMPLE_DIR / "photos").glob("*.jpg"))
    if not photos:
        print("Gere as fotos de exemplo: python tools/create_sample_photos.py --count 6")
        return 1

    print("[1/4] Montando as selfies de teste…")
    files = build_files(WORK_DIR)
    for name, path in files.items():
        print(f"       {name}: {path.name} ({path.stat().st_size} bytes)")

    print("[2/4] Criando evento de teste com fotos que contêm o mesmo rosto…")
    response = session.post(
        f"{base}/admin/event/create",
        data={"name": EVENT_NAME, "description": "Validação de formatos de selfie."},
        timeout=60,
    )
    if "admin/event/" not in response.url:
        print(f"       falha ao criar o evento (status {response.status_code})")
        return 1
    event_id = int(response.url.rstrip("/").split("/")[-1])
    with_existing = session.get(f"{base}/evento/{SLUG}", timeout=60)
    if with_existing.status_code != 200:
        print(f"       página pública indisponível ({with_existing.status_code})")
        return 1
    handles = [("files", (p.name, p.open("rb"), "image/jpeg")) for p in photos]
    upload = session.post(
        f"{base}/admin/event/{event_id}/upload", files=handles, headers=json_headers, timeout=600
    ).json()
    print(f"       {upload['stats']['processed']} foto(s) processada(s), {upload['stats']['faces']} rosto(s)")
    if upload["stats"]["faces"] == 0:
        print("       nenhum rosto detectado nas fotos de exemplo")
        return 1

    print("[3/4] Enviando a MESMA selfie em formatos diferentes (deve funcionar)…")
    for key, label in [
        ("jpeg_ok", "JPEG normal"),
        ("sem_extensao", "sem extensão"),
        ("extensao_txt_conteudo_jpeg", "extensão .txt com conteúdo JPEG"),
        ("heic", "HEIC de verdade (foto de iPhone)"),
        ("heic_com_extensao_errada", "HEIC com extensão .png"),
    ]:
        if key not in files:
            continue
        status, body = search(base, SLUG, files[key], session)
        encontradas = body.get("count")
        print(f"       {label}: HTTP {status} · encontradas={encontradas} · erro={body.get('error')}")
        check(f"busca funciona com {label}", status == 200 and (encontradas or 0) > 0)

    print("[4/4] Enviando arquivos que NÃO são selfie (deve explicar e devolver 400)…")
    expectations = {
        "png_corrompido": "corrompido",
        "arquivo_vazio": "corrompido",
        "zip": "ZIP",
        "nef": "RAW",
        "pdf": "PDF",
        "video": "vídeo",
    }
    for key, esperado in expectations.items():
        status, body = search(base, SLUG, files[key], session)
        mensagem = body.get("error") or ""
        print(f"       {key}: HTTP {status} · {mensagem[:100]}")
        check(f"{key} rejeitado com 400", status == 400, f"(status {status})")
        check(f"{key} explica o formato", esperado.lower() in mensagem.lower())
        check(f"{key} sem traceback", "Traceback" not in str(body))

    session.post(f"{base}/admin/event/{event_id}/delete", timeout=60)
    print("\nEvento de teste removido.")
    print("RESULTADO:", "TODOS OS TESTES PASSARAM" if ok else "HOUVE FALHAS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
