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
#
# Porta: o servidor escuta em $PORT, com 8080 como padrão quando a variável
# não existe (uso local). Em plataformas como o Railway, que injetam $PORT,
# basta não definir nada — o valor da plataforma é respeitado.
#
# Regra de manutenção desta imagem: NUNCA coloque um comentário (`#`) no meio
# de uma instrução que usa `\` para continuar na linha seguinte. O Docker
# remove esses comentários, mas outros parsers de Dockerfile (linters, CI,
# pre-flight de plataformas de deploy) tratam a linha como o fim da instrução
# e reprovam o arquivo. Comentários vão SEMPRE acima da instrução.
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

# Dependências do projeto (versões fixadas em requirements.txt).
RUN pip install --no-cache-dir -r requirements.txt

# Servidor WSGI de produção (o `flask run` é só para desenvolvimento).
RUN pip install --no-cache-dir waitress

COPY . .

# Dados persistentes: banco SQLite + fotos ficam em /app/data.
# O volume NÃO é declarado com `VOLUME` de propósito — quem monta é o operador
# (Railway: Settings > Volumes apontando para /app/data; local:
# `docker run -v "$PWD/data:/app/data"`). Sem volume montado, os dados vivem
# apenas dentro do container e somem quando ele é recriado.
ENV DATABASE_PATH=/app/data/database.db \
    STORAGE_FOLDER=/app/data/storage \
    HOST=0.0.0.0 \
    PORT=8080 \
    DEBUG=false

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

# O modelo é carregado quando o `app` é importado (create_app).
# `sh -c` é necessário para expandir `${PORT}` em tempo de execução (o exec form
# do Dockerfile não passa por shell); o `exec` mantém o waitress como PID 1,
# para receber SIGTERM no restart do container.
CMD ["sh", "-c", "exec waitress-serve --host=0.0.0.0 --port=${PORT:-8080} --threads=4 app:app"]
