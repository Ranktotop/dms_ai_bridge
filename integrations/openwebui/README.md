# OpenWebUI Integration

Connects OpenWebUI to dms_ai_bridge for document-aware chat via a **Tool Function**.
OpenWebUI's own LLM manages the conversation; the Bridge handles document search and
ReAct reasoning as a backend service.

---

## Tool Function (`tool.py`)

The Tool Function lets OpenWebUI's own (strong) LLM manage the conversation while the
Bridge handles document search and ReAct reasoning as a backend service.

### How it works

```
OpenWebUI LLM  ──►  search_documents(query)
                         │
                         ├── Status updates (ReAct steps) appear in the UI
                         │
                         ├── POST /chat/openwebui/stream  (SSE, ReAct agent)
                         │
                         └── Fallback: POST /query/openwebui  (semantic search)
```

### Installation

1. Go to **Admin Panel → Functions → New Function**
2. Set type to **Tool**
3. Paste the contents of `tool.py`
4. Save and configure the Valves

### Valves (Configuration)

| Valve | Default | Description |
|-------|---------|-------------|
| BASE_URL | `http://dms-bridge:8000` | dms_ai_bridge server URL |
| API_KEY | `` | X-Api-Key header value |
| USER_ID | `5` | OpenWebUI user ID (must be in config/user_mapping.yml) |
| LIMIT | `5` | Max search results passed to agent |
| TIMEOUT | `120.0` | HTTP timeout in seconds |

### Attaching the Tool to a Model

1. Go to **Admin Panel → Models** (or Workspace → Models)
2. Edit the model you want to use (e.g. llama3, mistral, …)
3. Under **Tools**, enable **DMS AI Bridge**
4. Save

### Usage

Start a chat with the model. The LLM decides when to call `search_documents()`.
Ask questions like "Do you have any invoices from last year?" or "Find contracts with
Acme Corp." — the LLM will invoke the tool and present the Bridge's answer.

Status updates (ReAct reasoning steps) appear as status indicators during processing.

