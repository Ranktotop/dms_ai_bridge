# dms_ai_bridge

Intelligent middleware between Document Management Systems (e.g. Paperless-ngx) and AI
frontends (OpenWebUI, AnythingLLM) via semantic search.

---

## Project Goal

Users ask questions in natural language about their documents. The bridge indexes all
documents from a DMS into a vector database and answers search queries through a FastAPI
server. A custom ReAct agent (no LangChain) handles intent classification and result
synthesis.

---

## Architecture

```
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
FastAPI (POST /query/{frontend})
  │── UserMappingService.resolve(frontend, user_id, engine) → owner_id
  │── LLMClientInterface.do_embed()    ← embed query text
  │── RAGClientInterface.do_search_points()   ← filter by owner_id + vector
  └── LLMClientInterface.do_chat()     ← Phase IV synthesis

OpenWebUI / AnythingLLM
  └── POST /query/{frontend}  {"user_id": "5", "query": "..."}
         │  frontend = path param ("openwebui" | "anythingllm" | …)
         ▼
  UserMappingService reads config/user_mapping.yml
         │  resolve("openwebui", "5", "paperless") → owner_id=3
         ▼
  SearchService.do_search(query, owner_id=3, …)
```

Additional DMS backends, RAG backends, and LLM providers can be added without touching
the core pipeline — new implementations satisfy the relevant interface and the factory
picks them up automatically.

---

## Implementation Phases

| Phase | Scope | Status |
|---|---|---|
| I | Shared infrastructure, DMS client, RAG client, SyncService | Complete |
| II | LLM client (embedding via Ollama) | Complete |
| III | FastAPI server — POST /webhook/{engine}/document + POST /query (scroll-based) | Complete |
| IV | Custom ReAct agent, vector similarity search, LLM synthesis | Complete |

---

## User Identity & Mapping

The bridge serves multiple AI frontends (OpenWebUI, AnythingLLM, …). Each frontend has its
own user namespace. DMS backends (Paperless-ngx, …) have their own owner concept. These two
namespaces are independent and must be explicitly bridged.

### Identity layers

| Layer | ID type | Example |
|---|---|---|
| AI frontend | `user_id: str` | `"5"` (OpenWebUI user) |
| DMS backend | `owner_id: int` | `3` (Paperless owner) |

The `user_id` is sent by the frontend in the request body. The `owner_id` is resolved
server-side via `UserMappingService` before any DMS or RAG call is made. Clients never
send or receive `owner_id` — it is an internal implementation detail.

### Mapping configuration (`config/user_mapping.yml`)

```yaml
users:
  "openwebui":
    "5":               # OpenWebUI user_id (string)
      paperless: 3     # Paperless-ngx owner_id (int)
    "7":
      paperless: 8
  "anythingllm":
    "12":
      paperless: 3     # same DMS owner as OpenWebUI user "5"
```

File path is configured via `USER_MAPPING_FILE` env var (default: `config/user_mapping.yml`).
The file is loaded once at startup and held in `app.state`. A restart is required to pick
up changes.

### UserMappingService (`server/user_mapping/UserMappingService.py`)

Key methods:
- `resolve(frontend: str, user_id: str, engine: str) -> int | None` — returns DMS owner_id
- `reverse_resolve(owner_id: int, engine: str) -> list[tuple[str, str]]` — returns all
  `(frontend, user_id)` pairs that map to this owner (used by webhook for cache invalidation)

If `resolve()` returns `None` (no mapping found), the QueryRouter raises HTTP 403 — unmapped
users cannot search.

### Route design

```
POST /query/{frontend}
  path param: frontend  — identifies the calling AI system ("openwebui", "anythingllm", …)
  body:       user_id   — the user's ID within that frontend
              query     — natural language query
              limit     — max results (default 5)
              chat_history — optional prior turns
```

The `{frontend}` path parameter is declared in `QueryRouter` and passed to
`UserMappingService.resolve()` before `SearchService` is called.

### Security note

`owner_id` MUST be resolved from the mapping before any RAG or DMS operation. A missing
or unresolvable mapping is a hard 403 — never fall back to a default owner.

---

## Generic Interfaces

All HTTP clients inherit from `ClientInterface`. The four domain interfaces extend it.

### `ClientInterface` (`shared/clients/ClientInterface.py`)

Base ABC for every HTTP client. Provides:
- `boot()` / `close()` — create and destroy `httpx.AsyncClient`
- `do_request(method, endpoint, **kwargs)` — authenticated HTTP call with timeout
- `do_healthcheck()` — GET to `_get_endpoint_healthcheck()`
- `get_config_val(raw_key)` — builds namespaced env key `{CLIENT_TYPE}_{ENGINE_NAME}_{KEY}`

Every subclass must implement:
`_get_engine_name()`, `_get_base_url()`, `_get_auth_header()`,
`_get_endpoint_healthcheck()`, `_get_required_config()`

---

