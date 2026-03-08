# dms_ai_bridge

Intelligent middleware between Document Management Systems (e.g.
[Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)) and AI frontends such as
[Open WebUI](https://github.com/open-webui/open-webui) or
[AnythingLLM](https://github.com/Mintplex-Labs/anything-llm).

The bridge indexes documents from a DMS into a [Qdrant](https://qdrant.tech/) vector database
using [Ollama](https://ollama.com/) embeddings. A separate ingestion pipeline converts and
uploads files from a local inbox into Paperless-ngx. A FastAPI server (Phase III) exposes a
semantic search endpoint and a webhook listener so the index stays in sync whenever a document
is added or updated.

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
FastAPI (POST /query/{frontend})        ← Phase III
  │── UserMappingService.resolve(frontend, user_id, engine) → owner_id
  │── LLMClientInterface.do_embed()
  │── RAGClientInterface.do_search_points()
  └── LLMClientInterface.do_chat()      ← Phase IV
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
| IV | Custom ReAct agent, vector similarity search, LLM synthesis | Complete |

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

### Phase IV — Agentic Logic (complete)

- Custom ReAct agent with multi-step reasoning (no LangChain dependency)
- LLM-based query classification — extracts correspondent, document type, and tag filters
- Result synthesis with LLM summarisation
- SSE streaming via `POST /chat/{frontend}/stream`

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
│   ├── agent/
│   │   ├── AgentService.py              # ReAct loop orchestrator
│   │   └── tools/
│   │       ├── AgentToolInterface.py    # Abstract tool base ABC
│   │       ├── AgentToolManager.py      # Tool registry + descriptions builder
│   │       ├── search_documents/        # AgentToolSearchDocuments
│   │       ├── list_filter_options/     # AgentToolListFilterOptions
│   │       └── get_document_details/    # AgentToolGetDocumentDetails
│   └── rag_search/
│       ├── SearchService.py             # embed → search → list[SearchResult]
│       └── helper/
│           └── IdentityHelper.py        # Resolves frontend user_id → owner_id map
├── config/
│   └── user_mapping.yml                 # frontend/user_id → DMS owner_id mapping
└── server/                              # Phase III/IV
    ├── api_server.py                    # FastAPI entry point with lifespan
    ├── routers/
    │   ├── WebhookRouter.py             # POST /webhook/{engine}/document
    │   ├── QueryRouter.py               # POST /query/{frontend}
    │   └── ChatRouter.py                # POST /chat/{frontend} + /stream (ReAct agent)
    ├── dependencies/
    │   ├── auth.py                      # X-API-Key verification
    │   └── services.py                  # FastAPI Depends helpers
    ├── models/
    │   ├── requests.py                  # WebhookRequest, SearchRequest
    │   └── responses.py                 # SearchResultItem, SearchResponse
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

### `POST /chat/{frontend}`

ReAct agent — returns a synthesised natural language answer.

**Request**
```json
{ "query": "Summarise my invoices from 2024", "user_id": "5" }
```

**Response**
```json
{ "query": "Summarise my invoices from 2024", "answer": "In 2024 you received..." }
```

### `POST /chat/{frontend}/stream`

ReAct agent — streams the answer word-by-word as Server-Sent Events.

**SSE format**
```
data: {"type": "step", "chunk": "🔍 Suche nach Dokumenten..."}
data: {"type": "answer", "chunk": "In "}
data: {"type": "answer", "chunk": "2024 "}
...
data: {"type": "step", "chunk": "✅ Fertig"}
data: [DONE]
```

`user_id` is resolved to an internal `owner_id` via `UserMappingService` before any search
is performed. Unknown users receive HTTP 403 — no fallback default owner.

---

## Integrations

Pre-built connectors are available in the `integrations/` directory.

### OpenWebUI (`integrations/openwebui/pipeline.py`)

A pipeline plugin that connects OpenWebUI to dms_ai_bridge via the `/chat/openwebui` and
`/chat/openwebui/stream` endpoints.

1. Copy `integrations/openwebui/pipeline.py` to your OpenWebUI `pipelines/` directory.
2. Restart OpenWebUI and configure the Valves in Admin Panel → Pipelines:
   - `BASE_URL` — dms_ai_bridge server URL (default: `http://dms-bridge:8000`)
   - `API_KEY` — matches `APP_API_KEY` in your `.env`
   - `USER_ID` — OpenWebUI user ID mapped in `config/user_mapping.yml`

### AnythingLLM (`integrations/anythingllm/dms-bridge-skill/`)

An agent skill that gives AnythingLLM document search capabilities via `/chat/anythingllm`
(falls back to `/query/anythingllm` if the chat endpoint is unavailable).

1. Copy the `integrations/anythingllm/dms-bridge-skill/` folder to the AnythingLLM custom skills directory (contains `handler.js`, `plugin.json`, `package.json`).
2. Enable the "DMS Document Search" skill in Agent Settings and configure:
   - `API_URL` — dms_ai_bridge server URL (default: `http://dms-bridge:8000`)
   - `API_KEY` — matches `APP_API_KEY` in your `.env`
   - `USER_ID` — AnythingLLM user ID mapped in `config/user_mapping.yml`

---

## Security

- Every request must carry the `X-API-Key` header matching `APP_API_KEY`.
- `owner_id` is enforced unconditionally on every Qdrant upsert and every search filter.
- Documents without `owner_id` are skipped at sync time — no silent writes to Qdrant.
- `user_id` from AI frontends is never passed directly to search — always resolved through
  `UserMappingService` first.
