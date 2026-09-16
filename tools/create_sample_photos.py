"""Gera fotos sintéticas para testar o fluxo completo sem precisar de fotos reais.

Uso (com o ambiente virtual ativado):

    python tools/create_sample_photos.py --count 8

Cria em ``tools/sample/photos`` variações (cortes, rotações e brilho) de um
retrato de domínio público que acompanha o scikit-image e uma ``selfie.jpg``
diferente das demais. Depois:

1. Crie um evento no painel (/admin).
2. Envie todas as fotos de ``tools/sample/photos``.
3. Abra a página pública do evento e envie ``tools/sample/selfie.jpg``.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

OUTPUT_DIR = Path(__file__).resolve().parent / "sample"

# Posição aproximada do rosto (em coordenadas normalizadas) no retrato de
# exemplo usado para gerar as fotos de teste.
FACE_CENTER = (0.44, 0.23)


def load_portrait() -> Image.Image:
    """Retrato real usado apenas para testes (astronauta da NASA, domínio público)."""
    try:
        from skimage import data

        return Image.fromarray(data.astronaut().astype("uint8"))
    except Exception:  # pragma: no cover - fallback sem scikit-image
        rng = np.random.default_rng(7)
        array = (rng.random((512, 512, 3)) * 255).astype("uint8")
        return Image.fromarray(array)


def random_crop(image: Image.Image, rng: random.Random, zoom: float = 1.0) -> Image.Image:
    """Recorta uma região ao redor do rosto do retrato de exemplo.

    ``zoom`` < 1 produz um enquadramento mais fechado (usado na selfie).
    """
    width, height = image.size
    cx = int(width * (FACE_CENTER[0] + rng.uniform(-0.03, 0.03)))
    cy = int(height * (FACE_CENTER[1] + rng.uniform(-0.03, 0.03)))
    half = int(min(width, height) * 0.21 * zoom * rng.uniform(0.92, 1.08))
    left = max(0, cx - half)
    top = max(0, cy - half)
    return image.crop((left, top, min(width, cx + half), min(height, cy + half)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera fotos de exemplo para o teste do MVP.")
    parser.add_argument("--count", type=int, default=8, help="Quantidade de fotos do evento.")
    parser.add_argument("--seed", type=int, default=42, help="Semente aleatória.")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    portrait = load_portrait()

    photos_dir = OUTPUT_DIR / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    for index in range(1, args.count + 1):
        photo = random_crop(portrait, rng)
        photo = photo.rotate(rng.uniform(-12, 12), resample=Image.BICUBIC, expand=True)
        photo = ImageEnhance.Brightness(photo).enhance(rng.uniform(0.85, 1.15))
        photo = ImageEnhance.Contrast(photo).enhance(rng.uniform(0.9, 1.1))
        photo = photo.resize((rng.randint(700, 1100), rng.randint(700, 1100)), Image.LANCZOS)
        photo.convert("RGB").save(photos_dir / f"evento-{index:02d}.jpg", quality=88)

    # Selfie: enquadramento diferente (mais fechado) da mesma pessoa.
    selfie = random_crop(portrait, rng, zoom=0.8).rotate(
        rng.uniform(-6, 6), resample=Image.BICUBIC, expand=True
    )
    selfie = ImageEnhance.Brightness(selfie).enhance(1.05)
    selfie.convert("RGB").save(OUTPUT_DIR / "selfie.jpg", quality=90)

    print(f"{args.count} foto(s) em: {photos_dir}")
    print(f"selfie em:      {OUTPUT_DIR / 'selfie.jpg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