### `DMSClientInterface` (`shared/clients/dms/DMSClientInterface.py`)

ABC for all Document Management System backends.

Key methods:
- `fill_cache()` — paginated fetch of all documents and metadata; builds
  `DocumentHighDetails` objects with fully resolved names
- `get_enriched_documents() -> list[DocumentHighDetails]`
- `get_documents()`, `get_correspondents()`, `get_tags()`, `get_owners()`,
  `get_document_types()` — cache accessors

`DocumentHighDetails` (canonical output model):
```
engine, id
correspondent_id, document_type_id, tag_ids, owner_id   ← raw IDs
correspondent, document_type, tags, owner                ← resolved names
title, content, created_date, mime_type, file_name
```

Current implementation: `DMSClientPaperless`
Factory: `DMSClientManager` — reads `DMS_ENGINES` from env

---

### `RAGClientInterface` (`shared/clients/rag/RAGClientInterface.py`)

ABC for all vector database backends.

Key methods:
- `do_upsert_points(points: list[PointUpsert]) -> bool` — insert/replace with deterministic UUIDs
- `do_fetch_points(filters, include_fields, with_vector) -> list[PointHighDetails]`
- `do_search_points(vector, filters, include_fields, with_vector) -> list[PointHighDetails]`
- `do_count(filters: list[dict]) -> int`
- `do_delete_points_by_filter(filter: dict) -> bool`
- `do_existence_check() -> bool`
- `do_create_collection(vector_size: int, distance: str) -> httpx.Response`

Abstract parser hooks every backend must implement:
- `_parse_endpoint_points(response, requested_page_size, total_points, current_page) -> PointsListResponse`
- `_parse_endpoint_points_search(response) -> list[PointHighDetails]`
- `_parse_endpoint_points_count(response) -> int`
- `_parse_endpoint_points_upsert(response) -> bool`
- `_parse_endpoint_points_delete(response) -> bool`

`Point model hierarchy` (`shared/clients/rag/models/Point.py`):

Request models (for upserts):
```
PointDetailsRequest          — dms_doc_id, dms_engine, content_hash
PointHighDetailsRequest      — extends PointDetailsRequest; adds chunk_index, title, owner_id: str*
PointUpsert                  — id: str, vector: list[float], payload: PointHighDetailsRequest
```

Response models (returned from queries):
```
PointBase                    — engine, id
PointDetails(PointBase)      — metadata without owner_id (for search results)
PointHighDetails(PointDetails) — full metadata incl. owner_id: str* (MANDATORY), chunk_index
PointsListResponse           — points, currentPage, nextPage, nextPageId, overallCount
PointsSearchResponse         — query, points, total
```
`*` owner_id type is `str` — never `int`. Raise `ValueError` on upsert if `owner_id` is None.

Point IDs are deterministic:
`uuid.uuid5(uuid.NAMESPACE_OID, f"{engine}:{doc_id}:{chunk_index}")`

Current implementation: `RAGClientQdrant`
Factory: `RAGClientManager` — reads `RAG_ENGINES` from env

---

### `LLMClientInterface` (`shared/clients/llm/LLMClientInterface.py`)

Unified ABC for inference backends — covers both embedding and chat/completion because
providers like Ollama support both natively.

Embedding methods (concrete):
- `do_embed(texts: str | list[str]) -> list[list[float]]`
- `do_fetch_embedding_vector_size() -> tuple[int, str]` — (dimension, distance_metric)
- `do_fetch_models() -> httpx.Response`

Chat/completion methods (concrete):
- `do_chat(messages: list[dict]) -> str` — returns assistant reply text

Abstract hooks subclasses must implement:
```
# embedding
get_embed_payload(texts)           extract_embeddings_from_response(response)
extract_vector_size_from_model_info(model_info)
get_endpoint_embedding()           get_endpoint_model_details()
_get_endpoint_models()

# chat
get_chat_payload(messages)         extract_chat_response(response)
_get_endpoint_chat()
```

Instance attributes (set in `__init__`):
- `self.embed_model` — reads `LLM_MODEL_EMBEDDING`
- `self.embed_distance` — reads `LLM_DISTANCE` (default: `Cosine`)
- `self.embed_model_max_chars` — reads `LLM_MODEL_EMBEDDING_MAX_CHARS`
- `self.chat_model` — reads `LLM_MODEL_CHAT` (optional; falls back to `embed_model`)

Current implementation: `LLMClientOllama`
Factory: `LLMClientManager` — reads `LLM_ENGINE` from env

---

### `CacheClientInterface` (`shared/clients/cache/CacheClientInterface.py`)

ABC for all cache backends. Used for cross-process caching between the standalone
SyncService process and the API server.

