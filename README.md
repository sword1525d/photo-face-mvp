# Photo Face MVP

Plataforma web de **busca de fotos por reconhecimento facial**, feita 100% em Python
(Flask + InsightFace + OpenCV + NumPy + SQLite + Pillow), com frontend em HTML, CSS e
JavaScript puro servido pelo próprio Flask.

O organizador cria um evento e envia as fotos; o sistema detecta cada rosto, gera o
embedding e guarda no banco. O visitante abre a página pública do evento, envia uma
selfie e recebe **apenas as fotos em que ele aparece**.

```
UPLOAD DAS FOTOS -> DETECÇÃO DOS ROSTOS -> EMBEDDINGS -> BANCO
        -> SELFIE -> EMBEDDING DA SELFIE -> COMPARAÇÃO -> GALERIA
```

---

## 1. Funcionalidades

**Administrador**

- Criar eventos (o *slug* é gerado automaticamente: `Corrida Manaus 2026` → `corrida-manaus-2026`).
- Upload de várias fotos de uma vez, com **drag and drop** e **barra de progresso**.
- **Upload de ZIP** com dezenas de fotos: o arquivo é descompactado e cada foto entra na fila
  (pastas internas são achatadas e arquivos inválidos são reportados, sem derrubar o envio).
- **Aceita RAW de câmera (`.NEF` e equivalentes)**: o RAW é convertido para JPEG para exibição
  e o **arquivo original é preservado**, baixável na galeria do painel (`⤓ Original` e `⤓ RAW`).
- **Aceita HEIC/HEIF** (fotos de iPhone e de vários Android) e valida todo arquivo pelo
  **conteúdo real**, não pela extensão — um arquivo renomeado funciona e um arquivo inválido
  recebe uma explicação do que ele é de verdade.
- Processamento automático: detecção de todos os rostos, geração dos embeddings e gravação no SQLite.
- Estatísticas do evento: total, processadas, sem rostos, com erro e rostos detectados.
- Excluir foto, reprocessar foto e excluir evento.

**Visitante**

- Página pública do evento com identidade visual própria.
- Envio de selfie com **consentimento obrigatório**.
- Mensagens claras para "nenhum rosto identificado" e "envie uma selfie com apenas uma pessoa".
- Galeria com grid responsivo (4–5 colunas no desktop, 2 no mobile) e lightbox (imagem ampliada).
- **Download do arquivo original** (sem redução de qualidade) em cada foto, e **seleção de
  várias fotos** para baixar tudo de uma vez em um único `.zip`. No celular o download
  aparece como um **ícone** no card (a opção de RAW fica só no desktop/painel).

**Privacidade**

- A selfie **nunca é armazenada**: é salva em `storage/tmp`, processada, e apagada na mesma requisição.
- Nenhum embedding completo é escrito em log.

---

## 2. Requisitos

| Item | Versão |
| --- | --- |
| Python | **3.10, 3.11 ou 3.12** (recomendado 3.12) |
| GPU | opcional (roda em CPU) |
| Espaço em disco | ~2 GB (dependências + modelo) |

> ⚠️ **Evite o Python 3.13/3.14 por enquanto**: o `onnxruntime` e o `insightface`
> ainda não têm *wheels* prontos para essas versões, o que obrigaria a compilar tudo
> na máquina. Use o 3.12 para a instalação mais tranquila.

---

## 3. Instalação

```bash
# 1) entre na pasta do projeto
cd photo-face-mvp

# 2) crie o ambiente virtual
# Windows:
python -m venv .venv
.venv\Scripts\activate

# Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate

# 3) instale as dependências
pip install -r requirements.txt
```

> O `rawpy` (que traz o LibRaw embutido) vem no `requirements.txt` e é o que garante a
> melhor conversão de arquivos RAW (`.NEF`). Se ele não estiver instalado, o sistema
> **continua aceitando RAW** usando o preview JPEG embutido no arquivo — só com
> qualidade um pouco menor.

