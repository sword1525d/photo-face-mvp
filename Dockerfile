# =============================================================================
# Photo Face MVP — imagem de container (Linux)
#
#   docker build -t photo-face-mvp .
#   docker run -p 8080:8080 -v "$PWD/data:/app/data" \
#     -e DATABASE_PATH=/app/data/database.db \
#     -e STORAGE_FOLDER=/app/data/storage \
#     photo-face-mvp
#
# Depois acesse http://localhost:8080 (painel em /admin).
# =============================================================================
FROM python:3.12-slim

# Bibliotecas de sistema:
#   build-essential -> o `insightface` compila um módulo Cython na instalação
#                      (na imagem slim não existe g++ por padrão)
#   libgl1 / libglib2.0-0 -> exigidas pelo `opencv-python` COM GUI. O projeto usa
#                      `opencv-python-headless`, então elas não seriam necessárias,
#                      mas ficam aqui para não quebrar caso alguém instale o
#                      pacote completo por engano (was: "libGL.so.1: cannot open
#                      shared object file")
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    # servidor WSGI de produção (o `flask run` é só para desenvolvimento)
    && pip install --no-cache-dir waitress

COPY . .

# Dados persistentes (banco + fotos). Monte um volume aqui!
ENV DATABASE_PATH=/app/data/database.db \
    STORAGE_FOLDER=/app/data/storage \
    HOST=0.0.0.0 \
    PORT=8080 \
    DEBUG=false

VOLUME ["/app/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

# O modelo é carregado quando o `app` é importado (create_app).
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8080", "--threads=4", "app:app"]