Key methods:
- `do_get(key: str) -> str | None` — retrieve; None on miss
- `do_set(key: str, value: str, ttl_seconds: int | None = None) -> None`
- `do_delete(key: str) -> None`
- `do_delete_pattern(pattern: str) -> None` — glob-based bulk deletion
- `do_exists(key: str) -> bool`
- `do_get_json(key: str) -> dict | list | None` — convenience wrapper
- `do_set_json(key: str, value: dict | list, ttl_seconds: int | None = None) -> None`

Key schema constants (defined in interface):
```
KEY_FILTER_OPTIONS = "filter_options"   → "filter_options:{owner_id}"
```

Current implementation: `CacheClientRedis`
Factory: `CacheClientManager` — reads `CACHE_ENGINE` from env

---

### `OCRClientInterface` (`shared/clients/ocr/OCRClientInterface.py`)

ABC for all OCR backends. Used by the document ingestion pipeline to convert files to
Markdown text via an external OCR service.

Key method (concrete):
- `do_convert_pdf_to_markdown(file_bytes: bytes, filename: str) -> str`
  — uploads file bytes, returns extracted Markdown; raises `RuntimeError` on failure

Instance attributes:
- `self.timeout` — reads `OCR_TIMEOUT` (default: `300` s)

Abstract hooks subclasses must implement:
```
_get_engine_name()
_get_base_url()
_get_auth_header()
_get_endpoint_healthcheck()
_get_endpoint_convert_pdf_to_markdown()
_get_required_config()
_get_convert_pdf_to_markdown_payload(file_bytes, filename)   → multipart files arg
_parse_convert_file_response(response)                       → Markdown str
```

Current implementation: `OCRClientDocling`
Factory: `OCRClientManager` — reads `OCR_ENGINE` from env

---

## Directory Structure

```
dms_ai_bridge/
├── .claude/
│   ├── agents/                          ← agent definitions (see Agent Responsibilities)
│   └── settings.json
├── CLAUDE.md                            ← this file
├── .env / .env.example
├── requirements.txt
├── start.sh
├── shared/
│   ├── clients/
│   │   ├── ClientInterface.py           ← base ABC (lifecycle, auth, do_request)
│   │   ├── dms/
│   │   │   ├── DMSClientInterface.py    ← DMS ABC
│   │   │   ├── DMSClientManager.py      ← factory (reflection-based)
│   │   │   ├── models/
│   │   │   │   ├── Document.py          ← DocumentBase/Details/HighDetails
│   │   │   │   ├── Correspondent.py
│   │   │   │   ├── Tag.py
│   │   │   │   ├── Owner.py
│   │   │   │   └── DocumentType.py
│   │   │   └── paperless/
│   │   │       ├── DMSClientPaperless.py
│   │   │       └── models.py
│   │   ├── llm/
│   │   │   ├── LLMClientInterface.py    ← unified ABC (embed + chat)
│   │   │   ├── LLMClientManager.py      ← factory (reflection-based)
│   │   │   └── ollama/
│   │   │       └── LLMClientOllama.py
│   │   ├── cache/
│   │   │   ├── CacheClientInterface.py  ← cache ABC (get/set/delete/delete_pattern)
│   │   │   ├── CacheClientManager.py    ← factory (reflection-based)
│   │   │   └── redis/
│   │   │       └── CacheClientRedis.py
│   │   ├── rag/
│   │   │   ├── RAGClientInterface.py    ← RAG ABC
│   │   │   ├── RAGClientManager.py      ← factory (reflection-based)
│   │   │   ├── models/
│   │   │   │   └── Point.py             ← all RAG point models (request + response, owner_id mandatory)
│   │   │   └── qdrant/
│   │   │       └── RAGClientQdrant.py
│   │   └── ocr/
│   │       ├── OCRClientInterface.py    ← OCR ABC (do_convert_pdf_to_markdown)
│   │       ├── OCRClientManager.py      ← factory (reflection-based)
│   │       └── docling/
│   │           └── OCRClientDocling.py
│   ├── helper/
│   │   ├── HelperConfig.py              ← central env var reader
│   │   └── HelperFile.py               ← file system helpers
│   ├── logging/
│   │   └── logging_setup.py             ← setup_logging(), ColorLogger
│   └── models/
│       └── config.py                    ← EnvConfig Pydantic model
├── services/
│   ├── doc_ingestion/
│   │   ├── IngestionService.py          ← orchestrator (boot Document → upload → PATCH)
│   │   ├── doc_ingestion.py             ← entry point (python -m services.doc_ingestion)
│   │   └── helper/
│   │       ├── Document.py              ← central document class (convert, OCR, metadata, tags)
│   │       ├── DocumentConverter.py     ← LibreOffice PDF conversion helper
│   │       └── FileScanner.py           ← rglob + watchfiles file discovery
│   ├── dms_rag_sync/
│   │   ├── SyncService.py               ← DMS → embed → RAG orchestration
│   │   └── dms_rag_sync.py              ← entry point (python -m services.dms_rag_sync)
│   ├── agent/
│   │   ├── AgentService.py              ← ReAct loop orchestrator
│   │   └── tools/
│   │       ├── AgentToolInterface.py    ← abstract tool base ABC
│   │       ├── AgentToolManager.py      ← tool registry + descriptions builder
│   │       ├── search_documents/        ← AgentToolSearchDocuments
│   │       ├── list_filter_options/     ← AgentToolListFilterOptions
│   │       └── get_document_details/    ← AgentToolGetDocumentDetails
│   └── rag_search/
│       ├── SearchService.py             ← embed → scroll → list[SearchResult] (no FastAPI)
│       └── helper/
│           └── IdentityHelper.py        ← resolves frontend user_id → owner_id map
├── config/
│   └── user_mapping.yml                 ← frontend/user_id → DMS owner_id mapping
└── server/
    ├── api_server.py                    ← FastAPI entry point with lifespan
    ├── routers/
    │   ├── HealthRouter.py              ← GET /health, GET /health/deep (no auth)
    │   ├── WebhookRouter.py             ← POST /webhook/{engine}/document
    │   ├── QueryRouter.py               ← POST /query/{frontend} (thin adapter → SearchService)
    │   └── ChatRouter.py                ← POST /chat/{frontend} + /stream (ReAct agent)
    ├── dependencies/
    │   ├── auth.py                      ← X-API-Key verification
    │   └── services.py                  ← FastAPI Depends helpers (get_search_service, …)
    ├── models/
    │   ├── requests.py                  ← WebhookRequest, SearchRequest (user_id, not owner_id)
    │   └── responses.py                 ← SearchResultItem, SearchResponse
    └── user_mapping/
        ├── UserMappingService.py        ← resolve(frontend, user_id, engine) → owner_id
        └── models.py                    ← UserMapping Pydantic models
```