### 3.1 InsightFace no Windows (passo importante)

O `insightface` é publicado apenas como **código-fonte** e exige o compilador
Microsoft Visual C++ para ser construído. Sem ele o `pip` falha com:

```
error: Microsoft Visual C++ 14.0 or greater is required.
```

Duas saídas:

**A. Usar o wheel pré-compilado (recomendado, não precisa de compilador)**

```powershell
python -m pip install https://github.com/Gourieff/Assets/raw/main/Insightface/insightface-0.7.3-cp312-cp312-win_amd64.whl
```

> Troque `cp312` por `cp310` / `cp311` conforme a versão do Python escolhida.
> Depois rode normalmente `pip install -r requirements.txt` — o `pip` reconhece que o
> `insightface==0.7.3` já está instalado e não tenta recompilar.

**B. Instalar as Build Tools**
[Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
(componente "Desenvolvimento para desktop com C++") e então rodar `pip install -r requirements.txt`.

Em **Linux/macOS** o `pip install -r requirements.txt` já funciona direto (o wheel é
construído automaticamente; em algumas imagens é preciso `apt install build-essential python3-dev`).

### 3.2 Modelo de reconhecimento facial

Na primeira execução o InsightFace baixa o modelo `buffalo_l` (~280 MB) para
`~/.insightface/models/buffalo_l`. É necessário apenas uma vez e acontece durante a
inicialização — a primeira execução demora um pouco mais por causa do download.

### 3.3 Versões fixadas (por que tanto `==`)

O conjunto de versões do `requirements.txt` foi testado junto. Os pontos sensíveis:

| Pacote | Motivo |
| --- | --- |
| `numpy==1.26.4` | `onnxruntime 1.18` e `opencv 4.10` **não** aceitam NumPy 2.x |
| `albumentations==1.4.19` | fixa `albucore==0.0.19` (o `pip` sozinho pegaria um par incompatível) |
| `contourpy==1.2.1`, `tifffile==2024.8.30` | versões que ainda funcionam com NumPy 1.26 |
| `rawpy==0.27.1` | decodifica RAW via LibRaw (wheel pré-compilado, sem compilador) |
| `pillow-heif==0.22.0` | leitura de HEIC/HEIF (fotos de iPhone) — exige `pillow>=10.1` |
| `insightface==0.7.3` | última versão estável, usa o modelo `buffalo_l` |

---

## 4. Executar

```bash
python app.py
```

Saída esperada:

```
Database initialized
Storage initialized
InsightFace loaded
Server running at http://127.0.0.1:5000
```

Acesse:

| Página | URL |
| --- | --- |
| Início (eventos) | http://localhost:5000 |
| Painel administrativo | http://localhost:5000/admin |
| Página pública do evento | http://localhost:5000/evento/corrida-manaus-2026 |
| Diagnóstico (JSON) | http://localhost:5000/healthz |

### 4.1 Em container (Docker / Linux)

O projeto inclui um `Dockerfile` pronto, baseado em `python:3.12-slim`:

```bash
docker build -t photo-face-mvp .
docker run -d --name photo-face -p 8080:8080 \
  -v "$PWD/data:/app/data" \
  photo-face-mvp
```

Acesse http://localhost:8080 (painel em `/admin`).

O que o `Dockerfile` já resolve:

| Item | Por quê |
| --- | --- |
| `build-essential` | o `insightface` compila um módulo Cython durante o `pip install` (a imagem *slim* não traz compilador) |
| `libgl1` / `libglib2.0-0` | exigidas pelo `opencv-python` **com GUI**. O projeto usa `opencv-python-headless`, que não precisa delas — ficam como rede de segurança |
| `/app/data` (volume) | banco SQLite + fotos. **Monte o volume**, senão tudo se perde ao recriar o container |
| `waitress` | servidor WSGI de produção, em vez do servidor de desenvolvimento do Flask |
| `HEALTHCHECK` | consulta `/healthz` a cada 30s |
| `$PORT` | a aplicação escuta na porta da variável `PORT` (padrão 8080 se ela não existir), que é o que plataformas como o Railway esperam |

> ⚠️ **Comentário dentro de instrução com `\` quebra o build em alguns parsers.** O Docker remove
> linhas `#` antes de juntar as continuações, mas linters e o pre-flight de plataformas de deploy
> param a instrução na linha comentada e reprovam o arquivo (sintoma: *"The Dockerfile failed
> validation"* / `unknown instruction: &&`). Comentários vão **acima** da instrução, nunca no meio.

> ⚠️ **Nunca instale `opencv-python` junto com `opencv-python-headless`**: os dois instalam o
> mesmo pacote `cv2` e um sobrescreve o outro. Era exatamente esse o caso do erro
> `libGL.so.1: cannot open shared object file` em container — o `cv2` que "ganhava" era o que
> precisa de bibliotecas gráficas.

Se o container subir mas o motor facial não carregar, o painel mostra o motivo real
(ex.: `Falha ao carregar o InsightFace (ImportError: libGL.so.1 ...)`) e as fotos ficam como
**aguardando** — corrija a causa e use **Reprocessar pendentes**, sem precisar reenviar nada.

---

## 5. Como usar (roteiro do primeiro teste)

1. `python app.py` e abra http://localhost:5000/admin
2. Clique em **+ Novo evento** → nome `Corrida Manaus 2026` → **Criar evento**.
3. Na tela do evento, **arraste dezenas de fotos** para a área de upload (ou clique em
   "Selecionar arquivos"). O progresso aparece em tempo real e cada foto entra na galeria
   já com a quantidade de rostos detectados.
4. Abra a página pública (link exibido no topo, ou http://localhost:5000/evento/corrida-manaus-2026).
5. Marque o consentimento, escolha uma selfie de alguém que aparece nas fotos.
6. O resultado aparece como *"Encontramos N fotos suas"* + galeria. Clique em uma foto
   para ampliar.

### Não tem fotos à mão?

O projeto inclui um gerador de fotos de teste (usa um retrato de domínio público que
acompanha o `scikit-image`):

```bash
python tools/create_sample_photos.py --count 20   # gera tools/sample/photos/*.jpg + tools/sample/selfie.jpg
```

Envie as fotos de `tools/sample/photos` no evento e use `tools/sample/selfie.jpg` como selfie.

---

## 5.1 Formatos aceitos no upload

| Formato | Como é tratado |
| --- | --- |
| `.jpg` `.jpeg` `.png` `.webp` | Enviado direto para detecção + thumbnail |
| `.heic` `.heif` `.avif` | Fotos de celular: lidas com o `pillow-heif` (já no `requirements.txt`) |
| `.nef` (+ `.nrw .cr2 .cr3 .arw .dng .orf .raf .rw2 .pef .srw` …) | **RAW de câmera**: convertido para JPEG (rawpy/LibRaw) e o **original é preservado** |
| `.zip` | Descompactado e **cada foto vira uma foto do evento** |

> **A extensão não é confiável.** Todo arquivo (foto de evento ou selfie) é validado pelos
> primeiros bytes: um HEIC chamado `.png`, um JPEG sem extensão ou um `.txt` com conteúdo de
> imagem funcionam normalmente. Quando o arquivo não serve, o usuário recebe o motivo exato —
> *"Este arquivo é um ZIP, não uma imagem"*, *"Este arquivo é um RAW de câmera…"*,
> *"O arquivo parece ser um PNG válido, mas está corrompido ou incompleto"* — sempre com HTTP 400,
> nunca com erro 500 ou traceback.

### ZIP com várias fotos

Arraste um `.zip` para a área de upload: o arquivo é descompactado e cada imagem entra
na mesma fila de processamento (uma por vez, com progresso na tela).

O que acontece com o conteúdo:

- fotos em subpastas são aceitas e **achatadas** no nome (`corrida/IMG_01.jpg` → `IMG_01.jpg`);
- entradas inválidas são **ignoradas com motivo** listado na tela
  (`.txt`, `Thumbs.db`, `__MACOSX`, ZIP dentro de ZIP, senha, formato desconhecido);
- nomes com `../` são reduzidos ao *basename* — **nada é escrito fora do storage**;
- limites de segurança (configuráveis): 500 fotos por ZIP, 60 MB por foto, 1 GB
descompactado e razão de compressão máxima de 200:1 (proteção contra *zip bomb*).

### RAW (`.NEF`) — conversão e preservação

Um `.nef` não é uma imagem: é um container TIFF com os dados brutos do sensor.
O sistema resolve isso em duas etapas:

1. **rawpy / LibRaw** (preferido) decodifica o RAW de verdade, aplicando o balanço de
   branco da câmera e gerando um JPEG de alta qualidade (`RAW_MAX_SIDE`, padrão 4000px);
2. se o `rawpy` não estiver instalado ou falhar com o arquivo, o **preview JPEG embutido**
   no próprio RAW é extraído por varredura de bytes (todo NEF tem um, geralmente em
   resolução total). Nada de dependência extra — funciona sempre.

O JPEG gerado passa a ser a imagem exibida (galeria, lightbox e detecção de rostos) e o
**`.nef` original fica guardado** ao lado, com os botões `⤓ Original` (o JPEG em tamanho cheio)
e `⤓ RAW` (o `.nef` de verdade) na galeria do painel e a etiqueta `RAW` no card. A busca
pública entrega o **JPEG em tamanho cheio** — quem quer o RAW usa o painel.
Ao excluir a foto, os dois arquivos são removidos.

---

## 6. Testes automatizados

```bash
# 1) Pipeline completo sem servidor web (banco + storage + rostos + busca)
python tools/smoke_test.py

# 2) Fluxo HTTP completo (com o servidor rodando em outro terminal)
python app.py
python tools/http_smoke_test.py          # use --keep para manter o evento criado

# 3) ZIP com várias fotos + RAW (.NEF) — inclui testes de segurança
python tools/zip_raw_test.py

# 4) Selfie em vários formatos (HEIC, extensão errada, arquivo corrompido…)
python tools/selfie_formats_test.py

# 5) Download dos originais (uma foto, várias em ZIP, RAW, erros amigáveis)
python tools/download_test.py
```

O segundo script executa exatamente o critério de aceite do MVP: cria o evento,
envia as fotos em lote, abre a página pública, envia a selfie, confere os resultados e
exclui uma foto. O terceiro monta um ZIP "sujo" (subpasta, `.txt`, ZIP dentro de ZIP,
caminho `../`, metadados do macOS e uma *zip bomb*) e um NEF sintético, e valida que
nada disso escapa do storage. O quarto envia a mesma selfie como JPEG, HEIC, arquivo
sem extensão e com extensão trocada, além de lixo, ZIP, PDF e vídeo — todas as respostas
devem ser coerentes (busca funciona ou mensagem clara). O quinto usa o *test client* do Flask
com banco e storage temporários (não toca nos seus dados) para provar que o download entrega
o **arquivo original byte a byte** (e não o thumbnail), que a seleção vira ZIP com os originais
intactos e que os erros viram mensagens amigáveis.

---

## 7. Estrutura do projeto

```
photo-face-mvp/
├── app.py                      # Flask: rotas, upload, busca, tratamento de erros
├── config.py                   # toda a configuração (paths, limites, threshold)
├── requirements.txt
├── Dockerfile                  # imagem Linux pronta (headless + waitress)
├── README.md
├── database.db                 # criado automaticamente na 1ª execução
├── services/
│   ├── database_service.py     # conexão SQLite + schema + helpers
│   ├── event_service.py        # eventos (CRUD, slug, estatísticas)
│   ├── photo_service.py        # fotos e rostos/embeddings (BLOB)
│   ├── image_service.py        # validação, gravação, thumbnails, leitura para CV
│   ├── raw_service.py          # RAW de câmera (rawpy/LibRaw + preview embutido)
│   ├── archive_service.py      # extração segura de ZIP (limites + anti zip bomb)
│   ├── download_service.py     # download dos originais (1 foto ou várias em ZIP)
│   ├── face_service.py         # InsightFace (modelo carregado 1x, singleton)
│   └── search_service.py       # busca por similaridade de cosseno (backend trocável)
├── templates/
│   ├── base.html  index.html  admin.html  admin_event.html
│   ├── event.html  results.html  error.html
├── static/
│   ├── css/style.css
│   └── js/app.js
├── storage/
│   ├── events/<event_id>/originals/
│   ├── events/<event_id>/thumbnails/
│   └── tmp/                    # selfies temporárias (apagadas na mesma requisição)
└── tools/
    ├── create_sample_photos.py # gera fotos de exemplo
    ├── smoke_test.py           # teste do pipeline de reconhecimento
    ├── http_smoke_test.py      # teste do fluxo HTTP completo
    ├── zip_raw_test.py         # teste de ZIP com várias fotos e de RAW (.NEF)
    ├── selfie_formats_test.py  # teste de formatos de selfie (HEIC, corrompido, ZIP…)
    └── download_test.py        # teste do download dos originais (1 foto e ZIP)
```

---

## 8. Banco de dados (SQLite)

```sql
events  (id, name, slug, description, event_date, cover_path, created_at)

photos  (id, event_id, filename, original_path, thumbnail_path, raw_path,
         status, error_message, faces_count, created_at)

faces   (id, photo_id, event_id, embedding BLOB, dim, bbox, confidence, created_at)
```

- `embedding` é gravado como **BLOB float32** (`numpy.ndarray.tobytes()`), com a dimensão
  em `dim` (512 no `buffalo_l`) e lido com `numpy.frombuffer()`.
- `bbox` é um JSON `[x1, y1, x2, y2]`.
- `raw_path` só é preenchido quando a foto veio de um RAW (ex.: `.nef`); ele aponta para
  o arquivo original preservado, que é apagado junto com a foto.
- `status` da foto: `pending`, `processed`, `no_faces`, `error`.
- Chaves estrangeiras com `ON DELETE CASCADE`; índices por evento, status e foto.
- `original_path` / `thumbnail_path` são **relativos ao storage** (`events/3/originals/abc.jpg`),
  então mover a pasta do projeto não quebra os registros.

---

## 9. Configuração (`config.py`)

Tudo pode ser sobrescrito por variável de ambiente (útil para produção):

| Config | Padrão | Descrição |
| --- | --- | --- |
| `SECRET_KEY` | `photo-face-mvp-dev-secret-change-me` | **troque em produção** |
| `DEBUG` | `True` | em `True` mostra a similaridade (%) nos resultados |
| `HOST` / `PORT` | `0.0.0.0` / `5000` | endereço do servidor |
| `DATABASE_PATH` | `./database.db` | arquivo SQLite |
| `STORAGE_FOLDER` | `./storage` | originais, thumbnails e temporários |
| `MAX_CONTENT_LENGTH` | 256 MB | limite por requisição (um ZIP grande precisa de folga) |
| `ALLOWED_EXTENSIONS` | `jpg,jpeg,png,webp,heic,heif,avif` | imagens comuns aceitas |
| `RAW_EXTENSIONS` | `nef,nrw,cr2,cr3,arw,dng,orf,raf,rw2,pef,srw,…` | RAW aceitos |
| `ARCHIVE_EXTENSIONS` | `zip` | arquivos compactados aceitos |
| `ALLOW_ARCHIVE_UPLOAD` | `True` | habilita/desabilita upload de ZIP |
| `MAX_ZIP_ENTRIES` | `500` | máximo de fotos por ZIP |
| `MAX_ZIP_ENTRY_SIZE` | 60 MB | tamanho máximo por foto dentro do ZIP |
| `MAX_ZIP_TOTAL_SIZE` | 1 GB | total descompactado por ZIP |
| `MAX_ZIP_COMPRESSION_RATIO` | `200` | anti *zip bomb* (razão descompactado/compactado) |
| `RAW_JPEG_QUALITY` | `92` | qualidade do JPEG gerado a partir do RAW |
| `RAW_MAX_SIDE` | `4000` | maior lado do JPEG gerado (`0` = resolução original) |
| `RAW_PREVIEW_FALLBACK` | `True` | usar o preview embutido se o rawpy falhar |
| `THUMBNAIL_WIDTH` | `500` | largura máxima da miniatura (proporção preservada) |
| `MAX_DETECTION_SIDE` | `2000` | redimensiona imagens maiores antes de detectar |
| `INSIGHTFACE_MODEL` | `buffalo_l` | modelo do InsightFace |
| `INSIGHTFACE_PROVIDERS` | `CUDAExecutionProvider,CPUExecutionProvider` | usa CPU automaticamente se não houver GPU |
| `FACE_MIN_DETECTION_SCORE` | `0.55` | confiança mínima do detector |
| **`FACE_SIMILARITY_THRESHOLD`** | **`0.45`** | similaridade mínima para considerar "a mesma pessoa" |
| `MAX_SEARCH_RESULTS` | `300` | máximo de fotos devolvidas por busca |
| `MAX_SELFIE_LENGTH` | 12 MB | limite do arquivo da selfie |
| `MAX_DOWNLOAD_PHOTOS` | `200` | máximo de fotos em um download (ZIP) |
| `MAX_DOWNLOAD_BYTES` | 2 GB | tamanho total máximo dos arquivos de um download |

Exemplo:

```bash
# Linux/macOS
FACE_SIMILARITY_THRESHOLD=0.38 DEBUG=false python app.py

# Windows PowerShell
$env:FACE_SIMILARITY_THRESHOLD="0.38"; $env:DEBUG="false"; python app.py
```

**Ajuste do threshold** (buffalo_l + cosine):

| Valor | Efeito |
| --- | --- |
| `0.50` | bem conservador — poucos falsos positivos, pode perder fotos difíceis |
| `0.45` | padrão equilibrado |
| `0.35` | permissivo — encontra mais fotos, aumenta o risco de misturar pessoas |
| `< 0.30` | não recomendado (começa a confundir pessoas diferentes) |

---

## 10. Rotas

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/` | página inicial com os eventos publicados |
| GET | `/admin` | painel: lista de eventos + criar evento |
| POST | `/admin/event/create` | cria o evento (slug automático) |
| GET | `/admin/event/<id>` | painel do evento (estatísticas, upload, galeria) |
| POST | `/admin/event/<id>/upload` | upload em lote (JSON quando `X-Requested-With: XMLHttpRequest`) |
| POST | `/admin/photo/<id>/delete` | exclui a foto e seus arquivos |
| POST | `/admin/photo/<id>/reprocess` | reprocessa a detecção de rostos |
| POST | `/admin/event/<id>/delete` | exclui o evento e todas as fotos |
| GET | `/evento/<slug>` | página pública do evento |
| POST | `/evento/<slug>/search` | busca pela selfie (JSON ou HTML) |
| GET | `/evento/<slug>/foto/<id>/baixar` | baixa o **arquivo original** da foto (`?raw=1` baixa o RAW preservado) |
| POST | `/evento/<slug>/baixar` | baixa as fotos marcadas (`ids`): 1 vira arquivo, 2+ viram `.zip` |
| GET | `/storage/<path>` | serve originais e thumbnails |
| GET | `/healthz` | status do banco, do motor facial e do threshold |

---

## 11. Como o reconhecimento funciona

1. **Detecção + embedding**: `insightface.app.FaceAnalysis(name="buffalo_l")`, com os módulos
   `detection` e `recognition` (os demais são ignorados para não gastar memória/CPU).
   O modelo é carregado **uma única vez**, no start da aplicação (`services/face_service.py`),
   dentro de um `threading.Lock` (o servidor Flask é multithread).
2. **Pré-processamento**: orientação EXIF aplicada, conversão para RGB/BGR e redução para no
   máximo `MAX_DETECTION_SIDE` pixels — evita detectar em fotos de 6000px.
3. **Embedding**: vetor de 512 dimensões float32 (`normed_embedding`, já normalizado).
4. **Comparação**: similaridade de cosseno

   $$\text{sim}(a,b) = \frac{a \cdot b}{\lVert a \rVert \, \lVert b \rVert}$$

   implementada em `services/search_service.py` e calculada de forma vetorizada
   (matriz `N x 512` × vetor da selfie) com NumPy.
5. **Deduplicação**: se a mesma foto tiver vários rostos compatíveis, ela entra **uma única vez**,
   com a maior similaridade.
6. **Ordenação**: resultados do maior para o menor score, cortados em `MAX_SEARCH_RESULTS`.
7. **Threshold**: só entram resultados com `sim >= FACE_SIMILARITY_THRESHOLD`.

### Trocando o mecanismo de busca no futuro

`FaceSearchService` recebe um *backend* que implementa `search(event_id, embedding, threshold, limit)`.
Hoje o backend é `NumpySearchBackend` (carrega os embeddings do evento em memória).
Para PostgreSQL + pgvector, FAISS ou outro índice, basta escrever outra classe com o mesmo
método e injetá-la — nenhuma rota precisa mudar.

---

## 12. Performance e limites (MVP)

- O upload é processado **uma foto por vez**, com progresso na interface (sem Celery/Redis).
- Cerca de **0,3–0,6 s por foto** em CPU moderna (imagem de ~1000px, 1 rosto).
- A busca compara a selfie com todos os embeddings do evento: ~15 ms para 10 rostos e
  ~50 ms para 1000 rostos.
- Recomendado para eventos de até ~50 mil rostos. Acima disso, migre para um índice vetorial.
- As miniaturas (500px) são o que a galeria carrega — os originais só aparecem no lightbox.

---

## 13. Segurança e privacidade

- Nome físico de arquivo sempre **UUID** (`uuid4().hex.ext`); o nome enviado pelo usuário é
  apenas exibido, nunca usado no disco.
- `secure_filename` em todo nome recebido + validação de extensão **e** de MIME.
- A imagem é validada com o Pillow (formato real), não apenas pela extensão.
- `/storage/<path>` usa `send_from_directory`, que bloqueia path traversal
  (`/storage/../../app.py` → 404, verificado nos testes).
- O download só aceita ids de fotos **do evento da URL**; um id de outro evento é ignorado
  (não confirma nem nega a existência da foto). O ZIP é montado em `storage/tmp` com
  `ZIP_STORED` (nenhuma recompressão: os bytes saem idênticos ao original) e apagado logo
  depois do envio, com faxina de sobras antigas a cada novo download.
- Limite de tamanho por requisição (`MAX_CONTENT_LENGTH`) e por selfie (`MAX_SELFIE_LENGTH`).
- Erros devolvem mensagens amigáveis ("Não foi possível processar esta imagem.") — nunca
  traceback para o usuário (o traceback fica no log).
- Selfie apagada ao final da busca (`finally`), inclusive em caso de erro.
- LGPD: a página pública exige consentimento explícito antes de habilitar o envio.

---

## 14. Solução de problemas

| Sintoma | Causa / solução |
| --- | --- |
| `error: Microsoft Visual C++ 14.0 or greater is required` | Use o wheel pré-compilado do InsightFace (seção 3.1) |
| `ImportError: cannot import name 'preserve_channel_dim' from 'albucore.utils'` | Versões desalinhadas de `albumentations`/`albucore` — rode `pip install -r requirements.txt` (que fixa `albumentations==1.4.19` e `albucore==0.0.19`) |
| `onnxruntime ... requires numpy<2.0, but you have numpy 2.x` | `pip install numpy==1.26.4` (já previsto no `requirements.txt`) |
| `No module named 'albumentations'` / `'matplotlib'` | Dependências do InsightFace não instaladas: `pip install -r requirements.txt` |
| Demora muito na primeira execução | Download do modelo `buffalo_l` (~280 MB) em `~/.insightface/models` |
| `/healthz` mostra `"face_engine": false` | Veja `face_error` no próprio `/healthz`; normalmente é dependência faltando |
| `libGL.so.1: cannot open shared object file` (container Linux) | O `cv2` instalado é o `opencv-python` (com GUI). Instale `opencv-python-headless` e remova o completo — `pip uninstall -y opencv-python && pip install opencv-python-headless==4.10.0.84`. Alternativa: `apt-get install -y libgl1 libglib2.0-0` |
| Fotos ficaram “Aguardando” e ninguém foi detectado | O motor facial estava indisponível no momento do upload (dependência faltando). Corrija e clique em **Reprocessar N pendentes** no painel do evento — os originais continuam salvos |
| NEF/RAW dá "Não foi possível ler este arquivo RAW" | O arquivo está corrompido/truncado ou o formato não é suportado pelo LibRaw. Veja `raw_support` em `/healthz` |
| `rawpy` aparece como `None` em `/healthz` | O modo *fallback* (preview JPEG embutido) assume. Para o melhor resultado: `pip install rawpy` |
| ZIP inteiro é ignorado no upload | Alguma entrada estourou os limites (`MAX_ZIP_*`); a razão exata aparece na lista de avisos da tela |
| Upload de ZIP grande retorna 413 | Aumente `MAX_CONTENT_LENGTH` (padrão 256 MB) |
| Selfie de iPhone (HEIC) não funciona | Instale o plugin: `pip install pillow-heif` (já previsto no `requirements.txt`); sem ele a mensagem na tela explica exatamente isso |
| "cannot identify image file ... tmpXXXX.png" no log | Arquivo renomeado ou corrompido. Agora o app responde 400 com o motivo; veja a mensagem na tela e o aviso no log (`Arquivo não é imagem (detectado: ...)`) |
| Muitas fotos com "sem rostos" | Fotos de costas, rosto muito pequeno ou escuro. Baixe `FACE_MIN_DETECTION_SCORE` (ex.: `0.4`) |
| Resultados faltando / sobrando | Ajuste `FACE_SIMILARITY_THRESHOLD`: menor = mais resultados |
| `ModuleNotFoundError: No module named 'app'` | Execute a partir da pasta `photo-face-mvp` (onde está o `app.py`) |
| Porta 5000 ocupada | `PORT=5001 python app.py` (Windows: `$env:PORT="5001"; python app.py`) |
| `database is locked` | WAL está ativo e as conexões são curtas; garanta que não há outro processo escrevendo no `.db` |

---

## 15. O que **não** faz parte deste MVP

Login de usuários, pagamentos, PostgreSQL, Redis/Celery/RabbitMQ, Kubernetes, AWS,
pgvector/FAISS, moderação de conteúdo e armazenamento em nuvem. O foco é rodar localmente
(ou em um único container — há um `Dockerfile` pronto) com o mínimo de infraestrutura.

### Próximos passos naturais

1. **Índice vetorial** (pgvector/FAISS) via `FaceSearchService.backend`.
2. **Login** para o painel administrativo e isolamento por organizador.
3. **Celery/RQ** para processar uploads muito grandes em background.
4. **Detecção de múltiplos rostos por selfie** com escolha manual do rosto.
5. **Retenção automática**: apagar fotos e embeddings após N dias do evento.
6. **Servidor de produção**: `waitress`/`gunicorn` + nginx (o `app.run` é de desenvolvimento).

---

## 16. Licença e créditos

Projeto de exemplo/MVP. O reconhecimento facial usa o
[InsightFace](https://github.com/deepinsight/insightface) com o modelo `buffalo_l`
(uso não comercial conforme a licença do projeto original — verifique antes de usar em produção).
