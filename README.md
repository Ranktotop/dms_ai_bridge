# dms_ai_bridge

Intelligent middleware between Document Management Systems (e.g.
[Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)) and AI frontends such as
[Open WebUI](https://github.com/open-webui/open-webui) or
[AnythingLLM](https://github.com/Mintplex-Labs/anything-llm).

The bridge indexes documents from a DMS into a [Qdrant](https://qdrant.tech/) vector database
using [Ollama](https://ollama.com/) embeddings. A separate ingestion pipeline converts and
uploads files from a local inbox into Paperless-ngx. A FastAPI server exposes structured tool
endpoints so AI frontends (OpenWebUI, AnythingLLM) can call document search, filter options,
and document detail retrieval directly — the frontend LLM handles orchestration and synthesis.
A webhook listener keeps the index in sync whenever a document is added or updated.

---

## Architecture

```
File Inbox
  │
  ▼
IngestionService ──► DocumentConverter (LibreOffice) ──► Document (OCR + LLM metadata)
  │                                                           │
  ▼                                                           ▼
DMSClientInterface.do_upload_document()            OCRClientInterface (Docling)
  │                                                LLMClientInterface (Vision + Chat)
  ▼
DMS (Paperless-ngx)
  │
  ▼
DMSClientInterface ──► fill_cache() ──► DocumentHighDetails[]
  │
  ▼
SyncService ──► chunk() ──► LLMClientInterface.do_embed() ──► vectors[]
  │                                          │
  ▼                                          ▼
RAGClientInterface.do_upsert_points()   Ollama /api/embed
  │
  ▼
Qdrant (vector store, owner_id-filtered)
  ▲
  │
FastAPI (POST /tools/{frontend}/{tool_name})   ← Phase IV BridgeAPI
  │── UserMappingService.resolve(frontend, user_id, engine) → owner_id
  │── SearchService.do_search()              ← search_documents
  │── SearchService.do_get_filter_options()  ← list_filter_options
  └── SearchService.do_fetch_full_by_doc_id() ← get_document_details / get_document_full
```

Interface hierarchy (generic → concrete):

```
ClientInterface
  ├── DMSClientInterface  ──► DMSClientPaperless
  ├── RAGClientInterface  ──► RAGClientQdrant
  ├── LLMClientInterface  ──► LLMClientOllama
  ├── CacheClientInterface ─► CacheClientRedis
  └── OCRClientInterface  ──► OCRClientDocling
```

All backends are loaded via reflection-based factories — new implementations satisfy the
relevant interface and are picked up automatically by setting the appropriate env var.

---

## Implementation Status

| Phase | Scope | Status |
|---|---|---|
| I | Shared infrastructure, DMS client, RAG client, SyncService | Complete |
| II | LLM client — embedding + chat via Ollama | Complete |
| II+ | Cache client (Redis), OCR client (Docling), Document ingestion pipeline | Complete |
| III | FastAPI server — `POST /webhook/{engine}/document` + `POST /query/{frontend}` | Complete |
| IV | BridgeAPI — `POST /tools/{frontend}/{tool_name}` direct tool endpoints | Complete |

### Phase I — Infrastructure (complete)

- `ClientInterface` — base ABC for all HTTP clients (`boot`/`close`/`do_request`/`do_healthcheck`)
- `DMSClientInterface` (ABC) + `DMSClientPaperless` — Paperless-ngx REST client
  - `fill_cache()` — paginated fetch of all documents with fully resolved metadata
  - `DocumentHighDetails` — canonical output model with resolved correspondent, tags, type, owner
- `RAGClientInterface` (ABC) + `RAGClientQdrant` — Qdrant REST client via httpx
  - Deterministic point IDs (`uuid5(engine:doc_id:chunk_index)`)
  - `do_upsert_points()`, `do_fetch_points()`, `do_search_points()`, `do_count()`,
    `do_delete_points_by_filter()`, `do_create_collection()`
- `SyncService` — full sync (all documents) + incremental sync (single document)
  - Text chunking: character-level, `CHUNK_SIZE=1000`, `CHUNK_OVERLAP=100`
  - `asyncio.Semaphore(DOC_CONCURRENCY=5)` — bounded concurrency
  - Upsert batches of 100 — Qdrant-safe payload sizes
  - Orphan cleanup after full sync
- `HelperConfig` — all configuration via environment variables, never `os.getenv()` in business logic
- `owner_id` enforced as a security invariant on every Qdrant upsert and every search filter

### Phase II — LLM Client (complete)

- `LLMClientInterface` (ABC) — unified interface for embedding and chat/completion
  - `do_embed(texts)` → `list[list[float]]` — always returns a list, even for a single string
  - `do_fetch_embedding_vector_size()` → `(dimension, distance_metric)`
  - `do_chat(messages)` → `str` — OpenAI-format message dicts
  - `do_chat_vision(image_bytes, prompt)` → `str` — Vision LLM call
- `LLMClientOllama` — implements all abstract hooks for Ollama
  - Embedding: `POST /api/embed`
  - Chat: `POST /api/chat` with `"stream": false`
  - Vector size discovery via `POST /api/show`

### Phase II+ — Cache, OCR, Ingestion (complete)

- `CacheClientInterface` (ABC) + `CacheClientRedis` — cross-process cache via Redis
  - `do_get_json` / `do_set_json` for structured data
  - `do_delete_pattern()` for namespace-level invalidation
- `OCRClientInterface` (ABC) + `OCRClientDocling` — PDF-to-Markdown conversion
  - `do_convert_pdf_to_markdown(file_bytes, filename)` → `str`
  - Sends multipart request to Docling `POST /v1/convert/file`
- Document ingestion pipeline (`services/doc_ingestion/`)
  - `FileScanner` — rglob + watchfiles file discovery
  - `DocumentConverter` — LibreOffice headless PDF conversion
  - `Document` — path template parsing, OCR, Vision LLM OCR fallback, LLM metadata + tag extraction
  - `IngestionService` — orchestrates boot → upload → PATCH → cleanup

### Phase III — FastAPI Server (complete)

- `POST /webhook/{engine}/document` — fire-and-forget incremental sync via `BackgroundTasks`
- `POST /query/{frontend}` — embed query → `do_search_points()` with `owner_id` filter → `SearchResponse`
- `UserMappingService` — resolves `(frontend, user_id)` → `owner_id` via `config/user_mapping.yml`
- `X-API-Key` authentication on all endpoints
- `GET /health` — shallow health check (no auth); `GET /health/deep` — probes all backends

### Phase IV — BridgeAPI (complete)

- `POST /tools/{frontend}/{tool_name}` — direct tool endpoints for AI frontend integrations
- Four tools: `search_documents`, `list_filter_options`, `get_document_details`, `get_document_full`
- Each endpoint performs exactly one operation; the frontend LLM handles orchestration
- Results are merged per document (chunks from the same document are concatenated)
- Paginated full-text retrieval via `get_document_full` with `start_char` / `next_start_char`

---

## Project Structure

```
dms_ai_bridge/
├── CLAUDE.md                            # Architecture reference and coding conventions
├── .env / .env.example
├── requirements.txt
├── start.sh                             # Uvicorn launcher (Phase III)
├── shared/
│   ├── clients/
│   │   ├── ClientInterface.py           # Base ABC (lifecycle, auth, do_request)
│   │   ├── dms/
│   │   │   ├── DMSClientInterface.py    # DMS ABC (fill_cache, write methods)
│   │   │   ├── DMSClientManager.py      # Factory
│   │   │   ├── models/                  # Document, Correspondent, Tag, Owner, DocumentType
│   │   │   └── paperless/
│   │   │       └── DMSClientPaperless.py
│   │   ├── llm/
│   │   │   ├── LLMClientInterface.py    # Unified ABC (embed + chat + vision)
│   │   │   ├── LLMClientManager.py      # Factory
│   │   │   └── ollama/
│   │   │       └── LLMClientOllama.py
│   │   ├── rag/
│   │   │   ├── RAGClientInterface.py    # RAG ABC
│   │   │   ├── RAGClientManager.py      # Factory
│   │   │   ├── models/                  # Point.py (request + response models)
│   │   │   └── qdrant/
│   │   │       └── RAGClientQdrant.py
│   │   ├── cache/
│   │   │   ├── CacheClientInterface.py  # Cache ABC (get/set/delete/delete_pattern)
│   │   │   ├── CacheClientManager.py    # Factory
│   │   │   └── redis/
│   │   │       └── CacheClientRedis.py
│   │   └── ocr/
│   │       ├── OCRClientInterface.py    # OCR ABC (do_convert_pdf_to_markdown)
│   │       ├── OCRClientManager.py      # Factory
│   │       └── docling/
│   │           └── OCRClientDocling.py
│   ├── helper/
│   │   ├── HelperConfig.py              # Central env var reader
│   │   └── HelperFile.py               # File system helpers
│   ├── logging/
│   │   └── logging_setup.py             # setup_logging(), ColorLogger
│   └── models/
│       └── config.py                    # EnvConfig Pydantic model
├── services/
│   ├── doc_ingestion/
│   │   ├── IngestionService.py          # Orchestrator (boot → upload → PATCH)
│   │   ├── doc_ingestion.py             # Entry point (python -m services.doc_ingestion)
│   │   └── helper/
│   │       ├── Document.py              # Central document class (convert, OCR, metadata, tags)
│   │       ├── DocumentConverter.py     # LibreOffice PDF conversion helper
│   │       └── FileScanner.py           # rglob + watchfiles file discovery
│   ├── dms_rag_sync/
│   │   ├── SyncService.py               # DMS → embed → RAG orchestration
│   │   └── dms_rag_sync.py              # Entry point (python -m services.dms_rag_sync)
│   └── rag_search/
│       ├── SearchService.py             # embed → search → list[PointHighDetails]
│       └── helper/
│           └── IdentityHelper.py        # Resolves frontend user_id → owner_id map
├── config/
│   └── user_mapping.yml                 # frontend/user_id → DMS owner_id mapping
└── server/                              # Phase III/IV
    ├── api_server.py                    # FastAPI entry point with lifespan
    ├── routers/
    │   ├── WebhookRouter.py             # POST /webhook/{engine}/document
    │   ├── QueryRouter.py               # POST /query/{frontend}
    │   └── ToolsRouter.py               # POST /tools/{frontend}/{tool_name} (BridgeAPI)
    ├── dependencies/
    │   ├── auth.py                      # X-API-Key verification
    │   ├── services.py                  # FastAPI Depends helpers
    │   └── identity.py                  # get_verified_identity_helper()
    ├── models/
    │   ├── requests.py                  # WebhookRequest, SearchRequest, Tool*Request
    │   └── responses.py                 # SearchResultItem, SearchResponse, Tool*Response
    └── user_mapping/
        ├── UserMappingService.py        # resolve(frontend, user_id, engine) → owner_id
        └── models.py                    # UserMapping Pydantic models
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values.

### General

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `info` | Logging level (`debug`, `info`, `warning`, `error`) |
| `APP_API_KEY` | — | Secret key required in `X-API-Key` header |
| `LANGUAGE` | `German` | Language for LLM-extracted text (metadata, tags) |

### DMS

| Variable | Default | Description |
|---|---|---|
| `DMS_ENGINES` | — | Comma-separated list of DMS backends, e.g. `[paperless]` |
| `DMS_TIMEOUT` | `30` | HTTP timeout in seconds |
| `DMS_PAPERLESS_BASE_URL` | — | Paperless-ngx base URL |
| `DMS_PAPERLESS_API_KEY` | — | Paperless-ngx API token |

### RAG (Qdrant)

| Variable | Default | Description |
|---|---|---|
| `RAG_ENGINES` | — | Comma-separated list of RAG backends, e.g. `[qdrant]` |
| `RAG_TIMEOUT` | `30` | HTTP timeout in seconds |
| `RAG_QDRANT_BASE_URL` | — | Qdrant REST API base URL |
| `RAG_QDRANT_COLLECTION` | — | Qdrant collection name |
| `RAG_QDRANT_API_KEY` | — | Qdrant API key (optional) |

### LLM (Ollama)

| Variable | Default | Description |
|---|---|---|
| `LLM_ENGINE` | — | LLM backend, e.g. `ollama` |
| `LLM_TIMEOUT` | `600` | HTTP timeout in seconds |
| `LLM_OLLAMA_BASE_URL` | — | Ollama base URL |
| `LLM_OLLAMA_API_KEY` | — | Ollama Bearer token (optional) |
| `LLM_MODEL_EMBEDDING` | — | Embedding model name (e.g. `nomic-embed-text`) |
| `LLM_MODEL_EMBEDDING_MAX_CHARS` | — | Max characters per chunk |
| `LLM_MODEL_CHAT` | — | Chat model (e.g. `llama3.2`); falls back to `LLM_MODEL_EMBEDDING` |
| `LLM_MODEL_VISION` | — | Vision model for image-based OCR (e.g. `llava`) |
| `LLM_DISTANCE` | `Cosine` | Qdrant distance metric (`Cosine`, `Dot`, `Euclid`) |

### Cache (Redis)

| Variable | Default | Description |
|---|---|---|
| `CACHE_ENGINE` | — | Cache backend, e.g. `redis` |
| `CACHE_REDIS_BASE_URL` | — | Redis URL (e.g. `redis://localhost:6379`) |
| `CACHE_REDIS_PASSWORD` | — | Redis password (optional) |
| `CACHE_REDIS_DB` | `0` | Redis database index |
| `CACHE_DEFAULT_TTL_SECONDS` | `86400` | Safety-net TTL for cached values |

### OCR (Docling)

| Variable | Default | Description |
|---|---|---|
| `OCR_ENGINE` | — | OCR backend, e.g. `docling` |
| `OCR_TIMEOUT` | `300` | HTTP timeout in seconds (OCR is slow) |
| `OCR_DOCLING_BASE_URL` | — | Docling server base URL |
| `OCR_DOCLING_API_KEY` | — | Docling API key (optional) |

### Document Ingestion

| Variable | Default | Description |
|---|---|---|
| `DOC_INGESTION_SKIP_OCR_READ` | `false` | Skip PyMuPDF direct read, always use OCR |
| `DOC_INGESTION_MINIMUM_TEXT_CHARS_FOR_DIRECT_READ` | `40` | Min chars per page before Vision LLM fallback |
| `DOC_INGESTION_PAGE_DPI` | `150` | DPI for Vision LLM page rendering |
| `DOC_INGESTION_VISION_CONTEXT_CHARS` | `300` | Chars of preceding text passed as context to Vision LLM |

---

## Running

### Document ingestion (file inbox → Paperless-ngx)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in .env

python -m services.doc_ingestion
```

### Full DMS → RAG sync (one-shot)

```bash
python -m services.dms_rag_sync
```

Expected log output:

```
INFO  Starting full sync...
INFO  Fetching documents from Paperless-ngx...
DEBUG Syncing document 42 ('Invoice ACME 2024'): 3 chunk(s).
...
INFO  Full sync complete.
```

### API server

```bash
bash start.sh
# or directly:
uvicorn server.api_server:app --host 0.0.0.0 --port 8000
```

### With Docker Compose

```bash
cp .env.example .env
# edit .env with your settings
docker compose -f .docker/docker-compose.yml up -d
```

---

## API Endpoints

### `GET /health`

Shallow health check — no authentication required.

**Response**
```json
{ "status": "ok" }
```

### `GET /health/deep`

Deep health check — probes all configured backends. No authentication required.

**Response**
```json
{ "status": "ok", "backends": { "paperless": "ok", "qdrant": "ok", "ollama": "ok", "redis": "ok" } }
```

---

All endpoints below require the `X-API-Key` header matching `APP_API_KEY`.

### `POST /webhook/{engine}/document`

Called by Paperless-ngx after a document is added or updated. Triggers an incremental sync
as a background task and returns immediately.

**Request**
```json
{ "document_id": 42 }
```

**Response**
```json
{ "status": "accepted", "document_id": 42 }
```

### `POST /query/{frontend}`

Semantic vector search against the Qdrant index with LLM-based query classification.

**Request**
```json
{ "query": "Invoice from ACME 2024", "user_id": "5", "limit": 5 }
```

**Response**
```json
{
  "query": "Invoice from ACME 2024",
  "results": [
    { "dms_doc_id": "42", "title": "Invoice ACME", "score": 0.91, "chunk_text": "..." }
  ],
  "total": 1
}
```

### `POST /tools/{frontend}/search_documents`

Semantic search — returns merged results (one entry per document).

**Request**
```json
{ "user_id": "5", "query": "Invoice from ACME 2024", "limit": 5 }
```

**Response**
```json
{
  "results": [
    {
      "dms_doc_id": "42", "title": "Invoice ACME", "score": 0.91,
      "content": "...", "created": "2024-01-15",
      "correspondent": "ACME GmbH", "document_type": "Invoice",
      "tags": ["2024"], "view_url": "https://paperless/documents/42/"
    }
  ]
}
```

### `POST /tools/{frontend}/list_filter_options`

Returns available filter values for the authenticated user.

**Request**
```json
{ "user_id": "5" }
```

**Response**
```json
{ "correspondents": ["ACME GmbH", "Bank AG"], "document_types": ["Invoice"], "tags": ["2024"] }
```

### `POST /tools/{frontend}/get_document_details`

Returns full metadata and content for a specific document.

**Request**
```json
{ "user_id": "5", "document_id": "42" }
```

**Response**
```json
[{ "dms_doc_id": "42", "title": "Invoice ACME", "content": "...", "view_url": "..." }]
```

### `POST /tools/{frontend}/get_document_full`

Returns paginated full text of a document (4000 chars per page).

**Request**
```json
{ "user_id": "5", "document_id": "42", "start_char": 0 }
```

**Response**
```json
{ "content": "...", "total_length": 12500, "next_start_char": 4000 }
```

Call again with `start_char: 4000` to read the next page. `next_start_char` is `null` on the last page.

`user_id` is resolved to an internal `owner_id` via `UserMappingService` before any search
is performed. Unknown users receive HTTP 403 — no fallback default owner.

---

## Integrations

Pre-built connectors are available in the `integrations/` directory.

### OpenWebUI (`integrations/openwebui/tool.py`)

A native OpenWebUI tool set that exposes four tools to any model: `search_documents`,
`list_filter_options`, `get_document_details`, and `get_document_full`. The model decides
which tool to call and synthesises the answer — no agent loop runs on the bridge.

1. Deploy `integrations/openwebui/tool.py` under Admin → Tools in OpenWebUI.
2. Attach the tool set to any model and configure the Valves:
   - `BASE_URL` — dms_ai_bridge server URL (default: `http://dms-bridge:8000`)
   - `API_KEY` — matches `APP_API_KEY` in your `.env`
   - `LIMIT` — max search results (default: 5)

OpenWebUI automatically passes the logged-in user's ID via `__user__["id"]` — no manual `USER_ID` configuration required.

### AnythingLLM (`integrations/anythingllm/dms-bridge-skill/`)

An agent skill that gives AnythingLLM document search capabilities via
`POST /tools/anythingllm/search_documents`.

1. Copy the `integrations/anythingllm/dms-bridge-skill/` folder to the AnythingLLM custom skills directory (contains `handler.js`, `plugin.json`, `package.json`).
2. Enable the "DMS Document Search" skill in Agent Settings and configure:
   - `DMS_BRIDGE_URL` — dms_ai_bridge server URL (default: `http://dms-bridge:8000`)
   - `DMS_BRIDGE_API_KEY` — matches `APP_API_KEY` in your `.env`
   - `DMS_BRIDGE_USER_ID` — AnythingLLM user ID mapped in `config/user_mapping.yml`
   - `DMS_BRIDGE_LIMIT` — max search results (default: 5)

---

## Security

- Every request must carry the `X-API-Key` header matching `APP_API_KEY`.
- `owner_id` is enforced unconditionally on every Qdrant upsert and every search filter.
- Documents without `owner_id` are skipped at sync time — no silent writes to Qdrant.
- `user_id` from AI frontends is never passed directly to search — always resolved through
  `UserMappingService` first.