---

## Agent Responsibilities

Nine specialised agents own distinct subsystems. Invoke the correct agent for any task
touching that subsystem. Agents that own interfaces must coordinate before changing
public method signatures.

### `infra-agent` — Shared Infrastructure
**Model:** `claude-opus-4-6`

**Owns:**
- `shared/helper/HelperConfig.py` — central env var reader
- `shared/logging/logging_setup.py` — `setup_logging()`, `ColorLogger`, `CustomFormatter`
- `shared/models/config.py` — `EnvConfig` Pydantic model
- `shared/clients/ClientInterface.py` — base ABC for all HTTP clients
- `.docker/Dockerfile`, `.docker/docker-compose.yml`
- `requirements.txt`, `start.sh`

**Invoke when:**
changing `HelperConfig` public API, adding logging features, modifying the base HTTP
client lifecycle (`boot`/`close`/`do_request`), updating Python dependencies, or
adjusting Docker configuration.

**Critical:** all other agents depend on this agent's outputs. Treat every change as
potentially breaking for the whole team. Never remove or rename public methods on
`HelperConfig` or `ClientInterface` without updating all subclasses in the same commit.

---

### `dms-agent` — DMS Client Subsystem
**Model:** `claude-sonnet-4-6`

**Owns:**
- `shared/clients/dms/DMSClientInterface.py`
- `shared/clients/dms/DMSClientManager.py`
- `shared/clients/dms/models/` (Document, Correspondent, Tag, Owner, DocumentType)
- `shared/clients/dms/paperless/DMSClientPaperless.py`
- `shared/clients/dms/paperless/models.py`

**Invoke when:**
adding a new DMS backend, modifying how documents are fetched or cached, changing
DMS data models, debugging Paperless-ngx API issues, or adding new metadata fields
to `DocumentHighDetails`.

**Key rules:**
- `fill_cache()` must resolve ALL foreign keys before building `DocumentHighDetails` —
  never leave names as `None` when an ID is set
- Never trigger OCR — only read the `content` field Paperless already provides
- If a DMS endpoint is unavailable during cache fill, log WARNING and continue; do not abort
- Model hierarchy `DocumentBase → DocumentDetails → DocumentHighDetails` must not be flattened
- Adding `DocumentHighDetails` fields requires coordination with sync-agent and rag-agent

---

### `rag-agent` — RAG / Vector DB Subsystem
**Model:** `claude-sonnet-4-6`

**Owns:**
- `shared/clients/rag/RAGClientInterface.py`
- `shared/clients/rag/RAGClientManager.py`
- `shared/clients/rag/models/Point.py`
- `shared/clients/rag/qdrant/RAGClientQdrant.py`

**Invoke when:**
adding a new vector DB backend, modifying upsert or search behaviour, changing the
`Point model hierarchy`, debugging Qdrant issues, or adding new filter capabilities
to `do_fetch_points()` or `do_search_points()`.

**Key rules:**
- **Security invariant (non-negotiable):** every upsert must have `owner_id` set; every
  user-facing search must filter by `owner_id`. Raise `ValueError` on upsert if `owner_id`
  is `None`.
