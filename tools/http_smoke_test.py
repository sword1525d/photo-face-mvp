"""Teste de fumaça via HTTP: exercita o fluxo completo contra um servidor rodando.

    # numa janela:  python app.py
    # noutra:       python tools/http_smoke_test.py

Fluxo testado (o mesmo do critério de aceite do MVP):

    criar evento -> upload em lote -> detecção de rostos -> busca por selfie
    -> galeria de resultados -> exclusão de foto

Use ``--keep`` para não apagar o evento criado ao final.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "tools" / "sample"
EVENT_NAME = "Corrida Manaus 2026"
EVENT_SLUG = "corrida-manaus-2026"


def fail(message: str) -> None:
    print(f"  FALHOU: {message}")
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Testa o fluxo completo via HTTP.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--keep", action="store_true", help="Não remove o evento no final.")
    parser.add_argument(
        "--password", default="", help="Senha do painel (padrão: ADMIN_PASSWORD ou 264079)."
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = requests.Session()
    json_headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

    # O painel pede senha (o site público não). Sem entrar, os passos de
    # administração cairiam na tela de login e o teste falharia com um erro
    # enganoso lá na frente.
    password = args.password or os.environ.get("ADMIN_PASSWORD", "264079")
    login = session.post(f"{base}/admin/login", data={"password": password}, timeout=30)
    if "/admin/login" in login.url:
        fail("não consegui entrar no painel — a senha mudou? (use --password ou ADMIN_PASSWORD)")

    print(f"[1/7] GET {base}/healthz")
    health = session.get(f"{base}/healthz", timeout=30).json()
    print("      ", health)
    if not health.get("face_engine"):
        fail("o motor de reconhecimento facial não está disponível.")

    photos = sorted((SAMPLE_DIR / "photos").glob("*.jpg"))
    selfie = SAMPLE_DIR / "selfie.jpg"
    if not photos or not selfie.exists():
        fail("fotos de exemplo não encontradas. Rode: python tools/create_sample_photos.py")

    print(f"[2/7] POST /admin/event/create ({EVENT_NAME})")
    response = session.post(
        f"{base}/admin/event/create",
        data={"name": EVENT_NAME, "description": "Fotos oficiais do evento.", "event_date": "2026-08-16"},
        timeout=60,
    )
    if response.status_code != 200 or "admin/event/" not in response.url:
        fail(f"não foi possível criar o evento (status {response.status_code})")
    event_id = int(response.url.rstrip("/").split("/")[-1])
    print(f"       evento criado: id={event_id} url={response.url}")

    print(f"[3/7] POST /admin/event/{event_id}/upload com {len(photos)} fotos (upload em lote)")
    files = [("files", (path.name, path.open("rb"), "image/jpeg")) for path in photos]
    response = session.post(
        f"{base}/admin/event/{event_id}/upload", files=files, headers=json_headers, timeout=600
    )
    payload = response.json()
    total_faces = sum(item.get("faces_count") or 0 for item in payload["results"])
    print(f"       upload: {payload['count']} arquivo(s), {total_faces} rosto(s) detectado(s)")
    for item in payload["results"]:
        status = "ok" if item.get("ok") else "erro"
        print(f"       - {item.get('filename')}: {status} ({item.get('status')}, {item.get('faces_count')} rosto(s))")
    if payload["stats"]["processed"] == 0:
        fail("nenhuma foto foi processada.")

    print(f"[4/7] GET /admin/event/{event_id} (painel do evento)")
    response = session.get(f"{base}/admin/event/{event_id}", timeout=60)
    if response.status_code != 200:
        fail(f"painel do evento devolveu {response.status_code}")
    print("       ok")

    print(f"[5/7] GET /evento/{EVENT_SLUG} (página pública)")
    response = session.get(f"{base}/evento/{EVENT_SLUG}", timeout=60)
    if response.status_code != 200 or "Encontre suas fotos" not in response.text:
        fail(f"página pública devolveu {response.status_code}")
    print("       ok")

    print("[6/7] POST /evento/<slug>/search com a selfie")
    with selfie.open("rb") as handle:
        response = session.post(
            f"{base}/evento/{EVENT_SLUG}/search",
            files={"selfie": (selfie.name, handle, "image/jpeg")},
            data={"consent": "1"},
            headers=json_headers,
            timeout=300,
        )
    result = response.json()
    if not response.ok or not result.get("ok"):
        fail(f"busca falhou ({response.status_code}): {result.get('error')}")
    print(f"       encontradas {result['count']} foto(s) em {result['elapsed']}s")
    for item in result["results"]:
        print(f"       - photo_id={item['photo_id']} similaridade={item['similarity']} {item['filename']}")
    if result["count"] == 0:
        fail("nenhuma foto correspondente encontrada (verifique o threshold).")

    print("[7/7] POST /admin/photo/<id>/delete")
    photo_id = result["results"][0]["photo_id"]
    response = session.post(f"{base}/admin/photo/{photo_id}/delete", headers=json_headers, timeout=60)
    if not response.json().get("ok"):
        fail("não foi possível excluir a foto.")
    print(f"       foto {photo_id} excluída; restam {response.json()['stats']['total']} foto(s)")

    if not args.keep:
        session.post(f"{base}/admin/event/{event_id}/delete", timeout=60)
        print("       evento de teste removido (use --keep para mantê-lo).")

    print(f"\nFluxo completo OK. Página pública: {base}/evento/{EVENT_SLUG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