- Point IDs must be deterministic (`uuid5`) — never use random UUIDs
- `Point.py` model field names are a stable contract — coordinate with sync-agent and
  api-agent before any field rename

---

### `cache-agent` — Cache Client Subsystem
**Model:** `claude-sonnet-4-6`

**Owns:**
- `shared/clients/cache/CacheClientInterface.py`
- `shared/clients/cache/CacheClientManager.py`
- `shared/clients/cache/redis/CacheClientRedis.py`

**Invoke when:**
adding a new cache backend, modifying how filter options are stored or invalidated,
changing cache key schemas, debugging Redis connectivity issues, or adding new
cacheable data types.

**Key rules:**
- Cache keys must use the constants defined in `CacheClientInterface` — never hardcode
  key strings in callers
- `do_get_json` / `do_set_json` are the preferred API for structured data
- TTL is always optional; a 24 h safety net is configured via `CACHE_DEFAULT_TTL_SECONDS`
- `do_delete_pattern("filter_options:*")` must be called by SyncService after full sync
- `CacheClientRedis` overrides `boot()` / `close()` to manage a `redis.asyncio.Redis`
  connection — it does NOT use `httpx.AsyncClient`
- Key schema changes require coordination with service-agent (reader and invalidator)

---

### `embed-llm-agent` — LLM / Embedding Client Subsystem
**Model:** `claude-sonnet-4-6`

**Owns:**
- `shared/clients/llm/LLMClientInterface.py`
- `shared/clients/llm/LLMClientManager.py`
- `shared/clients/llm/ollama/LLMClientOllama.py`

**Invoke when:**
adding a new LLM/embedding provider, changing how texts are embedded or chat messages
are sent, debugging embedding or chat API responses, adjusting model configuration
(distance metric, vector size discovery, chat model), or implementing a new provider.

**Key rules:**
- `do_embed()` always returns `list[list[float]]` — even for a single string; callers
  access `result[0]` for the first vector
- Batch splitting is the caller's responsibility — `do_embed()` sends the full list as
  one request
- `do_chat()` messages use the OpenAI format (`role`/`content` dicts)
- `get_chat_payload()` implementations must set `"stream": False`
- Changing `do_fetch_embedding_vector_size()` tuple order: notify service-agent and api-agent
- Changing `do_chat()` return type: notify service-agent and api-agent

**Adding a new provider:**
1. Create `shared/clients/llm/{engine_lower}/LLMClient{Engine}.py`
2. Inherit `LLMClientInterface`, implement all abstract hooks (both embedding and chat)
3. Set `LLM_ENGINE={Engine}` in env — factory loads via reflection

---

### `service-agent` — Services Layer
**Model:** `claude-sonnet-4-6`

**Owns:**
- `services/dms_rag_sync/SyncService.py`
- `services/dms_rag_sync/dms_rag_sync.py`
- `services/rag_search/SearchService.py`

**Invoke when:**
changing chunking strategy or constants, tuning batch sizes or concurrency, fixing
sync bugs, modifying `do_incremental_sync()`, adjusting orphan cleanup logic, or
changing search/ranking logic in `SearchService`.

**Key rules:**
- `do_incremental_sync(document_id: int, engine: str) -> None` is the public API contract
  with api-agent's `WebhookRouter` — never change this signature without notifying api-agent
- `do_search(query: str, identity_helper: IdentityHelper, chat_history: list[dict] | None, limit: int) -> list[PointHighDetails]`
  is the public API contract with api-agent's `QueryRouter` — never change this signature
  without notifying api-agent
- `SearchService` calls `CacheClientInterface` for filter option lookup and
  `SyncService` calls it for invalidation — cache key contract is owned by cache-agent
- No FastAPI, Starlette, or Pydantic response models in any service — keep them
  framework-agnostic and reusable outside the server context
- Skip documents without `owner_id` (security gate — no silent writes)
- Chunking: character-level only (`CHUNK_SIZE=1000`, `CHUNK_OVERLAP=100`), no tokenisation
- Concurrency: always `asyncio.Semaphore(DOC_CONCURRENCY=5)` — never unbounded `gather()`
- Upsert in batches of `UPSERT_BATCH_SIZE=100` — Qdrant rejects oversized payloads
- After full sync: scroll RAG for all `dms_engine` vectors, delete any whose `dms_doc_id`
  is absent from the current DMS document set (orphan cleanup)

---

### `agent-agent` — ReAct Agent Subsystem
**Model:** `claude-sonnet-4-6`

**Owns:**
- `services/agent/AgentService.py` — ReAct loop orchestrator
- `services/agent/tools/AgentToolResult.py` — `CitationRef` + `AgentToolResult` dataclasses
- `services/agent/tools/AgentToolInterface.py` — abstract tool base ABC
- `services/agent/tools/AgentToolManager.py` — tool registry + descriptions builder
- `services/agent/tools/search_documents/AgentToolSearchDocuments.py`
- `services/agent/tools/list_filter_options/AgentToolListFilterOptions.py`
- `services/agent/tools/get_document_details/AgentToolGetDocumentDetails.py`
- `services/agent/tools/get_document_full/AgentToolGetDocumentFull.py`

**Invoke when:**
modifying the ReAct loop, adding or changing a tool, changing the step_callback
mechanism, updating the system prompt, or adjusting how tool errors are handled.

**Key rules:**
- No FastAPI imports in `services/agent/` — keep the subsystem framework-agnostic
- System prompt only via `_get_system_prompt()` getter — never as a module-level constant
- `step_callback` is optional — always guard with `if step_callback:` before calling
- Tool errors: log the exception, return `AgentToolResult(observation="...", citations=[])` —
  never re-raise out of `do_execute()`, never return plain `str`
- `do_execute()` return type is always `AgentToolResult` — never `str`
- New tool: create `services/agent/tools/{tool_name}/AgentTool{Name}.py`, inherit
  `AgentToolInterface`, implement `do_execute()` and `get_step_hint()`, register in
  `AgentToolManager`

**Key contracts:**
- `AgentResponse` has fields: `answer: str`, `tool_calls: list[str]`, `citations: list[CitationRef]`
- `AgentService.do_run(query, chat_history, max_iterations, step_callback, identity_helper, client_settings) -> AgentResponse`
  — consumed by api-agent's `ChatRouter`; never change this signature without notifying api-agent
- Tools read `SearchService.do_search()` and `SearchService.do_fetch_by_doc_id()` —
  coordinate signature changes with service-agent

**Strict boundary — api-agent must NEVER edit files under `services/agent/`.**

---

### `ingestion-agent` — Document Ingestion Pipeline
**Model:** `claude-sonnet-4-6`

**Owns:**
- `services/doc_ingestion/IngestionService.py` — orchestrator
- `services/doc_ingestion/doc_ingestion.py` — entry point
- `services/doc_ingestion/helper/Document.py` — central document class
- `services/doc_ingestion/helper/DocumentConverter.py` — LibreOffice PDF conversion
- `services/doc_ingestion/helper/FileScanner.py` — file discovery
- Abstract write methods in `shared/clients/dms/DMSClientInterface.py`
  (`do_upload_document`, `do_update_document`, `do_resolve_or_create_*`)
- Paperless implementations of those write methods in `DMSClientPaperless.py`

**Invoke when:**
modifying the ingestion pipeline, changing OCR or text extraction strategy, updating
path template parsing, debugging document upload or metadata update issues, or adding
new DMS write capabilities.

**Key classes:**

`Document` (`services/doc_ingestion/helper/Document.py`):
- Central class representing a file to be ingested
- Lifecycle: `boot()` (converts file, extracts text, reads metadata & tags) / `cleanup()`
- Path template parsing: `_read_meta_from_path()` — positional `{correspondent}`,
  `{document_type}`, `{year}`, `{month}`, `{day}`, `{title}` segments; `correspondent`
  is mandatory
- OCR strategy: direct read for `txt`/`md`; PyMuPDF per-page text → Vision LLM fallback
  for any page below `minimum_text_chars = 40`
- LLM metadata: `_read_meta_from_content()` — JSON response with correspondent, document_type,
  year, month, day, title; path metadata takes precedence (path wins over LLM)
- LLM tags: `_read_tags_from_content()` — returns `list[str]`, max 3 tags
- DMS cache context: `_get_dms_cache()` feeds existing document_types and tag names into
  prompts so the LLM prefers existing values
- Public getters: `get_title()`, `get_metadata() -> DocMetadata`, `get_tags() -> list[str]`,
  `get_content() -> str`, `get_date_string(pattern) -> str | None`

`DocMetadata` (dataclass in `Document.py`):
```
correspondent, document_type, year, month, day, title, filename
```

`DocumentConverter` (`services/doc_ingestion/helper/DocumentConverter.py`):
- Wraps LibreOffice (`soffice`) to convert office formats to PDF
- Native formats (pdf, png, jpg, jpeg, txt, md): copied to working directory unchanged
- Convertible formats (docx, doc, odt, xlsx, xls, ods, csv, pptx, ppt, odp, rtf): run
  LibreOffice headless, output moved to working directory
- Lifecycle: `boot()` / `cleanup()` / `is_booted()`
- Raises `RuntimeError` if LibreOffice is not in PATH

`IngestionService` (`services/doc_ingestion/IngestionService.py`):
- `do_ingest_file(file_path, root_path) -> int | None` — full pipeline:
  1. `fill_cache()` on DMS client
  2. Instantiate and `boot()` a `Document`
  3. Call `get_metadata()`, `get_tags()`, `get_title()`, `get_content()`, `get_date_string()`
  4. Resolve/create correspondent, document_type, tags via DMS write methods
  5. Upload original file bytes via `do_upload_document()`
  6. PATCH document with full metadata via `do_update_document()`
  7. `cleanup()` the Document in a `finally` block

**Key rules:**
- `Document.boot()` raises on failure — wrap in try/except in `IngestionService`
- `Document.cleanup()` MUST be called in a `finally` block — never skip
- Path metadata takes precedence over LLM metadata (path wins)
- `correspondent` is mandatory in path metadata — raise `RuntimeError` if absent
- Vision LLM (`LLM_MODEL_VISION`) is required; raise `RuntimeError` at boot if not configured
- `DocumentConverter` requires LibreOffice — raise `RuntimeError` if not found in PATH
- Working directories use UUID-based names to avoid collisions across concurrent ingestions
- Language for all LLM-extracted text is configured via `LANGUAGE` env var (default: `German`)

**Coordination:**
- `do_chat_vision()` on `LLMClientInterface` must be implemented by embed-llm-agent
- DMS write methods must be implemented by dms-agent
- `OCRClientInterface` is owned by ocr-agent — do not modify it here

---

### `ocr-agent` — OCR Client Subsystem
**Model:** `claude-sonnet-4-6`

**Owns:**
- `shared/clients/ocr/OCRClientInterface.py`
- `shared/clients/ocr/OCRClientManager.py`
- `shared/clients/ocr/docling/OCRClientDocling.py`

**Invoke when:**
adding a new OCR backend, changing how documents are converted to Markdown, debugging
OCR service responses, adjusting Docling conversion parameters (formats, OCR flags,
pdf backend), or adding new conversion capabilities to `OCRClientInterface`.

**Key rules:**
- `do_convert_pdf_to_markdown()` must never return an empty string — raise `RuntimeError`
  if the service returns empty or failed content
- Multipart payloads use the `files` argument to `do_request` — never `data` — to
  produce an AsyncByteStream-compatible MultipartStream for httpx
- Default timeout is `300` s (`OCR_TIMEOUT`) — OCR is inherently slow; do not lower it
  without confirming with ingestion-agent
- `OCR_ENGINE` controls which backend is loaded — factory loads via reflection

---

### `api-agent` — FastAPI Server (Phase III/IV)
**Model:** `claude-opus-4-6`

**Owns:**
- `server/api_server.py` — FastAPI entry point with lifespan
- `server/routers/HealthRouter.py` — `GET /health`, `GET /health/deep`
- `server/routers/WebhookRouter.py` — `POST /webhook/{engine}/document`
- `server/routers/QueryRouter.py` — `POST /query/{frontend}` (thin adapter over `SearchService`)
- `server/routers/ChatRouter.py` — `POST /chat/{frontend}` + `POST /chat/{frontend}/stream`
- `server/dependencies/auth.py` — `X-API-Key` verification
- `server/dependencies/services.py` — FastAPI `Depends` helpers
- `server/models/requests.py` — `WebhookRequest`, `SearchRequest`
- `server/models/responses.py` — `SearchResultItem`, `SearchResponse`
- `server/user_mapping/UserMappingService.py` — loads `config/user_mapping.yml`, resolves user identities
- `server/user_mapping/models.py` — `UserMapping` Pydantic models

**Invoke when:**
creating the FastAPI app, adding routes, wiring `SearchService` into `QueryRouter`,
implementing `UserMappingService`, or implementing auth middleware.

**Key rules:**
- All route handlers: `async def`
- Clients and services accessed only via `request.app.state.*` — never instantiate in handlers
- `user_id` (external frontend ID) comes from the request body; `owner_id` (DMS-internal) is
  resolved via `UserMappingService.resolve()` in the router — never pass raw `user_id` to
  `SearchService` or any RAG/DMS call
- Missing mapping → HTTP 403, never fall back to a default owner
- Use `BackgroundTasks` for the webhook — never `asyncio.create_task()` in route handlers
- `QueryRouter` is a thin adapter: resolve user_id → owner_id → call `SearchService.do_search()` →
  map `list[SearchResult]` to `SearchResponse` — no embed, scroll, or LLM logic in the router
- Every subdirectory under `server/` needs `__init__.py` for uvicorn module resolution
- Phase IV is complete — `ChatRouter` is a thin adapter over `AgentService` (owned by agent-agent)

**API contracts:**
```
POST /webhook/{engine}/document
  Request:  {"document_id": 42}
  Response: {"status": "accepted", "document_id": 42}
  Action:   background_tasks.add_task(sync_service.do_incremental_sync, document_id, engine)

POST /query/{frontend}
  Path:     frontend — AI system identifier ("openwebui", "anythingllm", …)
  Request:  {"query": "...", "user_id": "5", "limit": 5, "chat_history": [...]}
  Response: {"query": "...", "results": [...], "total": N}
  Mapping:  UserMappingService.resolve(frontend, user_id, engine) → owner_id (or 403)
```

---

## Coding Conventions

### Interface-first
New backends are ALWAYS created by implementing the relevant interface. Never create
direct dependencies between concrete implementations.

### Constructors
```python
def __init__(self, helper_config: HelperConfig) -> None:
    super().__init__(helper_config)        # required for ClientInterface subclasses
    self.logging = helper_config.get_logger()
```

### Method prefixes
| Prefix | Meaning |
|---|---|
| `do_*` | async action with side effects (I/O, state change) |
| `get_*` | pure getter — no I/O, no side effects |
| `is_*` | boolean check |
| `_read_*` | private reader |

### Class section banners (in this order)
```python
##########################################
############# LIFECYCLE ##################
##########################################

##########################################
############# CHECKER ####################
##########################################

##########################################
############## GETTER ####################
##########################################

##########################################
############# REQUESTS ###################
##########################################

##########################################
############### CORE #####################
##########################################

##########################################
############# HELPERS ####################
##########################################
```

### Logging — always %-style, never f-strings
```python
# correct
self.logging.info("Syncing document %d ('%s'): %d chunk(s)", doc_id, title, n)

# WRONG
self.logging.info(f"Syncing document {doc_id}")
```

### Type annotations
PEP 604 union syntax: `str | None` — never `Optional[str]`

### Configuration keys
Pattern: `{CLIENT_TYPE}_{ENGINE_NAME}_{SETTING}`

```
DMS_PAPERLESS_BASE_URL     DMS_PAPERLESS_API_KEY
RAG_QDRANT_BASE_URL        RAG_QDRANT_COLLECTION
LLM_OLLAMA_BASE_URL        LLM_OLLAMA_API_KEY
LLM_MODEL_EMBEDDING        LLM_MODEL_CHAT         LLM_DISTANCE
CACHE_REDIS_BASE_URL       CACHE_REDIS_PASSWORD   CACHE_REDIS_DB
CACHE_DEFAULT_TTL_SECONDS  LLM_MAX_FILTER_VALUES_PER_CATEGORY
OCR_ENGINE                 OCR_TIMEOUT                        OCR_DOCLING_BASE_URL       OCR_DOCLING_API_KEY
USER_MAPPING_FILE          (path to user_mapping.yml, default: config/user_mapping.yml)
```

Never call `os.getenv()` directly in business logic — always use `HelperConfig`.

### Async
No `requests`, no synchronous I/O. Exclusively `httpx.AsyncClient` for all HTTP calls.

### Security invariant
`owner_id` MUST be present in every Qdrant upsert payload and every search filter.
Documents without `owner_id` are skipped at sync time — no silent writes.

### Language
All code, variable names, comments, docstrings, and log messages: **English**.

### Style reference — always read an existing file of the same type first
Before creating any new file, read the nearest existing implementation of the same
type and match its structure exactly:
- New service → any existing service in `services/` as reference
- New client  → any existing client in `shared/clients/` as reference
- New router  → any existing router in `server/routers/` as reference

### Prompts / strings / constants — always via getters, never module-level
Never define prompts, system messages, or multi-line strings as module-level
variables or constants. Always expose them via getter methods:

```python
# correct
def _get_system_prompt(self) -> str:
    return "You are a ..."

# WRONG
SYSTEM_PROMPT = "You are a ..."
```

### Single Responsibility — helper classes, not fat classes
- One class does one thing
- Extract sub-functionality into dedicated helper classes
- Never accumulate unrelated methods in one class

### Naming
- Services: `*Service.py` (e.g. `AgentService`, not `ReActAgent`)
- No deep structural nesting — prefer `services/agent` over `services/rag_search/agent`

---

## Agent Selection Guide

| Trigger | Agent |
|---|---|
| `HelperConfig`, `ClientInterface`, logging, Docker, `requirements.txt` | `infra-agent` |
| Paperless API, `DMSClientInterface`, `DocumentHighDetails`, DMS models | `dms-agent` |
| Qdrant, `RAGClientInterface`, `Point.py` models, point filters | `rag-agent` |
| Redis, `CacheClientInterface`, filter option cache, cache invalidation | `cache-agent` |
| Ollama, `LLMClientInterface`, embedding, chat, new LLM provider | `embed-llm-agent` |
| Sync pipeline, `SyncService`, `SearchService`, orphan cleanup | `service-agent` |
| ReAct loop, `AgentService`, agent tools, `step_callback`, system prompt | `agent-agent` |
| File ingestion, `Document`, `DocumentConverter`, `IngestionService` | `ingestion-agent` |
| `OCRClientInterface`, `OCRClientDocling`, `OCR_ENGINE`, Docling backend | `ocr-agent` |
| FastAPI routes, `QueryRouter`, webhook, auth, Phase III/IV server | `api-agent` |
| `UserMappingService`, `user_mapping.yml`, frontend/user_id resolution | `api-agent` |

**Cross-cutting changes** (e.g. adding a field to `Point.py` models, changing a `ClientInterface`
abstract method) require coordinating the relevant agents before merging — see each agent's
"Coordination points" section in `.claude/agents/`.
